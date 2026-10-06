"""Shared fixtures for engine/services/ tests."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.db import SessionsRepository, SessionStatus
from dovo.engine.models import FailurePrompter, RunObserver, RunOutcome


@pytest.fixture
def stub_drive_run(
    monkeypatch: pytest.MonkeyPatch, engine_paths: WorkspacePaths
) -> Callable[[RunOutcome | None], None]:
    """Replace drive_run so Engine persistence runs without executing steps; the callable sets the returned outcome."""

    def _install(outcome: RunOutcome | None = None) -> None:
        result = outcome or RunOutcome(status=SessionStatus.COMPLETED, worktree_path=engine_paths.root_dir)

        def fake_drive_run(
            paths: WorkspacePaths,
            sessions: SessionsRepository,
            session_id: str,
            *,
            observer: RunObserver | None,
            prompter: FailurePrompter | None,
            no_tty: bool,
        ) -> RunOutcome:
            return result.model_copy(update={"session_id": session_id})

        monkeypatch.setattr("dovo.engine.engine.drive_run", fake_drive_run)

    return _install
