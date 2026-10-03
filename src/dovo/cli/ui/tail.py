# src/dovo/cli/ui/tail.py
"""ANSI collapsing-tail display for streaming step output."""

from __future__ import annotations

import sys
from collections import deque
from typing import IO

from rich.console import Console
from rich.text import Text

from dovo.cli.ui.events import SandboxLifecycleEvent, StepDoneEvent, StepOutputEvent, StepStartEvent

DEFAULT_TAIL_SIZE = 10
_ERASE_LINE = "\033[2K\r"
_CURSOR_UP = "\033[1A"


class CollapsingTailDisplay:
    """ANSI streaming display that collapses the active output tail on step completion.

    On step start: prints a permanent banner line.
    On each output line: appends to a ring buffer and reprints only the tail block
    (erase + reprint), so history above the tail is never disturbed.
    On step done: erases the tail block and prints a permanent summary line.
    """

    def __init__(
        self,
        console: Console,
        *,
        tail_size: int = DEFAULT_TAIL_SIZE,
        _stream: IO[str] | None = None,
    ) -> None:
        """Initialize the display.

        Args:
            console: Rich Console for permanent (non-erased) output.
            tail_size: Maximum number of tail lines shown during a step.
            _stream: Output stream for ANSI erase/reprint sequences.
                Defaults to sys.stderr. Tests inject a StringIO here.
        """
        self.console = console
        self.tail_size = tail_size
        self._stream: IO[str] = _stream if _stream is not None else sys.stderr
        self._tail: deque[str] = deque(maxlen=tail_size)
        self._tail_height: int = 0

    def handle_step_start(self, event: StepStartEvent) -> None:
        """Print a permanent step-start banner and clear the tail buffer.

        Args:
            event: StepStartEvent describing the beginning step.
        """
        self._erase_tail()
        self._tail.clear()
        self._tail_height = 0
        step_label = event.name or event.step_id
        command_suffix = f" ({event.command})" if event.command is not None else ""
        banner = Text(f"[{event.idx}/{event.total}] {step_label}{command_suffix}")
        self.console.print(banner)

    def handle_step_output(self, event: StepOutputEvent) -> None:
        """Append a line to the tail ring buffer and reprint the tail block.

        Args:
            event: StepOutputEvent carrying the output line.
        """
        self._erase_tail()
        self._tail.append(event.line.rstrip("\r\n"))
        self._reprint_tail()

    def handle_step_done(self, event: StepDoneEvent) -> None:
        """Erase the tail block and print a permanent summary line.

        Args:
            event: StepDoneEvent carrying completion status and duration.
        """
        self._erase_tail()
        self._tail.clear()
        self._tail_height = 0
        if event.ok:
            duration = event.duration_seconds if event.duration_seconds is not None else 0.0
            summary = Text.from_markup(f"[bold green]✔[/] [{event.idx}/{event.total}] {event.step_id}  {duration:.2f}s")
        else:
            detail = event.error_message or f"exit code {event.exit_code}"
            summary = Text.from_markup(f"[bold red]✖[/] [{event.idx}/{event.total}] {event.step_id}  {detail}")
        self.console.print(summary)

    def handle_sandbox(self, event: SandboxLifecycleEvent, rendered: Text) -> None:
        """Print a sandbox lifecycle notice above any active tail.

        Args:
            event: SandboxLifecycleEvent (used to update sandbox info if needed).
            rendered: Pre-rendered Text from SandboxLifecycleFormatter.
        """
        del event
        self._erase_tail()
        self.console.print(rendered)
        self._reprint_tail()

    def print_above(self, renderable: object) -> None:
        """Print a Rich renderable above the current tail without losing tail context.

        Args:
            renderable: Any Rich-renderable object.
        """
        self._erase_tail()
        self.console.print(renderable)
        self._reprint_tail()

    def _erase_tail(self) -> None:
        """Erase self._tail_height lines from the terminal and reset the counter."""
        if self._tail_height > 0:
            self._stream.write((_CURSOR_UP + _ERASE_LINE) * self._tail_height)
            self._stream.flush()
        self._tail_height = 0

    def _reprint_tail(self) -> None:
        """Write every line in self._tail to self._stream and update self._tail_height."""
        for line in self._tail:
            self._stream.write(f"{line}\n")
        self._stream.flush()
        self._tail_height = len(self._tail)
