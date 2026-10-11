"""Worktree domain entrypoint."""

from __future__ import annotations

from pathlib import Path

from dovo.common.filesystem import WorkspacePaths
from dovo.common.lock import WorkspaceLock
from dovo.core.db import SessionsRepository, WorktreeRecord, WorktreesRepository, WorktreeStatus
from dovo.core.worktree.models import (
    WorktreeApplyResult,
    WorktreeApplyStrategy,
    WorktreeCreateResult,
    WorktreeDeleteResult,
    WorktreeDetectionResult,
    WorktreeDiffResult,
    WorktreeListResult,
    WorktreePruneResult,
    WorktreeSession,
    WorktreeShowResult,
)
from dovo.core.worktree.services.delete import collect_worktree_delete
from dovo.core.worktree.services.detector import WorktreeDetector
from dovo.core.worktree.services.lifecycle import WorktreeLifecycle
from dovo.core.worktree.services.list import collect_worktree_list
from dovo.core.worktree.services.patch import WorktreePatch
from dovo.core.worktree.services.pruner import WorktreePruner
from dovo.core.worktree.services.show import collect_worktree_show


class Worktree:
    """Unified entrypoint for Git worktrees, lifecycle, and diff/patch application."""

    def __init__(
        self,
        paths: WorkspacePaths,
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
        self.lifecycle = WorktreeLifecycle(self.paths, self.db)
        self.patch = WorktreePatch(self.paths, self.db, lifecycle=self.lifecycle)

    @property
    def worktree_base_dir(self) -> Path:
        """Base storage directory for created worktrees."""
        return self.lifecycle.worktree_base_dir

    def list(self, status: WorktreeStatus | str | None = None) -> WorktreeListResult:
        """List tracked worktrees with lifecycle status, reconciling stale directories."""
        return collect_worktree_list(self.paths, self.db, status)

    def show(self, worktree_id: str) -> WorktreeShowResult:
        """Show details for one tracked worktree, reconciling stale active rows."""
        return collect_worktree_show(self.paths, self.db, worktree_id)

    def delete(self, worktree_id: str) -> WorktreeDeleteResult:
        """Inspect worktree row and disk state for deletion without mutating."""
        return collect_worktree_delete(self.paths, self.db, worktree_id=worktree_id)

    def create(
        self,
        session_id: str | None = None,
        *,
        include_wip: bool = False,
        name: str | None = None,
        base_ref: str | None = None,
    ) -> WorktreeCreateResult:
        """Create an isolated worktree and return structured result."""
        with WorkspaceLock(self.paths.lock_file):
            return self.lifecycle.create(
                session_id=session_id,
                include_wip=include_wip,
                name=name,
                base_ref=base_ref,
            )

    def cleanup(
        self,
        session: WorktreeSession | WorktreeRecord,
        *,
        force: bool = True,
    ) -> list[str]:
        """Remove worktree, delete throwaway branch, and prune."""
        with WorkspaceLock(self.paths.lock_file):
            return self.lifecycle.cleanup(session, force=force)

    def prune(
        self,
        *,
        dry_run: bool = False,
        force: bool = False,
    ) -> WorktreePruneResult:
        """Safely prune stale worktrees, orphaned directories, and temporary branches."""
        pruner = WorktreePruner(self.path, self.db, sessions_db=self.sessions_db)
        return pruner.prune(dry_run=dry_run, force=force)

    def prune_git_worktrees(self) -> None:
        """Prune stale Git worktree registrations."""
        with WorkspaceLock(self.paths.lock_file):
            self.lifecycle.prune()

    def get_active(self) -> list[Path]:
        """List active worktree directories."""
        return self.lifecycle.get_active()

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
        """Apply worktree changes back to main workspace."""
        with WorkspaceLock(self.paths.lock_file):
            return self.patch.apply(
                worktree_id=worktree_id,
                strategy=strategy,
                allow_dirty=allow_dirty,
                dry_run=dry_run,
                delete=delete,
                message=message,
            )

    def diff(
        self,
        worktree_id: str,
        *,
        stat: bool = False,
    ) -> WorktreeDiffResult:
        """Inspect unified diff or diffstat for a worktree."""
        return self.patch.diff(worktree_id=worktree_id, stat=stat)

    def detect(self) -> WorktreeDetectionResult:
        """Scan repository for stale worktrees, orphaned directories, and dead refs."""
        detector = WorktreeDetector(self.path, self.db, sessions_db=self.sessions_db)
        return detector.detect()
