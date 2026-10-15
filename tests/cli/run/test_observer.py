"""Contract tests for DispatcherRunObserver worktree cleanup events."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.cli.run.observer import DispatcherRunObserver
from dovo.cli.ui.dispatcher import UiDispatcher
from dovo.cli.ui.events import WorktreeLifecycleEvent


class _RecordingDispatcher(UiDispatcher):
    """UiDispatcher that records dispatched events instead of rendering them."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[object] = []

    def dispatch(self, data: object, *args: object, **kwargs: object) -> None:
        self.events.append(data)


class DispatcherRunObserverCleanupTests:
    """Dispatch of the worktree cleanup event by DispatcherRunObserver.on_worktree_cleanup."""

    @pytest.mark.parametrize(
        ("inside_cwd", "expected_path"),
        [
            pytest.param(True, ".dovo/worktrees/run-1", id="inside-cwd-relative"),
            pytest.param(False, None, id="outside-cwd-absolute"),
        ],
    )
    def test_cleanup_event_path_is_relative_only_inside_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inside_cwd: bool, expected_path: str | None
    ) -> None:
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        worktree_path = cwd / ".dovo" / "worktrees" / "run-1" if inside_cwd else tmp_path / "elsewhere" / "run-1"
        dispatcher = _RecordingDispatcher()

        DispatcherRunObserver(dispatcher).on_worktree_cleanup(kept=True, path=worktree_path)

        [event] = dispatcher.events
        assert isinstance(event, WorktreeLifecycleEvent)
        assert event.path == (expected_path or str(worktree_path))

    def test_cleanup_event_forwards_kept_id_and_branch(self, tmp_path: Path) -> None:
        dispatcher = _RecordingDispatcher()

        DispatcherRunObserver(dispatcher).on_worktree_cleanup(
            kept=True, path=tmp_path / "run-1", worktree_id="run-1", branch="dovo/run-1"
        )

        [event] = dispatcher.events
        assert isinstance(event, WorktreeLifecycleEvent)
        assert (event.action, event.kept, event.worktree_id, event.branch) == ("cleanup", True, "run-1", "dovo/run-1")
