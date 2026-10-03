"""Central UI dispatcher for CLI domain data presentation."""

import json
import sys
from collections.abc import Callable
from typing import Any, TypeVar, overload

from rich.console import Console

from dovo.cli.ui.events import (
    LoopLifecycleEvent,
    PromptEvent,
    StepDoneEvent,
    StepOutputEvent,
    StepStartEvent,
    WorktreeLifecycleEvent,
)
from dovo.cli.ui.live import LiveDisplayManager
from dovo.cli.ui.tail import CollapsingTailDisplay
from dovo.common.types import ComponentFormatter

T = TypeVar("T")
F = TypeVar("F", bound=ComponentFormatter[Any] | type[ComponentFormatter[Any]])


class UiDispatcher:
    """Central UI dispatcher routing domain data to appropriate formatters."""

    def __init__(self, console: Console | None = None, output_format: str = "terminal") -> None:
        """Initialize dispatcher.

        Args:
            console: Rich Console instance to use for terminal output. Defaults to None.
            output_format: Default output format ("terminal" or "json"). Defaults to "terminal".
        """
        self._custom_console: Console | None = console
        self._registry: dict[type[Any], ComponentFormatter[Any]] = {}
        self._output_format: str = output_format
        self._live_display: LiveDisplayManager | None = None
        self._tail_display: CollapsingTailDisplay | None = None
        self._register_default_formatters()

    def _register_default_formatters(self) -> None:
        """Register all default component formatters."""
        from dovo.cli.ui.formatters import register_all_formatters

        register_all_formatters(self, console=self._custom_console)

    @property
    def _console(self) -> Console:
        """Get the active Console instance."""
        if self._custom_console is not None:
            return self._custom_console
        return Console()

    @property
    def is_interactive(self) -> bool:
        """Whether the active console output is connected to an interactive terminal (TTY)."""
        return self._console.is_terminal

    @property
    def is_terminal_format(self) -> bool:
        """Whether the active output format is terminal."""
        return self._output_format == "terminal"

    @property
    def output_format(self) -> str:
        """Get current output format."""
        return self._output_format

    def set_output_format(self, output_format: str) -> None:
        """Set output format ("terminal" or "json").

        Args:
            output_format: Presentation format ("terminal" or "json").
        """
        self._output_format = output_format

    @overload
    def register(
        self,
        model_cls: type[T],
        formatter: ComponentFormatter[T] | type[ComponentFormatter[T]],
    ) -> ComponentFormatter[T] | type[ComponentFormatter[T]]: ...

    @overload
    def register(
        self,
        model_cls: type[T],
        formatter: None = None,
    ) -> Callable[[F], F]: ...

    def register(
        self,
        model_cls: type[Any],
        formatter: ComponentFormatter[Any] | type[ComponentFormatter[Any]] | None = None,
    ) -> Any:
        """Register a formatter for a domain data model class.

        Can be called directly or used as a decorator.

        Args:
            model_cls: Domain data model class to register.
            formatter: Formatter instance or class. If None, returns a decorator.

        Returns:
            The registered formatter or a decorator function.
        """

        def decorator(fmt: F) -> F:
            fmt_inst = fmt() if isinstance(fmt, type) else fmt
            self._registry[model_cls] = fmt_inst
            return fmt

        if formatter is not None:
            return decorator(formatter)
        return decorator

    def dispatch(self, data: Any, output_format: str | None = None) -> None:
        """Dispatch domain data to registered formatter according to output format.

        Args:
            data: Domain data object to render.
            output_format: Presentation format ("terminal" or "json"). Defaults to None.

        Raises:
            ValueError: If no formatter is registered for the data's type.
        """
        effective_format = output_format if output_format is not None else self._output_format
        data_type = type(data)
        formatter = self._registry.get(data_type)
        if formatter is None:
            raise ValueError(f"No formatter registered for type: {data_type.__name__}")

        if effective_format == "json":
            envelope = {
                "event_type": data_type.__name__,
                "payload": formatter.to_json_serializable(data),
            }
            sys.stdout.write(json.dumps(envelope) + "\n")
            sys.stdout.flush()
        elif effective_format == "raw":
            sys.stdout.write(formatter.to_raw(data))
            sys.stdout.flush()
        elif not self._dispatch_interactive(data, formatter):
            rich_renderable = formatter.to_rich(data)
            self._console.print(rich_renderable)

    def _dispatch_interactive(self, data: Any, formatter: ComponentFormatter[Any]) -> bool:
        """Route event to active live or collapsing-tail display if one is running."""
        if self._live_display is not None and self._live_display.is_active:
            if isinstance(data, PromptEvent):
                self.stop_live()
                self._console.print(formatter.to_rich(data))
            else:
                self._dispatch_live(data, formatter)
            return True

        if self._tail_display is not None:
            if isinstance(data, PromptEvent):
                self.stop_collapsing_tail()
                self._console.print(formatter.to_rich(data))
            else:
                self._dispatch_collapsing_tail(data, formatter)
            return True

        return False

    def start_live(self) -> None:
        """Start interactive live display if terminal output format is active."""
        if self.is_terminal_format and self.is_interactive and self._live_display is None:
            self._live_display = LiveDisplayManager(self._console)
            self._live_display.start()

    def stop_live(self) -> None:
        """Stop active interactive live display."""
        if self._live_display is not None:
            self._live_display.stop()
            self._live_display = None

    def start_collapsing_tail(self) -> None:
        """Start the ANSI collapsing-tail display if terminal output format is active."""
        if not self.is_terminal_format or not self.is_interactive or self._tail_display is not None:
            return
        self._tail_display = CollapsingTailDisplay(self._console)

    def stop_collapsing_tail(self) -> None:
        """Stop the active collapsing-tail display."""
        self._tail_display = None

    def _dispatch_collapsing_tail(self, data: object, formatter: ComponentFormatter[Any]) -> None:
        """Route events through the active collapsing-tail display.

        Args:
            data: The event object being dispatched.
            formatter: The registered ComponentFormatter for data's type.
        """
        if self._tail_display is None:
            return
        if isinstance(data, StepStartEvent):
            self._tail_display.handle_step_start(data)
        elif isinstance(data, StepOutputEvent):
            self._tail_display.handle_step_output(data)
        elif isinstance(data, StepDoneEvent):
            self._tail_display.handle_step_done(data)
        elif isinstance(data, WorktreeLifecycleEvent):
            self._tail_display.handle_worktree(data, formatter.to_rich(data))
        else:
            self._tail_display.print_above(formatter.to_rich(data))

    def _dispatch_live(self, data: Any, formatter: ComponentFormatter[Any]) -> None:
        """Route events through active live display session."""
        if self._live_display is None:
            return
        if isinstance(data, StepStartEvent):
            self._live_display.handle_step_start(data)
        elif isinstance(data, StepOutputEvent):
            self._live_display.handle_step_output(data)
        elif isinstance(data, StepDoneEvent):
            self._live_display.handle_step_done(data)
        elif isinstance(data, WorktreeLifecycleEvent):
            self._live_display.handle_worktree(data, formatter.to_rich(data))
        elif isinstance(data, LoopLifecycleEvent):
            self._live_display.handle_loop_lifecycle(data)
        else:
            self._live_display.print_above(formatter.to_rich(data))


ui_dispatcher = UiDispatcher()
