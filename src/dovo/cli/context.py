from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Self

from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.cli.ui.events import LockWaitEvent
from dovo.common.filesystem import Filesystem, WorkspacePaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.common.lock import WorkspaceLock
from dovo.core.bootstrap import initialize_workspace
from dovo.core.config import Config
from dovo.core.config.models import DovoConfig
from dovo.core.db.db import DovoDb
from dovo.core.project.services.storage import resolve_workspace_paths


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
    """Auto-initialize the workspace when no project identity exists yet under fs.repository_paths.dovo_dir."""
    if (fs.dovo_dir / "project.json").exists():
        return
    initialize_workspace(fs.root_dir)


@dataclass(frozen=True)
class CliContext:
    """Core environment state for Dovo CLI."""

    paths: WorkspacePaths
    db: DovoDb
    config: DovoConfig | None = None

    @classmethod
    def build(cls, *, path: Path | None = None, load_config: bool = True) -> Self:
        """Factory to build the global CLI state."""
        filesystem = Filesystem(path)
        repository_paths = filesystem.repository_paths
        global_paths = resolve_global_paths()
        paths = resolve_workspace_paths(repository_paths, global_paths)
        config = Config.load_required(paths) if load_config else None
        db = DovoDb(paths.database_file, project_id=paths.project_id)
        WorkspaceLock.set_default_on_wait(default_lock_wait_notifier)
        return cls(paths=paths, db=db, config=config)
