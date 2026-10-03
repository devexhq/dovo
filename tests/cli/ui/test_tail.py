"""Contract tests for CollapsingTailDisplay streaming, capacity, and erasure."""

from __future__ import annotations

import io

import pytest
from rich.console import Console
from rich.text import Text

from dovo.cli.ui.events import StepDoneEvent, StepOutputEvent, StepStartEvent
from dovo.cli.ui.tail import _CURSOR_UP, _ERASE_LINE, CollapsingTailDisplay


def _display(*, tail_size: int = 2) -> tuple[CollapsingTailDisplay, io.StringIO, io.StringIO]:
    """Build a CollapsingTailDisplay with inspectable console and stream buffers."""
    console_buffer = io.StringIO()
    console = Console(file=console_buffer, force_terminal=False, color_system=None, width=100)
    stream = io.StringIO()
    return CollapsingTailDisplay(console, tail_size=tail_size, _stream=stream), console_buffer, stream


class CollapsingTailDisplayTests:
    """Contract tests for CollapsingTailDisplay."""

    def test_handle_step_output_bounds_tail_to_capacity(self) -> None:
        """CollapsingTailDisplay.handle_step_output: a 3rd line at tail_size=2 drops the oldest line from the final _stream reprint."""
        display, _, stream = _display(tail_size=2)

        display.handle_step_output(StepOutputEvent(step_id="s1", line="L1"))
        display.handle_step_output(StepOutputEvent(step_id="s1", line="L2"))
        display.handle_step_output(StepOutputEvent(step_id="s1", line="L3"))

        assert stream.getvalue().endswith("L2\nL3\n")
        assert "L1" not in stream.getvalue().rsplit("L2\nL3\n", maxsplit=1)[-1]

    def test_handle_step_start_erases_prior_tail_and_prints_banner(self) -> None:
        """CollapsingTailDisplay.handle_step_start: erases the prior tail via _CURSOR_UP+_ERASE_LINE on _stream and prints '[idx/total] name (command)' to console."""
        display, console_buffer, stream = _display(tail_size=2)
        display.handle_step_output(StepOutputEvent(step_id="s1", line="L1"))
        display.handle_step_output(StepOutputEvent(step_id="s1", line="L2"))

        display.handle_step_start(StepStartEvent(idx=1, total=3, step_id="s1", name="Build", command="make"))

        assert stream.getvalue().endswith((_CURSOR_UP + _ERASE_LINE) * 2)
        assert "[1/3] Build (make)" in console_buffer.getvalue()

    @pytest.mark.parametrize(
        ("ok", "expected_glyph"),
        [
            pytest.param(True, "✔", id="completed"),
            pytest.param(False, "✖", id="failed"),
        ],
    )
    def test_handle_step_done_erases_tail_and_prints_summary(self, ok: bool, expected_glyph: str) -> None:
        """CollapsingTailDisplay.handle_step_done: prints a ✔/✖ summary line to console matching event.ok, erasing the tail on _stream."""
        display, console_buffer, stream = _display(tail_size=2)
        display.handle_step_output(StepOutputEvent(step_id="s1", line="L1"))

        display.handle_step_done(
            StepDoneEvent(idx=1, total=1, step_id="s1", ok=ok, exit_code=0 if ok else 1, duration_seconds=1.23)
        )

        assert stream.getvalue().endswith((_CURSOR_UP + _ERASE_LINE) * 1)
        rendered = console_buffer.getvalue()
        assert expected_glyph in rendered
        assert "[1/1] s1" in rendered

    def test_print_above_prints_renderable_and_reprints_tail(self) -> None:
        """CollapsingTailDisplay.print_above: prints the renderable to console and erases-then-reprints the active tail on _stream."""
        display, console_buffer, stream = _display(tail_size=2)
        display.handle_step_output(StepOutputEvent(step_id="s1", line="L1"))
        display.handle_step_output(StepOutputEvent(step_id="s1", line="L2"))

        display.print_above(Text("Sandbox: Active (/tmp/x)"))

        assert "Sandbox: Active (/tmp/x)" in console_buffer.getvalue()
        assert stream.getvalue().endswith("L1\nL2\n")
