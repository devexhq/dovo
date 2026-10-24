"""Worktree lifecycle management service."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

from dovo.common.constants import DEFAULT_MAXIMUM_WORKTREES_ALLOWED
from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import WorkspacePaths
from dovo.common.lock import WorkspaceLock
from dovo.common.session_id import new_session_id
from dovo.core.config import Config
from dovo.core.config.models import WorktreeConfig
from dovo.core.db import WorktreeRecord, WorktreesRepository, WorktreeStatus
from dovo.core.git.exceptions import (
    GitCommandError,
    GitNotFoundError,
    GitPlumbingTimeoutError,
)
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.exceptions import WorktreeError
from dovo.core.worktree.models import (
    WorktreeCreateResult,
    WorktreeCreateStatus,
    WorktreeSession,
)
from dovo.core.worktree.services.failure_log import logged_failure, record_fallback
from dovo.core.worktree.services.wip import apply_wip_to_worktree


def _clean_opt_str(val: str | None) -> str | None:
    """Trim whitespace from string, returning None if empty or None."""
    if val is None:
        return None
    s = val.strip()
    return s if s else None


def _extract_target_metadata(target: WorktreeSession | WorktreeRecord) -> tuple[Path, str, str]:
    """Extract worktree path, session id, and branch name from a session or record."""
    if isinstance(target, WorktreeRecord):
        return Path(target.worktree_path), target.id, target.branch_name
    return target.worktree_path, target.session_id, target.target_branch


def _wip_failure_cause(exc: Exception) -> Exception:
    """Return the OSError a WIP-copy WorktreeError wraps, so logged_failure records it; else exc."""
    if isinstance(exc, WorktreeError) and isinstance(exc.__cause__, OSError):
        return exc.__cause__
    return exc


def _create_context(worktree_path: Path, temp_branch: str) -> dict[str, str | None]:
    """Return the diagnostic context of a create step: worktree ID, branch and path."""
    return {"worktree_id": worktree_path.name, "branch": temp_branch, "path": str(worktree_path)}


def _already_exists(worktree_id: str, fix: str) -> WorktreeCreateResult:
    """Build the already_exists result for a worktree ID whose directory or branch is taken."""
    return WorktreeCreateResult(
        status=WorktreeCreateStatus.ALREADY_EXISTS,
        error_code=ErrorCode.WORKTREE_PATH_CONFLICT,
        errors=[f"Worktree '{worktree_id}' already exists (WORKTREE_PATH_CONFLICT)."],
        fixes=[fix],
    )


class WorktreeLifecycle:
    """Orchestrates worktree creation, validation, cleanup, and pruning."""

    def __init__(
        self,
        paths: WorkspacePaths,
        db: WorktreesRepository,
        *,
        git: GitRunner,
    ) -> None:
        """Initialize lifecycle manager.

        Args:
            paths: Resolved command-invocation workspace paths.
            db: Explicit WorktreesRepository instance.
            git: Runner for every git command.
        """
        self.paths = paths
        self.path = paths.root_dir
        self.db = db
        self.git = git

    @property
    def worktree_base_dir(self) -> Path:
        """Base storage directory for created worktrees."""
        return self.paths.worktrees_dir

    def _get_worktree_config(self) -> WorktreeConfig:
        """Return active worktree configuration."""
        return Config(self.paths).worktree

    def _ensure_worktree_dir(self) -> None:
        """Create the parent worktree storage directory if missing."""
        self.worktree_base_dir.mkdir(parents=True, exist_ok=True)

    def get_active(self) -> list[Path]:
        """List immediate child directories under the worktree base path."""
        if not self.worktree_base_dir.exists():
            return []
        return [p for p in self.worktree_base_dir.iterdir() if p.is_dir()]

    def discard_partial(
        self,
        worktree_path: Path,
        temp_branch: str,
        *,
        created_dir: bool,
        created_branch: bool,
    ) -> None:
        """Best-effort removal of only the directory and branch this create call made."""
        context = {"branch": temp_branch, "path": str(worktree_path)}
        if created_dir:
            try:
                self.git.worktree_remove(self.path, worktree_path, force=True)
            except Exception as exc:
                record_fallback(exc, operation="worktree.create", context=context)
                shutil.rmtree(worktree_path, ignore_errors=True)
        if created_branch:
            try:
                self.git.branch_delete(self.path, temp_branch, force=True)
            except Exception as exc:
                record_fallback(exc, operation="worktree.create", context=context)
        try:
            self.git.worktree_prune(self.path)
        except Exception as exc:
            record_fallback(exc, operation="worktree.create", context=context)

    def _check_capacity(self, max_allowed: int = DEFAULT_MAXIMUM_WORKTREES_ALLOWED) -> WorktreeCreateResult | None:
        """Return an error result when active worktrees reach configured capacity."""
        active = self.get_active()
        if len(active) >= max_allowed:
            return WorktreeCreateResult(
                status=WorktreeCreateStatus.CAPACITY_EXCEEDED,
                errors=[f"Maximum active worktrees reached ({len(active)}/{max_allowed})."],
                fixes=[
                    "Remove a kept worktree with `dovo worktree delete <worktree-id>` (see `dovo worktree list`), or",
                    "Raise worktree.max_active_worktrees in .dovo/config.json",
                ],
            )
        return None

    def _resolve_base_ref(self, override_base_ref: str | None, default_base_ref: str = "HEAD") -> str:
        """Return the git ref to branch the worktree from."""
        if override_base_ref is not None:
            return override_base_ref
        source_branch = self.git.get_current_branch(self.path)
        if source_branch not in ("unknown", "HEAD (detached)"):
            return source_branch
        return default_base_ref

    def _check_collision(self, worktree_path: Path, temp_branch: str) -> WorktreeCreateResult | None:
        """Return already_exists when the target directory or branch exists, without running any git mutation.

        worktree_path must be unresolved, so a dangling symlink at the target name counts as existing.
        """
        try:
            path_taken = worktree_path.exists() or worktree_path.is_symlink()
        except OSError as exc:
            return WorktreeCreateResult(
                status=WorktreeCreateStatus.GIT_FAILED,
                errors=[f"Worktree path check failed (WORKTREE_GIT_FAILED): {exc}"],
                fixes=[f"Check that '{worktree_path.parent}' is readable, then retry"],
            )
        if path_taken:
            return _already_exists(worktree_path.name, self._collision_fix(worktree_path, temp_branch, path_taken=True))
        try:
            branch_taken = self.git.branch_exists(self.path, temp_branch)
        except GitPlumbingTimeoutError as exc:
            return WorktreeCreateResult(
                status=WorktreeCreateStatus.GIT_TIMEOUT,
                errors=[f"Git worktree operation timed out (WORKTREE_GIT_TIMEOUT): {exc}"],
                fixes=["Check for git lock files, credential prompts, or stuck git processes, then retry"],
            )
        except Exception as exc:
            return WorktreeCreateResult(
                status=WorktreeCreateStatus.GIT_FAILED,
                errors=[
                    logged_failure(
                        f"Git worktree operation failed (WORKTREE_GIT_FAILED): {exc}",
                        exc,
                        operation="worktree.create",
                        context=_create_context(worktree_path, temp_branch),
                    )
                ],
                fixes=["Ensure this directory is a Git repository with a valid base ref"],
            )
        if not branch_taken:
            return None
        return _already_exists(worktree_path.name, self._collision_fix(worktree_path, temp_branch, path_taken=False))

    def _collision_fix(self, worktree_path: Path, temp_branch: str, *, path_taken: bool) -> str:
        """Return show/delete advice when Dovo tracks the ID, else name the exact path or branch to clear by hand.

        `dovo worktree delete` acts only on a row that is not cleaned, so any other collision gets manual advice.
        """
        worktree_id = worktree_path.name
        row = self.db.get(worktree_id)
        if row is not None and row.status is not WorktreeStatus.CLEANED:
            return f"Inspect it with `dovo worktree show {worktree_id}` or remove it with `dovo worktree delete {worktree_id}`"
        if path_taken:
            return f"'{worktree_path}' is not tracked by Dovo; move or remove it, then retry"
        return (
            f"Branch '{temp_branch}' is not tracked by Dovo; "
            f"delete it with `git branch -D {temp_branch}` if you no longer need it, then retry"
        )

    def _probe_created_branch(self, temp_branch: str) -> bool:
        """Return True when temp_branch exists after a failed add; any probe failure returns False so cleanup never deletes on doubt."""
        try:
            return self.git.branch_exists(self.path, temp_branch)
        except Exception as exc:
            record_fallback(exc, operation="worktree.create", context={"branch": temp_branch})
            return False

    def _discard_failed_add(self, worktree_path: Path, temp_branch: str) -> None:
        """Discard whatever a failed `git worktree add` left at names the collision check proved absent.

        Existence after the failure is taken as ownership. The check ran under WorkspaceLock, so no other dovo
        process can create these names in between, and `dovo/<id>` is a namespace only dovo creates branches in.
        """
        self.discard_partial(
            worktree_path,
            temp_branch,
            created_dir=worktree_path.exists(),
            created_branch=self._probe_created_branch(temp_branch),
        )

    def _create_worktree(
        self,
        worktree_path: Path,
        temp_branch: str,
        base_ref: str,
    ) -> WorktreeCreateResult | None:
        """Create git worktree and branch, discarding on failure."""
        try:
            self.git.worktree_add(self.path, worktree_path, temp_branch, base_ref)
            return None
        except GitPlumbingTimeoutError as exc:
            self._discard_failed_add(worktree_path, temp_branch)
            return WorktreeCreateResult(
                status=WorktreeCreateStatus.GIT_TIMEOUT,
                errors=[f"Git worktree operation timed out (WORKTREE_GIT_TIMEOUT): {exc}"],
                fixes=["Check for git lock files, credential prompts, or stuck git processes, then retry"],
            )
        except Exception as exc:
            self._discard_failed_add(worktree_path, temp_branch)
            return WorktreeCreateResult(
                status=WorktreeCreateStatus.GIT_FAILED,
                errors=[
                    logged_failure(
                        f"Git worktree operation failed (WORKTREE_GIT_FAILED): {exc}",
                        exc,
                        operation="worktree.create",
                        context=_create_context(worktree_path, temp_branch),
                    )
                ],
                fixes=["Ensure this directory is a Git repository with a valid base ref"],
            )

    def _resolve_base_commit(
        self,
        worktree_path: Path,
        temp_branch: str,
    ) -> tuple[str, WorktreeCreateResult | None]:
        """Determine base commit SHA for the created worktree."""
        try:
            commit_sha = self.git.rev_parse(worktree_path, rev="HEAD")
            return commit_sha, None
        except GitPlumbingTimeoutError as exc:
            self.discard_partial(worktree_path, temp_branch, created_dir=True, created_branch=True)
            return "", WorktreeCreateResult(
                status=WorktreeCreateStatus.GIT_TIMEOUT,
                errors=[f"Git worktree operation timed out (WORKTREE_GIT_TIMEOUT): {exc}"],
            )
        except Exception as exc:
            self.discard_partial(worktree_path, temp_branch, created_dir=True, created_branch=True)
            return "", WorktreeCreateResult(
                status=WorktreeCreateStatus.GIT_FAILED,
                errors=[
                    logged_failure(
                        f"Git worktree operation failed (WORKTREE_GIT_FAILED): {exc}",
                        exc,
                        operation="worktree.create",
                        context=_create_context(worktree_path, temp_branch),
                    )
                ],
            )

    def _overlay_wip(
        self,
        worktree_path: Path,
        temp_branch: str,
    ) -> tuple[list[str], WorktreeCreateResult | None]:
        """Overlay uncommitted working tree changes into worktree."""
        try:
            paths = apply_wip_to_worktree(git=self.git, source_root=self.path, worktree_path=worktree_path)
            return paths, None
        except GitPlumbingTimeoutError as exc:
            self.discard_partial(worktree_path, temp_branch, created_dir=True, created_branch=True)
            return [], WorktreeCreateResult(
                status=WorktreeCreateStatus.GIT_TIMEOUT,
                errors=[f"Git timed out while overlaying uncommitted WIP (WORKTREE_GIT_TIMEOUT): {exc}"],
                fixes=["Check for git lock files or retry without --wip"],
            )
        except Exception as exc:
            self.discard_partial(worktree_path, temp_branch, created_dir=True, created_branch=True)
            return [], WorktreeCreateResult(
                status=WorktreeCreateStatus.WIP_FAILED,
                errors=[
                    logged_failure(
                        f"Failed to overlay uncommitted WIP into worktree (WORKTREE_WIP_FAILED): {exc}",
                        _wip_failure_cause(exc),
                        operation="worktree.create",
                        context=_create_context(worktree_path, temp_branch),
                    )
                ],
                fixes=["Resolve local conflicts and retry, or commit changes first"],
            )

    def _persist_session(self, session: WorktreeSession) -> list[str]:
        """Insert session into local database; return warnings on failure."""
        try:
            self.db.create(
                id=session.session_id,
                name=session.name,
                branch_name=session.target_branch,
                base_commit=session.base_commit,
                worktree_path=session.worktree_path,
            )
            return []
        except Exception as exc:
            context = {
                "worktree_id": session.session_id,
                "branch": session.target_branch,
                "path": str(session.worktree_path),
            }
            return [
                logged_failure(
                    f"Failed to persist worktree metadata to the local database: {exc}",
                    exc,
                    operation="worktree.create",
                    context=context,
                )
            ]

    def create(
        self,
        session_id: str | None = None,
        *,
        include_wip: bool = False,
        name: str | None = None,
        base_ref: str | None = None,
    ) -> WorktreeCreateResult:
        """Create an isolated worktree without raising for classified failures.

        Args:
            session_id: Optional fixed session id; otherwise generated (dovo_ + 8 hex).
            include_wip: When True, overlay uncommitted working tree changes.
            name: Optional human-readable worktree name.
            base_ref: Optional git ref override for worktree creation.

        Returns:
            Structured WorktreeCreateResult containing session on success.
        """
        with WorkspaceLock(self.paths.lock_file):
            worktree_cfg = self._get_worktree_config()
            self._ensure_worktree_dir()
            capacity_err = self._check_capacity(worktree_cfg.max_active_worktrees)
            if capacity_err is not None:
                return capacity_err

            return self._build_worktree(
                session_id, include_wip=include_wip, name=name, base_ref=base_ref, worktree_cfg=worktree_cfg
            )

    def _build_worktree(
        self,
        session_id: str | None,
        *,
        include_wip: bool,
        name: str | None,
        base_ref: str | None,
        worktree_cfg: WorktreeConfig,
    ) -> WorktreeCreateResult:
        """Create the worktree, resolve its base commit, and overlay WIP for a new worktree session."""
        resolved_name = _clean_opt_str(name)
        override_base_ref = _clean_opt_str(base_ref)
        sid = session_id or new_session_id("dovo")
        worktree_path = (self.worktree_base_dir / sid).resolve()
        temp_branch = f"dovo/{sid}"
        collision_err = self._check_collision(self.worktree_base_dir / sid, temp_branch)
        if collision_err is not None:
            return collision_err
        resolved_base = self._resolve_base_ref(override_base_ref, worktree_cfg.base_ref)

        worktree_err = self._create_worktree(worktree_path, temp_branch, resolved_base)
        if worktree_err is not None:
            return worktree_err

        base_commit, commit_err = self._resolve_base_commit(worktree_path, temp_branch)
        if commit_err is not None:
            return commit_err

        wip_paths: list[str] = []
        if include_wip:
            wip_paths, wip_err = self._overlay_wip(worktree_path, temp_branch)
            if wip_err is not None:
                return wip_err

        session = WorktreeSession(
            session_id=sid,
            target_branch=temp_branch,
            worktree_path=worktree_path,
            base_commit=base_commit,
            name=resolved_name,
            created_at=datetime.now(UTC).isoformat(),
            wip_applied=bool(include_wip),
            wip_paths=wip_paths,
        )

        warnings = self._persist_session(session)
        return WorktreeCreateResult(
            status=WorktreeCreateStatus.OK,
            session=session,
            warnings=warnings,
        )

    def _remove_worktree_dir(self, worktree_path: Path, *, force: bool) -> str | None:
        """Remove worktree directory with git worktree remove and rmtree fallback."""
        if not worktree_path.exists():
            return None
        try:
            self.git.worktree_remove(self.path, worktree_path, force=force)
            return None
        except Exception as exc:
            context = {"path": str(worktree_path)}
            try:
                shutil.rmtree(worktree_path)
            except Exception as rm_exc:
                return logged_failure(
                    f"Failed to remove worktree directory at '{worktree_path}': {exc}; fallback removal failed: {rm_exc}",
                    rm_exc,
                    operation="worktree.cleanup",
                    context=context,
                )
            record_fallback(exc, operation="worktree.cleanup", context=context)
            return None

    def _delete_branch(self, branch_name: str) -> str | None:
        """Delete temporary branch, ignoring expected idempotent not-found errors."""
        try:
            self.git.branch_delete(self.path, branch_name, force=True)
            return None
        except (GitCommandError, GitNotFoundError):
            # Best-effort: branch may already be deleted during idempotent cleanup.
            return None
        except Exception as exc:
            return logged_failure(
                f"Failed to delete branch '{branch_name}': {exc}",
                exc,
                operation="worktree.cleanup",
                context={"branch": branch_name},
            )

    def cleanup(
        self,
        target: WorktreeSession | WorktreeRecord,
        *,
        force: bool = True,
    ) -> list[str]:
        """Remove worktree, delete throwaway branch, and prune (idempotent).

        Args:
            target: WorktreeSession or WorktreeRecord to clean up.
            force: Force removal of worktree even if untracked files exist.

        Returns:
            List of warning messages encountered during cleanup steps.
        """
        with WorkspaceLock(self.paths.lock_file):
            worktree_path, session_id, branch_name = _extract_target_metadata(target)
            context = {"worktree_id": session_id, "branch": branch_name, "path": str(worktree_path)}
            warnings: list[str] = []

            dir_warning = self._remove_worktree_dir(worktree_path, force=force)
            if dir_warning:
                warnings.append(dir_warning)

            try:
                self.db.update_status(session_id, WorktreeStatus.CLEANED)
            except Exception as exc:
                warnings.append(
                    logged_failure(
                        f"Failed to update database status to 'cleaned' for worktree '{session_id}': {exc}",
                        exc,
                        operation="worktree.cleanup",
                        context=context,
                    )
                )

            branch_warning = self._delete_branch(branch_name)
            if branch_warning:
                warnings.append(branch_warning)

            try:
                self.prune()
            except Exception as exc:
                warnings.append(
                    logged_failure(
                        f"Failed to prune git worktrees: {exc}", exc, operation="worktree.cleanup", context=context
                    )
                )

            return warnings

    def prune(self) -> None:
        """Prune stale Git worktree administrative records."""
        try:
            self.git.worktree_prune(self.path)
        except (GitCommandError, GitNotFoundError):
            # Best-effort: ignore harmless git errors during administrative worktree prune.
            pass
