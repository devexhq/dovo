"""Contract tests for DispatcherFailurePrompter decision parsing and streaming order."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from rich.console import Console

from dovo.cli.run.prompter import (
    DispatcherFailurePrompter,
    DispatcherInteractionPrompter,
    TerminalReader,
    paused_prompt_message,
)
from dovo.cli.ui.dispatcher import UiDispatcher
from dovo.core.catalog.definitions import CommentPolicy, LoopStepBlock, StepDefinition
from dovo.core.interaction import (
    InteractionAnswer,
    InteractionRequest,
    RequestArtifact,
    RequestComment,
    RequestOption,
)
from dovo.engine import FailurePromptDecision, LoopPromptDecision
from dovo.engine.executors.models import StepResult


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
        dispatcher, _ = _buffered_dispatcher(force_terminal=True)
        prompter = DispatcherFailurePrompter(dispatcher)
        monkeypatch.setattr("builtins.input", lambda *_args, **_kwargs: raw_input)

        decision = prompter.prompt_step_failure(step=_failing_step(), result=_failing_result(), diagnostic="boom")

        assert decision == expected

    def test_prompt_step_failure_invalid_then_valid_reprompts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dispatcher, buffer = _buffered_dispatcher(force_terminal=True)
        prompter = DispatcherFailurePrompter(dispatcher)
        responses = iter(["zz", "c"])
        monkeypatch.setattr("builtins.input", lambda *_args, **_kwargs: next(responses))

        decision = prompter.prompt_step_failure(step=_failing_step(), result=_failing_result(), diagnostic="boom")

        assert decision == FailurePromptDecision.CONTINUE
        assert "Invalid option" in buffer.getvalue()

    def test_prompt_step_failure_eof_aborts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dispatcher, _ = _buffered_dispatcher(force_terminal=True)
        prompter = DispatcherFailurePrompter(dispatcher)

        def _raise_eof(*_args: object, **_kwargs: object) -> str:
            raise EOFError

        monkeypatch.setattr("builtins.input", _raise_eof)

        decision = prompter.prompt_step_failure(step=_failing_step(), result=_failing_result(), diagnostic="boom")

        assert decision == FailurePromptDecision.ABORT

    def test_prompt_text_visible_before_input_blocks(self, monkeypatch: pytest.MonkeyPatch) -> None:
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
        dispatcher, buffer = _buffered_dispatcher(force_terminal=True)
        prompter = DispatcherFailurePrompter(dispatcher)
        monkeypatch.setattr("builtins.input", lambda *_args, **_kwargs: "g")

        decision = prompter.prompt_loop_max_iterations(loop=_loop_block(), iteration=3, diagnostic="loop diag")

        assert decision == LoopPromptDecision.GRANT
        assert "Reached max_iterations (5)" in buffer.getvalue()

    def test_json_mode_aborts_without_blocking_and_events_carry_the_step_or_loop_id(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        prompter = DispatcherFailurePrompter(UiDispatcher(output_format="json"))

        def _fail_if_called(*_args: object, **_kwargs: object) -> str:
            raise AssertionError("input() must not be called in non-interactive json mode")

        monkeypatch.setattr("builtins.input", _fail_if_called)

        step_decision = prompter.prompt_step_failure(step=_failing_step(), result=_failing_result(), diagnostic="boom")
        loop_decision = prompter.prompt_loop_max_iterations(loop=_loop_block(), iteration=1, diagnostic="diag")

        assert (step_decision, loop_decision) == (FailurePromptDecision.ABORT, LoopPromptDecision.ABORT)
        events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
        assert [(event["event_type"], event["payload"]["step_id"]) for event in events] == [
            ("PromptEvent", "s1"),
            ("PromptEvent", "loop1"),
        ]


def _option(value: str, label: str, *, policy: CommentPolicy = "disabled", fail: bool = False) -> RequestOption:
    """Return a request option."""
    return RequestOption(value=value, label=label, comment=policy, fail=fail)


def _select_request(
    *, policy: CommentPolicy = "disabled", artifact: RequestArtifact | None = None
) -> InteractionRequest:
    """Return a select request approve / revise / deny with a required comment on revise."""
    return InteractionRequest(
        step_id="approve",
        mode="select",
        message="Approve this plan?",
        options=[
            _option("approve", "Approve", policy=policy),
            _option("revise", "Request changes", policy="required"),
            _option("deny", "Deny", policy=policy, fail=True),
        ],
        comment=RequestComment(policy=policy, message="What should change?"),
        artifact=artifact,
    )


def _multiselect_request() -> InteractionRequest:
    """Return a multiselect request over B1 / B2 / B3 without comments."""
    return InteractionRequest(
        step_id="triage",
        mode="multiselect",
        message="Which findings should the agent fix?",
        options=[
            _option("B1", "Missing validation"),
            _option("B2", "Unclear message"),
            _option("B3", "Race in cleanup"),
        ],
    )


class _Terminal:
    """Scripted builtins.input replacement recording each prompt; an exception entry is raised instead of returned."""

    def __init__(self, entries: list[str | BaseException]) -> None:
        self.entries = list(entries)
        self.prompts: list[str] = []

    def __call__(self, prompt: str = "") -> str:
        self.prompts.append(prompt)
        entry = self.entries.pop(0)
        if isinstance(entry, BaseException):
            raise entry
        return entry


def _ask(
    monkeypatch: pytest.MonkeyPatch, request: InteractionRequest, entries: list[str | BaseException]
) -> tuple[InteractionAnswer | None, _Terminal, io.StringIO]:
    """Ask request on an interactive terminal dispatcher fed entries; return the answer, terminal and output buffer."""
    dispatcher, buffer = _buffered_dispatcher(force_terminal=True)
    terminal = _Terminal(entries)
    monkeypatch.setattr("builtins.input", terminal)
    return DispatcherInteractionPrompter(dispatcher).ask(request), terminal, buffer


class DispatcherInteractionPrompterTests:
    """Contract tests for DispatcherInteractionPrompter."""

    def test_select_answer_by_index_with_required_comment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        answer, _, _ = _ask(monkeypatch, _select_request(), ["2", "Keep config compatible"])

        assert answer == InteractionAnswer(selected=["revise"], comment="Keep config compatible")

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            pytest.param("1,3", ["B1", "B3"], id="indexes"),
            pytest.param("", [], id="enter_for_none"),
            pytest.param("3,1,1", ["B1", "B3"], id="option_order_unique"),
            pytest.param("B2", ["B2"], id="value"),
        ],
    )
    def test_multiselect_reads_comma_indexes_and_enter_for_none(
        self, monkeypatch: pytest.MonkeyPatch, raw: str, expected: list[str]
    ) -> None:
        answer, terminal, _ = _ask(monkeypatch, _multiselect_request(), [raw])

        assert answer == InteractionAnswer(selected=expected)
        assert terminal.prompts == ["Select (comma-separated, Enter for none): "]

    def test_text_mode_reprompts_until_non_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        request = InteractionRequest(step_id="why", mode="text", message="Why?")

        answer, terminal, _ = _ask(monkeypatch, request, ["", "  ", "use sqlite "])

        assert answer == InteractionAnswer(text="use sqlite")
        assert terminal.prompts == ["Text (required): "] * 3

    def test_invalid_choice_reprompts_with_a_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        answer, terminal, buffer = _ask(monkeypatch, _select_request(), ["9", "x", "1"])

        assert answer == InteractionAnswer(selected=["approve"])
        assert terminal.prompts == ["Select [1-3]: "] * 3
        assert buffer.getvalue().count("Invalid choice") == 2

    @pytest.mark.parametrize(
        ("policy", "entries", "expected_comment", "expected_prompts", "expected_message"),
        [
            pytest.param(
                "optional",
                ["1", ""],
                None,
                ["Select [1-3]: ", "What should change? (optional, Enter to skip): "],
                None,
                id="optional_enter_skips",
            ),
            pytest.param(
                "optional",
                ["1", "keep it   "],
                "keep it",
                ["Select [1-3]: ", "What should change? (optional, Enter to skip): "],
                None,
                id="trailing_whitespace_stripped",
            ),
            pytest.param(
                "required",
                ["2", "", "now"],
                "now",
                ["Select [1-3]: ", "What should change? (required): ", "What should change? (required): "],
                None,
                id="required_reprompts_on_empty",
            ),
            pytest.param(
                "optional",
                ["1", "x" * 8001, "short"],
                "short",
                [
                    "Select [1-3]: ",
                    "What should change? (optional, Enter to skip): ",
                    "What should change? (optional, Enter to skip): ",
                ],
                "Comment is too long (max 8000 characters).",
                id="too_long_reprompts",
            ),
        ],
    )
    def test_comment_policy_prompts_and_limits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        policy: CommentPolicy,
        entries: list[str | BaseException],
        expected_comment: str | None,
        expected_prompts: list[str],
        expected_message: str | None,
    ) -> None:
        """Enforce the comment policy: skip, required re-prompt, 8000-character limit and whitespace stripping."""
        answer, terminal, buffer = _ask(monkeypatch, _select_request(policy=policy), entries)

        assert answer is not None
        assert answer.comment == expected_comment
        assert terminal.prompts == expected_prompts
        if expected_message is not None:
            assert expected_message in buffer.getvalue()

    @pytest.mark.parametrize(
        ("environment", "expected"),
        [
            pytest.param({"VISUAL": "vim", "EDITOR": "nano"}, "visual: vim", id="visual_first"),
            pytest.param({"EDITOR": "nano"}, "editor: nano", id="editor_second"),
            pytest.param({}, "eof input", id="eof_fallback"),
        ],
    )
    def test_edit_command_uses_visual_then_editor_then_eof_input(
        self, monkeypatch: pytest.MonkeyPatch, environment: dict[str, str], expected: str
    ) -> None:
        monkeypatch.delenv("VISUAL", raising=False)
        monkeypatch.delenv("EDITOR", raising=False)
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setattr(
            TerminalReader, "edit", lambda self, command: f"{'visual' if command == 'vim' else 'editor'}: {command}"
        )
        monkeypatch.setattr(TerminalReader, "read_until_eof", lambda self: "eof input")

        answer, _, _ = _ask(monkeypatch, _select_request(), ["2", "/edit"])

        assert answer == InteractionAnswer(selected=["revise"], comment=expected)

    @pytest.mark.parametrize("interrupt", [KeyboardInterrupt, EOFError])
    def test_interrupt_between_choice_and_comment_returns_none(
        self, monkeypatch: pytest.MonkeyPatch, interrupt: type[BaseException]
    ) -> None:
        answer, _, _ = _ask(monkeypatch, _select_request(), ["2", interrupt()])

        assert answer is None

    @pytest.mark.parametrize(
        ("no_tty", "force_terminal"),
        [
            pytest.param(True, True, id="no_tty"),
            pytest.param(False, False, id="not_a_terminal"),
        ],
    )
    def test_non_interactive_emits_event_and_returns_none_without_reading(
        self, monkeypatch: pytest.MonkeyPatch, no_tty: bool, force_terminal: bool
    ) -> None:
        dispatcher, buffer = _buffered_dispatcher(force_terminal=force_terminal)
        terminal = _Terminal([])
        monkeypatch.setattr("builtins.input", terminal)
        prompter = DispatcherInteractionPrompter(dispatcher, no_tty=no_tty)

        answer = prompter.ask(_select_request())

        assert (answer, terminal.prompts, prompter.is_interactive) == (None, [], False)
        assert "approve" in buffer.getvalue()

    def test_json_format_emits_prompt_event_and_returns_none_without_reading(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        terminal = _Terminal([])
        monkeypatch.setattr("builtins.input", terminal)
        prompter = DispatcherInteractionPrompter(UiDispatcher(output_format="json"), no_tty=False)

        answer = prompter.ask(_select_request())

        assert (answer, terminal.prompts, prompter.is_interactive) == (None, [], False)
        written = capsys.readouterr().out
        assert [json.loads(line)["event_type"] for line in written.splitlines() if line.strip()] == ["PromptEvent"]

    def test_event_uses_one_based_keys_option_values_and_short_digest(self, capsys: pytest.CaptureFixture[str]) -> None:
        artifact = RequestArtifact(name="plan", revision=2, digest="b07d44aa11223344", preview=["line"])
        prompter = DispatcherInteractionPrompter(UiDispatcher(output_format="json"), no_tty=True)

        prompter.ask(_select_request(policy="optional", artifact=artifact))

        payload = json.loads(capsys.readouterr().out.splitlines()[0])["payload"]
        assert payload["step_id"] == "approve"
        assert payload["artifact"] == {
            "name": "plan",
            "revision": 2,
            "digest": "b07d44aa",
            "reference": "plan@2  sha256:b07d44aa…",
            "preview": ["line"],
        }
        assert payload["comment"] == {"policy": "optional", "message": "What should change?"}
        assert [(o["key"], o["decision"], o["comment"], o["fail"]) for o in payload["options"]] == [
            ("1", "approve", "optional", False),
            ("2", "revise", "required", False),
            ("3", "deny", "optional", True),
        ]

    def test_multiselect_event_displays_the_value_with_the_label(self, capsys: pytest.CaptureFixture[str]) -> None:
        prompter = DispatcherInteractionPrompter(UiDispatcher(output_format="json"), no_tty=True)

        prompter.ask(_multiselect_request())
        prompter.ask(_select_request())

        events = [json.loads(line)["payload"] for line in capsys.readouterr().out.splitlines() if line.strip()]
        assert events[0]["options"][0]["display"] == "B1 — " + events[0]["options"][0]["label"]
        assert events[1]["options"][0]["display"] == events[1]["options"][0]["label"]


class TerminalReaderTests:
    """Contract tests for TerminalReader."""

    def test_read_until_eof_joins_lines(self, monkeypatch: pytest.MonkeyPatch) -> None:
        terminal = _Terminal(["first", "second", EOFError()])
        monkeypatch.setattr("builtins.input", terminal)

        assert TerminalReader().read_until_eof() == "first\nsecond"

    def test_edit_returns_the_file_content_the_editor_wrote(self, tmp_path: Path) -> None:
        editor = tmp_path / "editor.sh"
        editor.write_text("#!/bin/sh\nprintf 'edited text' > \"$1\"\n", encoding="utf-8")
        editor.chmod(0o755)

        assert TerminalReader().edit(str(editor)) == "edited text"


class PausedPromptMessageTests:
    """Contract tests for paused_prompt_message."""

    @pytest.mark.parametrize(
        ("non_interactive", "expected"),
        [
            pytest.param(
                False,
                "Run paused at prompt 'approve'. Resume with 'dovo resume blueprint_a1b2c3d4'.",
                id="interactive",
            ),
            pytest.param(
                True,
                "Run paused at prompt 'approve'. Answer with 'dovo resume blueprint_a1b2c3d4 --answer approve=<value>'.",
                id="non_interactive",
            ),
        ],
    )
    def test_message_text_by_interactivity(self, non_interactive: bool, expected: str) -> None:
        assert paused_prompt_message("approve", "blueprint_a1b2c3d4", non_interactive=non_interactive) == expected
