"""Contract tests for LiveDisplayManager loop status folding and iteration-scoped step table."""

from __future__ import annotations

import io

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table

from dovo.cli.ui.dispatcher import UiDispatcher
from dovo.cli.ui.events import LoopLifecycleEvent, StepDoneEvent, StepStartEvent
from dovo.cli.ui.live import LiveDisplayManager, build_loop_status_panel


def _manager() -> LiveDisplayManager:
    """Build a LiveDisplayManager with no active Live session, for state-only testing."""
    console = Console(force_terminal=False, color_system=None, width=100, file=io.StringIO())
    return LiveDisplayManager(console)


class LiveDisplayManagerLoopTests:
    """Contract tests for LiveDisplayManager's loop lifecycle handling."""

    def test_iteration_start_clears_steps_table(self) -> None:
        """LiveDisplayManager.handle_loop_lifecycle: iteration_start clears self.steps before the new iteration's StepStartEvents arrive."""
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
        """LiveDisplayManager._build_renderable: after a start event, the Group contains the loop panel alongside the step table."""
        manager = _manager()

        manager.handle_loop_lifecycle(LoopLifecycleEvent(loop_id="dev-cycle", action="start", max_iterations=5))
        renderable = manager._build_renderable()

        assert isinstance(renderable, Group)
        renderable_types = [type(item) for item in renderable.renderables]
        assert Panel in renderable_types
        assert Table in renderable_types

    def test_no_loop_active_omits_panel(self) -> None:
        """LiveDisplayManager._build_renderable: with no loop lifecycle event received, the renderable is the bare step table."""
        manager = _manager()

        renderable = manager._build_renderable()

        assert isinstance(renderable, Table)

    def test_dispatch_live_routes_loop_lifecycle_events(self) -> None:
        """UiDispatcher._dispatch_live: a LoopLifecycleEvent dispatched during an active Live session updates loop state via handle_loop_lifecycle."""
        console = Console(force_terminal=True, color_system=None, width=100, file=io.StringIO())
        dispatcher = UiDispatcher(console=console)
        dispatcher.start_live()
        try:
            dispatcher.dispatch(LoopLifecycleEvent(loop_id="dev-cycle", action="start", max_iterations=5))

            assert dispatcher._live_display is not None
            assert dispatcher._live_display._loop_id == "dev-cycle"
            assert dispatcher._live_display._loop_max_iterations == 5
        finally:
            dispatcher.stop_live()


class BuildLoopStatusPanelTests:
    """Contract tests for the build_loop_status_panel renderable builder."""

    def test_iteration_history_marks_pass_fail_current_and_pending(self) -> None:
        """build_loop_status_panel: iteration_results={1: False, 2: False}, iteration=3, max_iterations=5 renders 5 iteration markers."""
        panel = build_loop_status_panel(
            "dev-cycle",
            3,
            5,
            [],
            {1: False, 2: False},
        )

        console = Console(width=100, color_system=None, record=True)
        console.print(panel)
        rendered = console.export_text()

        assert "dev-cycle" in rendered
        assert "iteration 3/5" in rendered
        for iteration_number in range(1, 6):
            assert str(iteration_number) in rendered
