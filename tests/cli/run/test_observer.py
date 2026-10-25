"""Contract tests for DispatcherRunObserver worktree cleanup events."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.cli.run.observer import DispatcherRunObserver
from dovo.cli.ui.dispatcher import UiDispatcher
from dovo.cli.ui.events import AgentProgressEvent, AgentSummaryView, StepOutputEvent, WorktreeLifecycleEvent
from dovo.common.models import Remediation
from dovo.core.agents import AgentProgressKind
from dovo.core.catalog.definitions import StepDefinition
from dovo.engine.executors.models import AgentProgressUpdate
from tests.harness.builders import StepBuilder


class _RecordingDispatcher(UiDispatcher):
    """UiDispatcher that records dispatched events instead of rendering them."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[object] = []

    def dispatch(self, data: object, *args: object, **kwargs: object) -> None:
        self.events.append(data)


_SUMMARY_LINE = '{"status":"unfixable","summary":null,"unfixable_reason":"no network","touched_files":["a.py"]}'
_SUMMARY_VIEW = AgentSummaryView(
    status="unfixable", succeeded=False, unfixable_reason="no network", touched_files=["a.py"]
)


class DispatcherRunObserverOutputTests:
    """Dispatch of step output events by DispatcherRunObserver.on_step_output."""

    @pytest.mark.parametrize(
        ("step", "line", "stream", "expected_summary"),
        [
            pytest.param(
                StepBuilder.agent("plan").with_id("plan").build(),
                _SUMMARY_LINE + "\n",
                "stdout",
                _SUMMARY_VIEW,
                id="agent_step_summary_line",
            ),
            pytest.param(
                StepBuilder.agent("plan").with_id("plan").build(),
                '{"status":"ok"}',
                "stdout",
                None,
                id="agent_step_json_that_is_not_a_summary",
            ),
            pytest.param(
                StepBuilder.agent("plan").with_id("plan").build(),
                _SUMMARY_LINE,
                "stderr",
                None,
                id="agent_step_summary_shaped_stderr_line",
            ),
            pytest.param(
                StepBuilder.command("echo hi").with_id("plan").build(),
                _SUMMARY_LINE,
                "stdout",
                None,
                id="command_step_summary_shaped_line",
            ),
        ],
    )
    def test_summary_is_attached_only_to_an_agent_step_stdout_summary_line(
        self, step: StepDefinition, line: str, stream: str, expected_summary: AgentSummaryView | None
    ) -> None:
        dispatcher = _RecordingDispatcher()

        DispatcherRunObserver(dispatcher).on_step_output(1, 1, step, line, stream)

        assert dispatcher.events == [
            StepOutputEvent(step_id="plan", line=line.rstrip("\n"), stream=stream, agent_summary=expected_summary)
        ]


class DispatcherRunObserverProgressTests:
    """Dispatch of agent progress events by DispatcherRunObserver.on_step_progress."""

    def test_progress_update_is_dispatched_as_an_agent_progress_event(self) -> None:
        dispatcher = _RecordingDispatcher()
        step = StepBuilder.agent("plan").with_id("plan").build()
        update = AgentProgressUpdate(
            kind=AgentProgressKind.RESULT,
            target="a\nb",
            call_kind=AgentProgressKind.SHELL,
            line_count=9,
            is_error=True,
        )

        DispatcherRunObserver(dispatcher).on_step_progress(1, 1, step, update)

        assert dispatcher.events == [
            AgentProgressEvent(
                step_id="plan",
                kind=AgentProgressKind.RESULT,
                target="a\nb",
                call_kind=AgentProgressKind.SHELL,
                line_count=9,
                is_error=True,
            )
        ]


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

        remediation = Remediation(
            message="Remove it with 'dovo worktree delete run-1'", command="dovo worktree delete run-1"
        )

        DispatcherRunObserver(dispatcher).on_worktree_cleanup(
            kept=True, path=tmp_path / "run-1", worktree_id="run-1", branch="dovo/run-1", remediations=[remediation]
        )

        [event] = dispatcher.events
        assert isinstance(event, WorktreeLifecycleEvent)
        assert (event.action, event.kept, event.worktree_id, event.branch) == ("cleanup", True, "run-1", "dovo/run-1")
        assert event.remediations == [remediation]
