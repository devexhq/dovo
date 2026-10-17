"""Contract tests for observer notification dispatch."""

from __future__ import annotations

from dovo.core.catalog.definitions import StepDefinition
from dovo.engine.notify import safe_notify
from tests.harness.builders import StepBuilder
from tests.harness.sessions import NoOpRunObserver


class _ExplodingOnStepStartObserver(NoOpRunObserver):
    """Test double raising from on_step_start."""

    def on_step_start(self, idx: int, total: int, step: StepDefinition) -> None:
        raise RuntimeError("step start exploded")


class _RecordingOnStepStartObserver(NoOpRunObserver):
    """Test double recording the exact args and kwargs on_step_start was invoked with."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def on_step_start(self, *args: object, **kwargs: object) -> None:
        self.calls.append((args, kwargs))


class SafeNotifyTests:
    def test_none_observer_is_noop(self) -> None:
        step = StepBuilder.command("echo test").build()

        result = safe_notify(None, "on_step_start", 1, 2, step)

        assert result is None

    def test_observer_missing_method_is_noop(self) -> None:
        observer = NoOpRunObserver()

        result = safe_notify(observer, "on_some_hook_not_in_the_protocol")

        assert result is None

    def test_observer_method_exception_is_swallowed(self) -> None:
        observer = _ExplodingOnStepStartObserver()
        step = StepBuilder.command("echo test").build()

        result = safe_notify(observer, "on_step_start", 1, 2, step)

        assert result is None

    def test_observer_method_called_with_exact_args_and_kwargs(self) -> None:
        observer = _RecordingOnStepStartObserver()
        step = StepBuilder.command("echo test").build()

        safe_notify(observer, "on_step_start", 1, 2, step, stream="stdout")

        assert observer.calls == [((1, 2, step), {"stream": "stdout"})]
