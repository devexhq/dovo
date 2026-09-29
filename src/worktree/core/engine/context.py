"""Infrastructure context for one run's execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from worktree.common.filesystem import WorkspacePaths
from worktree.core.db import ArtifactsRepository
from worktree.core.sandbox.models import SandboxSession


@dataclass(frozen=True)
class RunSessionContext:
    """Infrastructure resources for one run's execution; durable progress lives only in ExecutionStateTree."""

    session_id: str
    paths: WorkspacePaths
    target_dir: Path
    session_tmp_dir: Path | None
    session_log_dir: Path | None
    artifacts_dir: Path | None
    artifacts_db: ArtifactsRepository | None = None
    sandbox: SandboxSession | None = None
    no_tty: bool = False
    save_attempt_logs: bool = True
