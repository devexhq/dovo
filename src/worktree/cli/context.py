from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Self

from worktree.cli.ui.dispatcher import ui_dispatcher
from worktree.cli.ui.events import LockWaitEvent
from worktree.common.filesystem import Filesystem, WorkspacePaths
from worktree.common.filesystem.services.global_root import resolve_global_paths
from worktree.common.lock import WorkspaceLock
from worktree.core.bootstrap import initialize_workspace
from worktree.core.config import Config
from worktree.core.config.models import WorktreeConfig
from worktree.core.db.db import WorktreeDb
from worktree.core.project.services.storage import resolve_workspace_paths


def default_lock_wait_notifier(lock_path: Path, holder_pid: str | None, timeout_seconds: float) -> None:
    """Dispatch LockWaitEvent through UI dispatcher when lock contention occurs."""
    ui_dispatcher.dispatch(
        LockWaitEvent(
            lock_path=str(lock_path),
            holder_pid=holder_pid,
            timeout_seconds=timeout_seconds,
        )
    )


def ensure_lazy_project_init(fs: Filesystem) -> None:
    """Auto-initialize the workspace when no project identity exists yet under fs.repository_paths.worktree_dir."""
    if (fs.worktree_dir / "project.json").exists():
        return
    initialize_workspace(fs.root_dir)


@dataclass(frozen=True)
class CliContext:
    """Core environment state for Worktree CLI."""

    paths: WorkspacePaths
    db: WorktreeDb
    config: WorktreeConfig | None = None

    @classmethod
    def build(cls, *, path: Path | None = None, load_config: bool = True) -> Self:
        """Factory to build the global CLI state."""
        filesystem = Filesystem(path)
        repository_paths = filesystem.repository_paths
        global_paths = resolve_global_paths()
        paths = resolve_workspace_paths(repository_paths, global_paths)
        config = Config.load_required(paths) if load_config else None
        db = WorktreeDb(paths.database_file, project_id=paths.project_id)
        WorkspaceLock.set_default_on_wait(default_lock_wait_notifier)
        return cls(paths=paths, db=db, config=config)
