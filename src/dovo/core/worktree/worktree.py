"""Worktree domain entrypoint."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext

from dovo.common.clock import Clock
from dovo.common.exceptions import catch_failure
from dovo.common.filesystem import WorkspacePaths
from dovo.common.lock import WorkspaceLock
from dovo.common.session_id import SessionIdFactory
from dovo.core.config import Config, ConfigLoadDiagnostics
from dovo.core.db import SessionsRepository, WorktreeRecord, WorktreesRepository, WorktreeStatus
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.exceptions import WorktreeApplyConflict
from dovo.core.worktree.models import (
    WorktreeApplyResult,
    WorktreeApplyStrategy,
    WorktreeCleanupResult,
    WorktreeCreateResult,
    WorktreeDeleteResult,
    WorktreeDiffResult,
    WorktreeListResult,
    WorktreePruneResult,
    WorktreeSession,
    WorktreeShowResult,
)
from dovo.core.worktree.services.lifecycle import WorktreeLifecycle
from dovo.core.worktree.services.list import collect_worktree_list
from dovo.core.worktree.services.patch import WorktreePatch
from dovo.core.worktree.services.pruner import WorktreePruner
from dovo.core.worktree.services.show import collect_worktree_show, find_worktree


def _target_id(target: WorktreeSession | WorktreeRecord) -> str:
    """Return the worktree id of a session or record."""
    if isinstance(target, WorktreeRecord):
        return target.id
    return target.session_id


class Worktree:
    """Unified entrypoint for Git worktrees, lifecycle, and diff/patch application."""

    def __init__(
        self,
        paths: WorkspacePaths,
        *,
        git: GitRunner,
        clock: Clock,
        new_id: SessionIdFactory,
        db: WorktreesRepository | None = None,
        sessions_db: SessionsRepository | None = None,
    ) -> None:
        self.paths = paths
        self.path = paths.root_dir
        self.cwd = self.path
        self.db = (
            db if db is not None else WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        )
        self.sessions_db = sessions_db
        self.git = git
        self.lifecycle = WorktreeLifecycle(self.paths, self.db, git=git, clock=clock, new_id=new_id)
        self.patch = WorktreePatch(self.paths, self.db, git=git, lifecycle=self.lifecycle)

    def list(self, status: WorktreeStatus | str | None = None) -> WorktreeListResult:
        """List tracked worktrees with lifecycle status, reconciling stale directories; report a failure by its error_code."""
        with catch_failure("worktree.list", path=str(self.path)) as caught:
            listed = collect_worktree_list(self.paths, self.db, status)
        if caught.failure is not None:
            return WorktreeListResult(
                **caught.failure.result_fields(),
            )
        return listed

    def show(self, worktree_id: str) -> WorktreeShowResult:
        """Show details for one tracked worktree, reconciling a stale active row; report a failure by its error_code."""
        with catch_failure("worktree.show", worktree_id=worktree_id) as caught:
            shown = collect_worktree_show(self.paths, self.db, worktree_id)
        if caught.failure is not None:
            return WorktreeShowResult(
                **caught.failure.result_fields(),
            )
        return shown

    def delete(
        self, worktree_id: str, *, confirm: Callable[[WorktreeRecord], bool] | None = None
    ) -> WorktreeDeleteResult:
        """Look up, confirm and remove a worktree; confirm=None skips confirmation.

        An unknown id or a cleaned row is never confirmed; confirm runs before the workspace lock is taken.

        Raises:
            LockTimeoutError: If another process holds the workspace lock.
        """
        row: WorktreeRecord | None = None
        with catch_failure("worktree.delete", worktree_id=worktree_id) as caught:
            row = find_worktree(self.db, worktree_id)
            if row.status is WorktreeStatus.CLEANED:
                return WorktreeDeleteResult(worktree_id=worktree_id, worktree=row, already_cleaned=True)
            if confirm is not None and not confirm(row):
                return WorktreeDeleteResult(worktree_id=worktree_id, worktree=row, cancelled=True)
            with WorkspaceLock(self.paths.lock_file):
                removal = self.lifecycle.cleanup(row)
        if caught.failure is not None:
            return WorktreeDeleteResult(
                worktree_id=worktree_id,
                worktree=row,
                **caught.failure.result_fields(),
            )
        return WorktreeDeleteResult(
            worktree_id=worktree_id,
            worktree=row,
            deleted=True,
            warnings=list(removal.warnings),
            diagnostics=list(removal.diagnostics),
        )

    def create(
        self,
        session_id: str | None = None,
        *,
        include_wip: bool = False,
        name: str | None = None,
        base_ref: str | None = None,
    ) -> WorktreeCreateResult:
        """Create an isolated worktree; report a failure by its error_code.

        Raises:
            LockTimeoutError: If another process holds the workspace lock.
        """
        with (
            WorkspaceLock(self.paths.lock_file),
            catch_failure("worktree.create", worktree_id=session_id, path=str(self.path)) as caught,
        ):
            worktree_cfg = Config(self.paths).worktree
            created = self.lifecycle.create(
                worktree_cfg, session_id=session_id, include_wip=include_wip, name=name, base_ref=base_ref
            )
        if caught.failure is not None:
            data = caught.failure.data
            records = data.records if isinstance(data, ConfigLoadDiagnostics) else ()
            return WorktreeCreateResult(
                **caught.failure.result_fields(prior_diagnostics=records),
            )
        return WorktreeCreateResult(
            session=created.session, warnings=list(created.warnings), diagnostics=list(created.diagnostics)
        )

    def cleanup(
        self,
        session: WorktreeSession | WorktreeRecord,
        *,
        force: bool = True,
    ) -> WorktreeCleanupResult:
        """Remove worktree, delete throwaway branch, and prune; report failed steps as warnings and an unexpected error by its error_code.

        Raises:
            LockTimeoutError: If another process holds the workspace lock.
        """
        worktree_id = _target_id(session)
        with (
            WorkspaceLock(self.paths.lock_file),
            catch_failure("worktree.cleanup", worktree_id=worktree_id, path=str(session.worktree_path)) as caught,
        ):
            removal = self.lifecycle.cleanup(session, force=force)
        if caught.failure is not None:
            return WorktreeCleanupResult(
                worktree_id=worktree_id,
                **caught.failure.result_fields(),
            )
        return WorktreeCleanupResult(
            worktree_id=worktree_id, warnings=list(removal.warnings), diagnostics=list(removal.diagnostics)
        )

    def prune(
        self,
        *,
        dry_run: bool = False,
        force: bool = False,
    ) -> WorktreePruneResult:
        """Safely prune stale worktrees, orphaned directories, and temporary branches; report a failure by its error_code.

        Raises:
            LockTimeoutError: If another process holds the workspace lock.
        """
        pruner = WorktreePruner(self.paths, self.db, git=self.git, sessions_db=self.sessions_db)
        lock = nullcontext() if dry_run else WorkspaceLock(self.paths.lock_file)
        with lock, catch_failure("worktree.prune", path=str(self.path)) as caught:
            pruned = pruner.prune(dry_run=dry_run, force=force)
        if caught.failure is not None:
            return WorktreePruneResult(
                status=None,
                dry_run=dry_run,
                force=force,
                **caught.failure.result_fields(),
            )
        return pruned

    def apply(
        self,
        worktree_id: str,
        *,
        strategy: WorktreeApplyStrategy = WorktreeApplyStrategy.PATCH,
        allow_dirty: bool = False,
        dry_run: bool = False,
        delete: bool = False,
        message: str | None = None,
    ) -> WorktreeApplyResult:
        """Apply worktree changes back to the main workspace; report a failure by its error_code.

        Raises:
            LockTimeoutError: If another process holds the workspace lock.
        """
        with WorkspaceLock(self.paths.lock_file), catch_failure("worktree.apply", worktree_id=worktree_id) as caught:
            applied = self.patch.apply(
                worktree_id,
                strategy=strategy,
                allow_dirty=allow_dirty,
                dry_run=dry_run,
                delete=delete,
                message=message,
            )
        if caught.failure is not None:
            conflict = caught.failure.data
            return WorktreeApplyResult(
                worktree_id=worktree_id,
                strategy=strategy,
                conflicting_files=list(conflict.conflicting_files)
                if isinstance(conflict, WorktreeApplyConflict)
                else [],
                **caught.failure.result_fields(),
            )
        return applied

    def diff(self, worktree_id: str, *, stat: bool = False) -> WorktreeDiffResult:
        """Inspect unified diff or diffstat for a worktree; report a failure by its error_code."""
        with catch_failure("worktree.diff", worktree_id=worktree_id) as caught:
            diffed = self.patch.diff(worktree_id, stat=stat)
        if caught.failure is not None:
            return WorktreeDiffResult(
                worktree_id=worktree_id,
                **caught.failure.result_fields(),
            )
        return diffed
