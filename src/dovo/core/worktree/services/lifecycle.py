"""Worktree lifecycle management service."""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Final, TypeGuard

from dovo.common.clock import Clock
from dovo.common.constants import DEFAULT_MAXIMUM_WORKTREES_ALLOWED
from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord, error_diagnostics, record_exception
from dovo.common.error_codes import ErrorCode
from dovo.common.exceptions import DomainError
from dovo.common.filesystem import WorkspacePaths
from dovo.common.models import Remediation
from dovo.common.session_id import SessionIdFactory
from dovo.core.config.models import WorktreeConfig
from dovo.core.db import WorktreeRecord, WorktreesRepository, WorktreeStatus
from dovo.core.git.exceptions import GitError
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.exceptions import WorktreeCapacityError, WorktreeError, recorded_cause
from dovo.core.worktree.models import WorktreeCreation, WorktreeRemoval, WorktreeSession
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


_StepFailure = tuple[str, list[DiagnosticRecord]]

_GIT_TIMEOUT_REMEDIATION: Final = Remediation(
    message="Check for git lock files, credential prompts, or stuck git processes, then retry"
)
_GIT_FAILED_REMEDIATION: Final = Remediation(message="Ensure this directory is a Git repository with a valid base ref")
_WIP_TIMEOUT_REMEDIATION: Final = Remediation(message="Check for git lock files or retry without '--wip'")
_WIP_FAILED_REMEDIATION: Final = Remediation(message="Resolve local conflicts and retry, or commit changes first")
# Branch deletion and git worktree prune ignore these during idempotent cleanup; any other code is a failure.
_IGNORED_CLEANUP_CODES: Final = frozenset({ErrorCode.GIT_FAILED, ErrorCode.GIT_BINARY_MISSING})


def _is_git_code(exc: Exception, code: ErrorCode) -> TypeGuard[GitError]:
    """Return whether exc is a GitError carrying code."""
    return isinstance(exc, GitError) and exc.error_code is code


def _git_step_error(exc: GitError, *, remediate: bool) -> WorktreeError:
    """Return a WorktreeError with exc's code and today's timed-out or failed message, with its remediation when remediate."""
    if exc.error_code is ErrorCode.GIT_TIMEOUT:
        message = f"Git worktree operation timed out (WORKTREE_GIT_TIMEOUT): {exc}"
        remediation = _GIT_TIMEOUT_REMEDIATION
    else:
        message = f"Git worktree operation failed (WORKTREE_GIT_FAILED): {exc}"
        remediation = _GIT_FAILED_REMEDIATION
    return WorktreeError(exc.error_code, message, [remediation] if remediate else [])


def _record_recovered(exc: Exception, *, operation: str, context: Mapping[str, str | None]) -> None:
    """Record exc at debug level for a failure Dovo recovered from, skipping a GIT_BINARY_MISSING GitError."""
    if _is_git_code(exc, ErrorCode.GIT_BINARY_MISSING):
        return

    record_exception(exc, level=DiagnosticLevel.DEBUG, operation=operation, context=context)


def _record_reported(exc: Exception, *, operation: str, context: Mapping[str, str | None]) -> list[DiagnosticRecord]:
    """Record a failure reported as a warning at error level and return its record; a DomainError records only its OSError cause."""
    recorded: BaseException = exc
    if isinstance(exc, DomainError):
        if not isinstance(exc.__cause__, OSError):
            return []
        recorded = exc.__cause__
    return error_diagnostics(
        record_exception(recorded, level=DiagnosticLevel.ERROR, operation=operation, context=context)
    )


class WorktreeLifecycle:
    """Orchestrates worktree creation, validation, cleanup, and pruning."""

    def __init__(
        self,
        paths: WorkspacePaths,
        db: WorktreesRepository,
        *,
        git: GitRunner,
        clock: Clock,
        new_id: SessionIdFactory,
    ) -> None:
        """Initialize lifecycle manager.

        Args:
            paths: Resolved command-invocation workspace paths.
            db: Explicit WorktreesRepository instance.
            git: Runner for every git command.
            clock: Source of a created worktree's created_at.
            new_id: Generator of a worktree ID when create() is given none.
        """
        self.paths = paths
        self.path = paths.root_dir
        self.db = db
        self.git = git
        self.clock = clock
        self.new_id = new_id

    @property
    def worktree_base_dir(self) -> Path:
        """Base storage directory for created worktrees."""
        return self.paths.worktrees_dir

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
                _record_recovered(exc, operation="worktree.create", context=context)
                shutil.rmtree(worktree_path, ignore_errors=True)
        if created_branch:
            try:
                self.git.branch_delete(self.path, temp_branch, force=True)
            except Exception as exc:
                _record_recovered(exc, operation="worktree.create", context=context)
        try:
            self.git.worktree_prune(self.path)
        except Exception as exc:
            _record_recovered(exc, operation="worktree.create", context=context)

    def _check_capacity(self, max_allowed: int = DEFAULT_MAXIMUM_WORKTREES_ALLOWED) -> None:
        """Raise when active worktrees reach configured capacity.

        Raises:
            WorktreeCapacityError: When max_allowed worktrees are active.
        """
        active = self.get_active()
        if len(active) >= max_allowed:
            raise WorktreeCapacityError(
                f"Maximum active worktrees reached ({len(active)}/{max_allowed}).",
                [
                    Remediation(
                        message=(
                            "Remove a kept worktree with 'dovo worktree delete <worktree-id>' (see 'dovo worktree list')"
                        ),
                        command="dovo worktree delete <worktree-id>",
                    ),
                    Remediation(message="Raise 'worktree.max_active_worktrees' in '.dovo/config.json'"),
                ],
            )

    def _resolve_base_ref(self, override_base_ref: str | None, default_base_ref: str = "HEAD") -> str:
        """Return the git ref to branch the worktree from."""
        if override_base_ref is not None:
            return override_base_ref
        source_branch = self.git.get_current_branch(self.path)
        if source_branch not in ("unknown", "HEAD (detached)"):
            return source_branch
        return default_base_ref

    def _check_collision(self, worktree_path: Path, temp_branch: str) -> None:
        """Raise when the target directory or branch exists, without running any git mutation.

        worktree_path must be unresolved, so a dangling symlink at the target name counts as existing.

        Raises:
            WorktreeError: WORKTREE_PATH_CONFLICT for a taken path or branch, WORKTREE_CREATION_FAILED when the path
                cannot be checked, or the git failure's code when the branch probe fails.
        """
        try:
            path_taken = worktree_path.exists() or worktree_path.is_symlink()
        except OSError as exc:
            raise WorktreeError(
                ErrorCode.WORKTREE_CREATION_FAILED,
                f"Worktree path check failed (WORKTREE_GIT_FAILED): {exc}",
                [Remediation(message=f"Check that '{worktree_path.parent}' is readable, then retry")],
            ) from exc
        if not path_taken:
            try:
                branch_taken = self.git.branch_exists(self.path, temp_branch)
            except GitError as exc:
                raise _git_step_error(exc, remediate=True) from recorded_cause(exc)
            if not branch_taken:
                return
        raise WorktreeError(
            ErrorCode.WORKTREE_PATH_CONFLICT,
            f"Worktree '{worktree_path.name}' already exists (WORKTREE_PATH_CONFLICT).",
            self._collision_remediations(worktree_path, temp_branch, path_taken=path_taken),
        )

    def _collision_remediations(self, worktree_path: Path, temp_branch: str, *, path_taken: bool) -> list[Remediation]:
        """Return show/delete steps when Dovo tracks the ID, else the exact path or branch to clear by hand.

        `dovo worktree delete` acts only on a row that is not cleaned, so any other collision gets manual advice.
        """
        worktree_id = worktree_path.name
        row = self.db.get(worktree_id)
        if row is not None and row.status is not WorktreeStatus.CLEANED:
            return [
                Remediation(
                    message=f"Inspect it with 'dovo worktree show {worktree_id}'",
                    command=f"dovo worktree show {worktree_id}",
                ),
                Remediation(
                    message=f"Remove it with 'dovo worktree delete {worktree_id}'",
                    command=f"dovo worktree delete {worktree_id}",
                ),
            ]
        if path_taken:
            return [
                Remediation(message=f"Path '{worktree_path}' is not tracked by Dovo; move or remove it, then retry")
            ]
        return [
            Remediation(
                message=(
                    f"Branch '{temp_branch}' is not tracked by Dovo; "
                    f"delete it with 'git branch -D {temp_branch}' if you no longer need it, then retry"
                ),
                command=f"git branch -D {temp_branch}",
            )
        ]

    def _probe_created_branch(self, temp_branch: str) -> bool:
        """Return True when temp_branch exists after a failed add; any probe failure returns False so cleanup never deletes on doubt."""
        try:
            return self.git.branch_exists(self.path, temp_branch)
        except Exception as exc:
            _record_recovered(exc, operation="worktree.create", context={"branch": temp_branch})
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

    def _create_worktree(self, worktree_path: Path, temp_branch: str, base_ref: str) -> None:
        """Create git worktree and branch, discarding what a failed add left before re-raising.

        Raises:
            WorktreeError: With the git failure's code when git worktree add fails.
        """
        try:
            self.git.worktree_add(self.path, worktree_path, temp_branch, base_ref)
        except Exception as exc:
            # Recover then re-raise: catch_failure records an unexpected exc once.
            self._discard_failed_add(worktree_path, temp_branch)
            if isinstance(exc, GitError):
                raise _git_step_error(exc, remediate=True) from recorded_cause(exc)
            raise

    def _resolve_base_commit(self, worktree_path: Path, temp_branch: str) -> str:
        """Return the base commit SHA of the created worktree, discarding it before re-raising a failure.

        Raises:
            WorktreeError: With the git failure's code when git rev-parse fails.
        """
        try:
            return self.git.rev_parse(worktree_path, rev="HEAD")
        except Exception as exc:
            # Recover then re-raise: catch_failure records an unexpected exc once.
            self.discard_partial(worktree_path, temp_branch, created_dir=True, created_branch=True)
            if isinstance(exc, GitError):
                raise _git_step_error(exc, remediate=False) from recorded_cause(exc)
            raise

    def _overlay_wip(self, worktree_path: Path, temp_branch: str) -> list[str]:
        """Overlay uncommitted working tree changes into worktree, discarding it before re-raising a failure.

        Raises:
            WorktreeError: With the failure's code when git or the overlay fails.
        """
        try:
            return apply_wip_to_worktree(git=self.git, source_root=self.path, worktree_path=worktree_path)
        except Exception as exc:
            # Recover then re-raise: catch_failure records an unexpected exc once.
            self.discard_partial(worktree_path, temp_branch, created_dir=True, created_branch=True)
            if _is_git_code(exc, ErrorCode.GIT_TIMEOUT):
                raise WorktreeError(
                    exc.error_code,
                    f"Git timed out while overlaying uncommitted WIP (WORKTREE_GIT_TIMEOUT): {exc}",
                    [_WIP_TIMEOUT_REMEDIATION],
                ) from recorded_cause(exc)
            if isinstance(exc, (GitError, WorktreeError)):
                raise WorktreeError(
                    exc.error_code,
                    f"Failed to overlay uncommitted WIP into worktree (WORKTREE_WIP_FAILED): {exc}",
                    [_WIP_FAILED_REMEDIATION],
                ) from recorded_cause(exc)
            raise

    def _persist_session(self, session: WorktreeSession) -> tuple[list[str], list[DiagnosticRecord]]:
        """Insert session into local database; return the warning and its records on failure."""
        try:
            self.db.create(
                id=session.session_id,
                name=session.name,
                branch_name=session.target_branch,
                base_commit=session.base_commit,
                worktree_path=session.worktree_path,
            )
            return [], []
        except Exception as exc:
            context = {
                "worktree_id": session.session_id,
                "branch": session.target_branch,
                "path": str(session.worktree_path),
            }
            return (
                [f"Failed to persist worktree metadata to the local database: {exc}"],
                _record_reported(exc, operation="worktree.create", context=context),
            )

    def create(
        self,
        worktree_cfg: WorktreeConfig,
        session_id: str | None = None,
        *,
        include_wip: bool = False,
        name: str | None = None,
        base_ref: str | None = None,
    ) -> WorktreeCreation:
        """Create an isolated worktree under the caller's lock.

        Args:
            worktree_cfg: The workspace's worktree configuration.
            session_id: Optional fixed session id; otherwise generated by new_id("dovo").
            include_wip: When True, overlay uncommitted working tree changes.
            name: Optional human-readable worktree name.
            base_ref: Optional git ref override for worktree creation.

        Returns:
            The created session, with the warnings and records of a failed metadata persist.

        Raises:
            WorktreeError: For a capacity, collision, git, WIP or path-check failure, carrying its error_code.
        """
        self._ensure_worktree_dir()
        self._check_capacity(worktree_cfg.max_active_worktrees)
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
    ) -> WorktreeCreation:
        """Create the worktree, resolve its base commit, and overlay WIP for a new worktree session."""
        resolved_name = _clean_opt_str(name)
        override_base_ref = _clean_opt_str(base_ref)
        sid = session_id or self.new_id("dovo")
        worktree_path = (self.worktree_base_dir / sid).resolve()
        temp_branch = f"dovo/{sid}"
        self._check_collision(self.worktree_base_dir / sid, temp_branch)
        resolved_base = self._resolve_base_ref(override_base_ref, worktree_cfg.base_ref)

        self._create_worktree(worktree_path, temp_branch, resolved_base)
        base_commit = self._resolve_base_commit(worktree_path, temp_branch)
        wip_paths = self._overlay_wip(worktree_path, temp_branch) if include_wip else []

        session = WorktreeSession(
            session_id=sid,
            target_branch=temp_branch,
            worktree_path=worktree_path,
            base_commit=base_commit,
            name=resolved_name,
            created_at=self.clock().isoformat(),
            wip_applied=bool(include_wip),
            wip_paths=wip_paths,
        )

        warnings, diagnostics = self._persist_session(session)
        return WorktreeCreation(session=session, warnings=tuple(warnings), diagnostics=tuple(diagnostics))

    def _remove_worktree_dir(self, worktree_path: Path, *, force: bool) -> _StepFailure | None:
        """Remove worktree directory with git worktree remove and rmtree fallback; return the failure when both fail."""
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
                return (
                    f"Failed to remove worktree directory at '{worktree_path}': {exc}; fallback removal failed: {rm_exc}",
                    _record_reported(rm_exc, operation="worktree.cleanup", context=context),
                )
            _record_recovered(exc, operation="worktree.cleanup", context=context)
            return None

    def _mark_cleaned(self, session_id: str, context: Mapping[str, str | None]) -> _StepFailure | None:
        """Set the worktree's database status to cleaned; return the failure when the update fails."""
        try:
            self.db.update_status(session_id, WorktreeStatus.CLEANED)
            return None
        except Exception as exc:
            return (
                f"Failed to update database status to 'cleaned' for worktree '{session_id}': {exc}",
                _record_reported(exc, operation="worktree.cleanup", context=context),
            )

    def _delete_branch(self, branch_name: str) -> _StepFailure | None:
        """Delete temporary branch, ignoring expected idempotent not-found errors; return any other failure."""
        try:
            self.git.branch_delete(self.path, branch_name, force=True)
            return None
        except Exception as exc:
            if isinstance(exc, GitError) and exc.error_code in _IGNORED_CLEANUP_CODES:
                # Best-effort: branch may already be deleted during idempotent cleanup.
                return None
            return (
                f"Failed to delete branch '{branch_name}': {exc}",
                _record_reported(exc, operation="worktree.cleanup", context={"branch": branch_name}),
            )

    def _prune_after_cleanup(self, context: Mapping[str, str | None]) -> _StepFailure | None:
        """Prune git's worktree records; return the failure when pruning fails."""
        try:
            self.prune()
            return None
        except Exception as exc:
            return (
                f"Failed to prune git worktrees: {exc}",
                _record_reported(exc, operation="worktree.cleanup", context=context),
            )

    def cleanup(
        self,
        target: WorktreeSession | WorktreeRecord,
        *,
        force: bool = True,
    ) -> WorktreeRemoval:
        """Remove worktree, delete throwaway branch, and prune (idempotent) under the caller's lock.

        Args:
            target: WorktreeSession or WorktreeRecord to clean up.
            force: Force removal of worktree even if untracked files exist.

        Returns:
            A warning for each failed cleanup step, with its error-level records.
        """
        worktree_path, session_id, branch_name = _extract_target_metadata(target)
        context = {"worktree_id": session_id, "branch": branch_name, "path": str(worktree_path)}
        steps = [
            self._remove_worktree_dir(worktree_path, force=force),
            self._mark_cleaned(session_id, context),
            self._delete_branch(branch_name),
            self._prune_after_cleanup(context),
        ]
        failures = [step for step in steps if step is not None]
        return WorktreeRemoval(
            warnings=tuple(message for message, _ in failures),
            diagnostics=tuple(record for _, records in failures for record in records),
        )

    def prune(self) -> None:
        """Prune stale Git worktree administrative records."""
        try:
            self.git.worktree_prune(self.path)
        except GitError as exc:
            # Best-effort: ignore harmless git errors during administrative worktree prune.
            if exc.error_code not in _IGNORED_CLEANUP_CODES:
                raise
