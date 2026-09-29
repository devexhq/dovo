"""Contract tests for DispatcherFailurePrompter decision parsing and streaming order."""

from __future__ import annotations

import io
import json

import pytest
from rich.console import Console

from worktree.cli.run.prompter import DispatcherFailurePrompter
from worktree.cli.ui.dispatcher import UiDispatcher
from worktree.core.engine.models import FailurePromptDecision, LoopPromptDecision
from worktree.core.step import LoopStepBlock, StepDefinition, StepResult


def _buffered_dispatcher(*, force_terminal: bool, output_format: str = "terminal") -> tuple[UiDispatcher, io.StringIO]:
    """Build a UiDispatcher whose Console writes to an inspectable StringIO buffer."""
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=force_terminal, color_system=None, width=100)
    return UiDispatcher(console=console, output_format=output_format), buffer


def _failing_step() -> StepDefinition:
    return StepDefinition(id="s1", name="Build", run="true")


def _failing_result() -> StepResult:
    return StepResult(step_id="s1", status="failed", exit_code=1, stdout="", stderr="boom", duration_seconds=0.1)


def _loop_block() -> LoopStepBlock:
    return LoopStepBlock(id="loop1", type="loop", until=["true"], do=[StepDefinition(id="sub1", run="true")])


class DispatcherFailurePrompterTests:
    """Contract tests for DispatcherFailurePrompter."""

    @pytest.mark.parametrize(
        ("raw_input", "expected"),
        [
            pytest.param("r", FailurePromptDecision.RETRY, id="short_retry"),
            pytest.param("retry", FailurePromptDecision.RETRY, id="full_retry"),
            pytest.param("c", FailurePromptDecision.CONTINUE, id="short_continue"),
            pytest.param("continue", FailurePromptDecision.CONTINUE, id="full_continue"),
            pytest.param("a", FailurePromptDecision.ABORT, id="short_abort"),
            pytest.param("abort", FailurePromptDecision.ABORT, id="full_abort"),
            pytest.param("  R  ", FailurePromptDecision.RETRY, id="whitespace_and_uppercase_retry"),
        ],
    )
    def test_prompt_step_failure_terminal_valid_decisions(
        self, monkeypatch: pytest.MonkeyPatch, raw_input: str, expected: FailurePromptDecision
    ) -> None:
        """DispatcherFailurePrompter.prompt_step_failure: each valid raw input token resolves to its FailurePromptDecision."""
        dispatcher, _ = _buffered_dispatcher(force_terminal=True)
        prompter = DispatcherFailurePrompter(dispatcher)
        monkeypatch.setattr("builtins.input", lambda *_args, **_kwargs: raw_input)

        decision = prompter.prompt_step_failure(step=_failing_step(), result=_failing_result(), diagnostic="boom")

        assert decision == expected

    def test_prompt_step_failure_invalid_then_valid_reprompts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DispatcherFailurePrompter.prompt_step_failure: invalid input then 'c' returns CONTINUE and prints 'Invalid option'."""
        dispatcher, buffer = _buffered_dispatcher(force_terminal=True)
        prompter = DispatcherFailurePrompter(dispatcher)
        responses = iter(["zz", "c"])
        monkeypatch.setattr("builtins.input", lambda *_args, **_kwargs: next(responses))

        decision = prompter.prompt_step_failure(step=_failing_step(), result=_failing_result(), diagnostic="boom")

        assert decision == FailurePromptDecision.CONTINUE
        assert "Invalid option" in buffer.getvalue()

    def test_prompt_step_failure_eof_aborts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DispatcherFailurePrompter.prompt_step_failure: input() raising EOFError returns FailurePromptDecision.ABORT."""
        dispatcher, _ = _buffered_dispatcher(force_terminal=True)
        prompter = DispatcherFailurePrompter(dispatcher)

        def _raise_eof(*_args: object, **_kwargs: object) -> str:
            raise EOFError

        monkeypatch.setattr("builtins.input", _raise_eof)

        decision = prompter.prompt_step_failure(step=_failing_step(), result=_failing_result(), diagnostic="boom")

        assert decision == FailurePromptDecision.ABORT

    def test_prompt_text_visible_before_input_blocks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DispatcherFailurePrompter.prompt_step_failure: prompt title and diagnostic are in the buffer at the moment input() is called."""
        dispatcher, buffer = _buffered_dispatcher(force_terminal=True)
        prompter = DispatcherFailurePrompter(dispatcher)
        seen_before_input: list[str] = []

        def _capture_then_answer(*_args: object, **_kwargs: object) -> str:
            seen_before_input.append(buffer.getvalue())
            return "a"

        monkeypatch.setattr("builtins.input", _capture_then_answer)

        prompter.prompt_step_failure(step=_failing_step(), result=_failing_result(), diagnostic="disk full")

        assert len(seen_before_input) == 1
        assert "Step 'Build' failed (exit code 1)." in seen_before_input[0]
        assert "disk full" in seen_before_input[0]

    def test_prompt_loop_max_iterations_terminal_grant_decision(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DispatcherFailurePrompter.prompt_loop_max_iterations: input 'g' returns LoopPromptDecision.GRANT, buffer contains the max-iterations title."""
        dispatcher, buffer = _buffered_dispatcher(force_terminal=True)
        prompter = DispatcherFailurePrompter(dispatcher)
        monkeypatch.setattr("builtins.input", lambda *_args, **_kwargs: "g")

        decision = prompter.prompt_loop_max_iterations(loop=_loop_block(), iteration=3, diagnostic="loop diag")

        assert decision == LoopPromptDecision.GRANT
        assert "Reached max_iterations (5)" in buffer.getvalue()

    def test_prompt_step_failure_non_interactive_json_mode_aborts_without_blocking(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """DispatcherFailurePrompter.prompt_step_failure: json output_format dispatches one PromptEvent NDJSON line and returns ABORT without calling input()."""
        dispatcher = UiDispatcher(output_format="json")
        prompter = DispatcherFailurePrompter(dispatcher)

        def _fail_if_called(*_args: object, **_kwargs: object) -> str:
            raise AssertionError("input() must not be called in non-interactive json mode")

        monkeypatch.setattr("builtins.input", _fail_if_called)

        decision = prompter.prompt_step_failure(step=_failing_step(), result=_failing_result(), diagnostic="boom")

        assert decision == FailurePromptDecision.ABORT
        lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["event_type"] == "PromptEvent"
