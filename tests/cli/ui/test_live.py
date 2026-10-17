"""Contract tests for LiveDisplayManager loop status folding and iteration-scoped step table."""

from __future__ import annotations

import io

from rich.console import Console

from dovo.cli.ui.dispatcher import UiDispatcher
from dovo.cli.ui.events import LoopLifecycleEvent, StepDoneEvent, StepStartEvent
from dovo.cli.ui.live import LiveDisplayManager, build_loop_status_panel
from tests.harness.formatter import render_rich


def _manager() -> LiveDisplayManager:
    """Build a LiveDisplayManager with no active Live session, for state-only testing."""
    console = Console(force_terminal=False, color_system=None, width=100, file=io.StringIO())
    return LiveDisplayManager(console)


def _terminal_console(output: io.StringIO) -> Console:
    """Build an interactive console whose Live frames are captured in output, since render_rich cannot drive Live."""
    return Console(force_terminal=True, color_system=None, width=160, file=output)


class LiveDisplayManagerLoopTests:
    """Contract tests for LiveDisplayManager's loop lifecycle handling."""

    def test_iteration_start_clears_steps_table(self) -> None:
        manager = _manager()
        manager.handle_step_start(
            StepStartEvent(idx=1, total=2, step_id="run-tests", name="run-tests", command="pytest")
        )
        manager.handle_step_done(StepDoneEvent(idx=1, total=2, step_id="run-tests", ok=False, exit_code=127))
        assert len(manager.steps) == 1

        manager.handle_loop_lifecycle(
            LoopLifecycleEvent(loop_id="dev-cycle", action="iteration_start", iteration=2, max_iterations=5)
        )

        assert manager.steps == []

    def test_loop_panel_included_in_single_renderable(self) -> None:
        output = io.StringIO()
        manager = LiveDisplayManager(_terminal_console(output))
        manager.start()

        manager.handle_loop_lifecycle(LoopLifecycleEvent(loop_id="dev-cycle", action="start", max_iterations=5))
        manager.handle_step_start(StepStartEvent(idx=1, total=1, step_id="run-tests", name="run-tests"))
        manager.stop()

        rendered = output.getvalue()
        assert "dev-cycle" in rendered
        assert "run-tests" in rendered

    def test_no_loop_active_omits_panel(self) -> None:
        output = io.StringIO()
        manager = LiveDisplayManager(_terminal_console(output))
        manager.start()
        manager.handle_step_start(StepStartEvent(idx=1, total=1, step_id="run-tests", name="run-tests"))
        manager.stop()

        rendered = output.getvalue()
        assert "run-tests" in rendered
        assert "Loop" not in rendered

    def test_dispatch_live_routes_loop_lifecycle_events(self) -> None:
        output = io.StringIO()
        dispatcher = UiDispatcher(console=_terminal_console(output))
        dispatcher.start_live()
        try:
            dispatcher.dispatch(LoopLifecycleEvent(loop_id="dev-cycle", action="start", max_iterations=5))
        finally:
            dispatcher.stop_live()

        assert "dev-cycle" in output.getvalue()


class BuildLoopStatusPanelTests:
    """Contract tests for the build_loop_status_panel renderable builder."""

    def test_iteration_history_marks_pass_fail_and_pending(self) -> None:
        panel = build_loop_status_panel(
            "dev-cycle",
            3,
            5,
            [],
            {1: True, 2: False},
        )

        rendered = render_rich(panel)

        assert "dev-cycle" in rendered
        assert "iteration 3/5" in rendered
        assert [marker for marker in ("1 ✔", "2 ✖", "3 ○", "4 ○", "5 ○") if marker not in rendered] == []
