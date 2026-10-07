"""Worktree lifecycle management service."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

from dovo.common.constants import DEFAULT_MAXIMUM_WORKTREES_ALLOWED
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
from dovo.core.worktree.models import (
    WorktreeCreateResult,
    WorktreeCreateStatus,
    WorktreeSession,
)
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


class WorktreeLifecycle:
    """Orchestrates worktree creation, validation, cleanup, and pruning."""

    def __init__(
        self,
        paths: WorkspacePaths,
        db: WorktreesRepository,
    ) -> None:
        """Initialize lifecycle manager.

        Args:
            paths: Resolved command-invocation workspace paths.
            db: Explicit WorktreesRepository instance.
        """
        self.paths = paths
        self.path = paths.root_dir
        self.db = db

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

    def discard_partial(self, worktree_path: Path, temp_branch: str) -> None:
        """Best-effort removal of a partial worktree/branch after failed creation."""
        if worktree_path.exists():
            try:
                GitRunner.worktree_remove(self.path, worktree_path, force=True)
            except Exception:
                # Best-effort fallback: remove directory directly if git worktree removal fails.
                shutil.rmtree(worktree_path, ignore_errors=True)
        try:
            GitRunner.branch_delete(self.path, temp_branch, force=True)
        except Exception:
            # Best-effort cleanup: branch may not have been created yet.
            pass
        try:
            GitRunner.worktree_prune(self.path)
        except Exception:
            # Best-effort cleanup: ignore errors during worktree prune.
            pass

    def _check_capacity(self, max_allowed: int = DEFAULT_MAXIMUM_WORKTREES_ALLOWED) -> WorktreeCreateResult | None:
        """Return an error result when active worktrees reach configured capacity."""
        active = self.get_active()
        if len(active) >= max_allowed:
            return WorktreeCreateResult(
                status=WorktreeCreateStatus.CAPACITY_EXCEEDED,
                errors=[f"Maximum active worktrees reached ({len(active)}/{max_allowed})."],
                fixes=[
                    "Run `dovo prune` to remove stale worktrees, or",
                    "Raise worktree.max_active_worktrees in .dovo/config.json",
                ],
            )
        return None

    def _resolve_base_ref(self, override_base_ref: str | None, default_base_ref: str = "HEAD") -> str:
        """Return the git ref to branch the worktree from."""
        if override_base_ref is not None:
            return override_base_ref
        source_branch = GitRunner.get_current_branch(self.path)
        if source_branch not in ("unknown", "HEAD (detached)"):
            return source_branch
        return default_base_ref

    def _create_worktree(
        self,
        worktree_path: Path,
        temp_branch: str,
        base_ref: str,
    ) -> WorktreeCreateResult | None:
        """Create git worktree and branch, discarding on failure."""
        try:
            GitRunner.worktree_add(self.path, worktree_path, temp_branch, base_ref)
            return None
        except GitPlumbingTimeoutError as exc:
            self.discard_partial(worktree_path, temp_branch)
            return WorktreeCreateResult(
                status=WorktreeCreateStatus.GIT_TIMEOUT,
                errors=[f"Git worktree operation timed out (WORKTREE_GIT_TIMEOUT): {exc}"],
                fixes=["Check for git lock files, credential prompts, or stuck git processes, then retry"],
            )
        except Exception as exc:
            self.discard_partial(worktree_path, temp_branch)
            return WorktreeCreateResult(
                status=WorktreeCreateStatus.GIT_FAILED,
                errors=[f"Git worktree operation failed (WORKTREE_GIT_FAILED): {exc}"],
                fixes=["Ensure this directory is a Git repository with a valid base ref"],
            )

    def _resolve_base_commit(
        self,
        worktree_path: Path,
        temp_branch: str,
    ) -> tuple[str, WorktreeCreateResult | None]:
        """Determine base commit SHA for the created worktree."""
        try:
            commit_sha = GitRunner.rev_parse(worktree_path, rev="HEAD")
            return commit_sha, None
        except GitPlumbingTimeoutError as exc:
            self.discard_partial(worktree_path, temp_branch)
            return "", WorktreeCreateResult(
                status=WorktreeCreateStatus.GIT_TIMEOUT,
                errors=[f"Git worktree operation timed out (WORKTREE_GIT_TIMEOUT): {exc}"],
            )
        except Exception as exc:
            self.discard_partial(worktree_path, temp_branch)
            return "", WorktreeCreateResult(
                status=WorktreeCreateStatus.GIT_FAILED,
                errors=[f"Git worktree operation failed (WORKTREE_GIT_FAILED): {exc}"],
            )

    def _overlay_wip(
        self,
        worktree_path: Path,
        temp_branch: str,
    ) -> tuple[list[str], WorktreeCreateResult | None]:
        """Overlay uncommitted working tree changes into worktree."""
        try:
            paths = apply_wip_to_worktree(source_root=self.path, worktree_path=worktree_path)
            return paths, None
        except GitPlumbingTimeoutError as exc:
            self.discard_partial(worktree_path, temp_branch)
            return [], WorktreeCreateResult(
                status=WorktreeCreateStatus.GIT_TIMEOUT,
                errors=[f"Git timed out while overlaying uncommitted WIP (WORKTREE_GIT_TIMEOUT): {exc}"],
                fixes=["Check for git lock files or retry without --wip"],
            )
        except Exception as exc:
            self.discard_partial(worktree_path, temp_branch)
            return [], WorktreeCreateResult(
                status=WorktreeCreateStatus.WIP_FAILED,
                errors=[f"Failed to overlay uncommitted WIP into worktree (WORKTREE_WIP_FAILED): {exc}"],
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
            return [f"Failed to persist worktree metadata to the local database: {exc}"]

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
            GitRunner.worktree_remove(self.path, worktree_path, force=force)
            return None
        except Exception as exc:
            try:
                shutil.rmtree(worktree_path)
                return None
            except Exception as rm_exc:
                return f"Failed to remove worktree directory at '{worktree_path}': {exc}; fallback removal failed: {rm_exc}"

    def _delete_branch(self, branch_name: str) -> str | None:
        """Delete temporary branch, ignoring expected idempotent not-found errors."""
        try:
            GitRunner.branch_delete(self.path, branch_name, force=True)
            return None
        except (GitCommandError, GitNotFoundError):
            # Best-effort: branch may already be deleted during idempotent cleanup.
            return None
        except Exception as exc:
            return f"Failed to delete branch '{branch_name}': {exc}"

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
            warnings: list[str] = []

            dir_warning = self._remove_worktree_dir(worktree_path, force=force)
            if dir_warning:
                warnings.append(dir_warning)

            try:
                self.db.update_status(session_id, WorktreeStatus.CLEANED)
            except Exception as exc:
                warnings.append(f"Failed to update database status to 'cleaned' for worktree '{session_id}': {exc}")

            branch_warning = self._delete_branch(branch_name)
            if branch_warning:
                warnings.append(branch_warning)

            try:
                self.prune()
            except Exception as exc:
                warnings.append(f"Failed to prune git worktrees: {exc}")

            return warnings

    def prune(self) -> None:
        """Prune stale Git worktree administrative records."""
        try:
            GitRunner.worktree_prune(self.path)
        except (GitCommandError, GitNotFoundError):
            # Best-effort: ignore harmless git errors during administrative worktree prune.
            pass
