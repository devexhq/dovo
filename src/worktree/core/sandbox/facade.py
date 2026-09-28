"""Sandbox domain facade."""

from __future__ import annotations

from pathlib import Path

from worktree.common.filesystem import WorkspacePaths
from worktree.common.lock import WorkspaceLock
from worktree.core.config.models import WorktreeConfig
from worktree.core.db import RunsRepository, SandboxesRepository, SandboxRecord, SandboxStatus
from worktree.core.sandbox.models import (
    SandboxApplyResult,
    SandboxApplyStrategy,
    SandboxCreateResult,
    SandboxDeleteResult,
    SandboxDetectionResult,
    SandboxDiffResult,
    SandboxListResult,
    SandboxPruneResult,
    SandboxSession,
    SandboxShowResult,
)
from worktree.core.sandbox.services.delete import collect_sandbox_delete
from worktree.core.sandbox.services.detector import SandboxDetector
from worktree.core.sandbox.services.lifecycle import SandboxLifecycle
from worktree.core.sandbox.services.list import collect_sandbox_list
from worktree.core.sandbox.services.patch import SandboxPatch
from worktree.core.sandbox.services.pruner import SandboxPruner
from worktree.core.sandbox.services.show import collect_sandbox_show


class Sandbox:
    """Unified entrypoint for Git worktree sandboxes, lifecycle, and diff/patch application."""

    def __init__(
        self,
        paths: WorkspacePaths,
        db: SandboxesRepository | None = None,
        runs_db: RunsRepository | None = None,
    ) -> None:
        self.paths = paths
        self.path = paths.root_dir
        self.cwd = self.path
        self.db = (
            db if db is not None else SandboxesRepository(db_path=paths.database_file, project_id=paths.project_id)
        )
        self.runs_db = runs_db
        self.lifecycle = SandboxLifecycle(self.paths, self.db)
        self.patch = SandboxPatch(self.paths, self.db, lifecycle=self.lifecycle)

    @property
    def config(self) -> WorktreeConfig:
        """Return the loaded workspace config."""
        return self.lifecycle.config

    @property
    def sandbox_base_dir(self) -> Path:
        """Base storage directory for created sandboxes."""
        return self.lifecycle.sandbox_base_dir

    def list(self, status: SandboxStatus | str | None = None) -> SandboxListResult:
        """List tracked sandboxes with lifecycle status, reconciling stale directories."""
        return collect_sandbox_list(self.paths, self.db, status)

    def show(self, sandbox_id: str) -> SandboxShowResult:
        """Show details for one tracked sandbox, reconciling stale active rows."""
        return collect_sandbox_show(self.paths, self.db, sandbox_id)

    def delete(self, sandbox_id: str) -> SandboxDeleteResult:
        """Inspect sandbox row and disk state for deletion without mutating."""
        return collect_sandbox_delete(self.paths, self.db, sandbox_id=sandbox_id)

    def create(
        self,
        session_id: str | None = None,
        *,
        include_wip: bool = False,
        name: str | None = None,
        base_ref: str | None = None,
    ) -> SandboxCreateResult:
        """Create an isolated sandbox worktree and return structured result."""
        with WorkspaceLock(self.paths.lock_file):
            return self.lifecycle.create(
                session_id=session_id,
                include_wip=include_wip,
                name=name,
                base_ref=base_ref,
            )

    def cleanup(
        self,
        session: SandboxSession | SandboxRecord,
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
    ) -> SandboxPruneResult:
        """Safely prune stale sandboxes, orphaned directories, and temporary branches."""
        pruner = SandboxPruner(self.path, self.db, runs_db=self.runs_db)
        return pruner.prune(dry_run=dry_run, force=force)

    def prune_git_worktrees(self) -> None:
        """Prune stale Git worktree registrations."""
        with WorkspaceLock(self.paths.lock_file):
            self.lifecycle.prune()

    def get_active(self) -> list[Path]:
        """List active sandbox directories."""
        return self.lifecycle.get_active()

    def apply(
        self,
        sandbox_id: str,
        *,
        strategy: SandboxApplyStrategy = SandboxApplyStrategy.PATCH,
        allow_dirty: bool = False,
        dry_run: bool = False,
        delete: bool = False,
        message: str | None = None,
    ) -> SandboxApplyResult:
        """Apply sandbox changes back to main workspace."""
        with WorkspaceLock(self.paths.lock_file):
            return self.patch.apply(
                sandbox_id=sandbox_id,
                strategy=strategy,
                allow_dirty=allow_dirty,
                dry_run=dry_run,
                delete=delete,
                message=message,
            )

    def diff(
        self,
        sandbox_id: str,
        *,
        stat: bool = False,
    ) -> SandboxDiffResult:
        """Inspect unified diff or diffstat for a sandbox."""
        return self.patch.diff(sandbox_id=sandbox_id, stat=stat)

    def detect(self) -> SandboxDetectionResult:
        """Scan repository for stale sandboxes, orphaned directories, and dead refs."""
        detector = SandboxDetector(self.path, self.db, runs_db=self.runs_db)
        return detector.detect()
