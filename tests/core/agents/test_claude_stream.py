"""Tests for the pure Claude Code stream-json classifier."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import dovo.core.agents.claude_stream as claude_stream_module
from dovo.core.agents.claude_stream import classify_claude_output
from dovo.core.agents.cli_mutation import CliMutationOutcome
from tests.harness.fixtures import load_agent_fixture

_IMPURE_MODULES = frozenset({"os", "subprocess", "pathlib", "shutil", "socket", "tempfile", "asyncio", "sys"})
_UPDATE_REQUIRED_DETAIL = (
    "Installed Claude Code lacks a required flag. Fix: run `claude update` or reinstall Claude Code."
)


def _fixture_stdout(name: str) -> str:
    return "\n".join(load_agent_fixture("claude", name))


def _last_result_text(name: str) -> str:
    results = [event for event in map(json.loads, load_agent_fixture("claude", name)) if event.get("type") == "result"]
    result_text = results[-1]["result"]
    assert isinstance(result_text, str)
    return result_text


def _stream(*events: dict[str, object]) -> str:
    return "\n".join(json.dumps(event) for event in events)


_SUCCESS_RESULT: dict[str, object] = {"type": "result", "subtype": "success", "is_error": False, "result": "done"}


class ClaudeStreamParsingTests:
    @pytest.mark.parametrize(
        ("line", "reason"),
        [
            pytest.param("not json", "", id="invalid-json"),
            pytest.param("[1]", "expected a JSON object, got list", id="array"),
            pytest.param("42", "expected a JSON object, got int", id="scalar"),
        ],
    )
    def test_malformed_line_returns_invalid_stream_error(self, line: str, reason: str) -> None:
        """[tier-1/unit] classify_claude_output: returncode 0 with a malformed stdout line returns CliMutationOutcome(status='error', error_detail='invalid stream-json from Claude Code: <reason>') with result_text None."""
        outcome = classify_claude_output(0, f"{line}\n{_stream(_SUCCESS_RESULT)}", "")

        assert outcome.status == "error"
        assert (outcome.error_detail or "").startswith(f"invalid stream-json from Claude Code: {reason}")
        assert outcome.result_text is None

    def test_truncated_json_line_returns_invalid_stream_error(self) -> None:
        """[tier-1/unit] classify_claude_output: returncode 0 with a final line cut mid-object returns status 'error' whose error_detail starts with 'invalid stream-json from Claude Code: '."""
        outcome = classify_claude_output(0, '{"type": "result", "subtype": "succ', "")

        assert outcome.status == "error"
        assert (outcome.error_detail or "").startswith("invalid stream-json from Claude Code: ")

    def test_unknown_event_types_and_blank_lines_are_ignored(self) -> None:
        """[tier-1/unit] classify_claude_output: blank lines and a well-formed {'type': 'future_event'} line before a successful result still return status 'finished' with that result's text."""
        stdout_text = f"\n  \n{json.dumps({'type': 'future_event'})}\r\n\n{_stream(_SUCCESS_RESULT)}\n"

        outcome = classify_claude_output(0, stdout_text, "")

        assert outcome == CliMutationOutcome(status="finished", result_text="done")


class ClaudeStreamSuccessTests:
    @pytest.mark.parametrize(
        "scenario",
        ["planning", "reviewing", "implementing_create", "implementing_edit", "permission_denied", "tool_failure"],
    )
    def test_success_fixture_returns_finished_with_final_result_text(self, scenario: str) -> None:
        """[tier-1/unit] classify_claude_output: each committed success fixture at returncode 0 returns CliMutationOutcome(status='finished', result_text=<the fixture's last result event 'result' string>, error_detail=None, denials=[])."""
        outcome = classify_claude_output(0, _fixture_stdout(scenario), "")

        assert outcome.status == "finished"
        assert outcome.result_text == _last_result_text(scenario)

    def test_last_result_event_decides_the_outcome(self) -> None:
        """[tier-1/unit] classify_claude_output: a stream with a successful result followed by a later error_max_turns result returns status 'error'."""
        stdout_text = _stream(_SUCCESS_RESULT, {"type": "result", "subtype": "error_max_turns", "is_error": True})

        outcome = classify_claude_output(0, stdout_text, "")

        assert outcome.status == "error"
        assert outcome.error_detail == "Claude Code result error (error_max_turns)"


class ClaudeStreamTerminalErrorTests:
    def test_stream_without_result_returns_ended_without_result(self) -> None:
        """[tier-1/unit] classify_claude_output: the truncated_no_result fixture at returncode 0 returns status 'error' with error_detail 'Claude Code stream ended without a result'."""
        outcome = classify_claude_output(0, _fixture_stdout("truncated_no_result"), "")

        assert outcome == CliMutationOutcome(status="error", error_detail="Claude Code stream ended without a result")

    def test_is_error_true_with_success_subtype_names_terminal_reason(self) -> None:
        """[tier-1/unit] classify_claude_output: the provider_error fixture stdout at returncode 0 returns status 'error' with error_detail 'Claude Code result error (api_error): There\'s an issue with the selected model (not-a-real-model). It may not exist or you may not have access to it. Run --model to pick a different model.' and result_text None."""
        outcome = classify_claude_output(0, _fixture_stdout("provider_error"), "")

        assert outcome == CliMutationOutcome(
            status="error",
            error_detail=(
                "Claude Code result error (api_error): There's an issue with the selected model "
                "(not-a-real-model). It may not exist or you may not have access to it. "
                "Run --model to pick a different model."
            ),
        )

    @pytest.mark.parametrize(
        ("event", "detail"),
        [
            pytest.param(
                {"subtype": "error_max_turns", "is_error": False, "result": "ran out"},
                "Claude Code result error (error_max_turns): ran out",
                id="error-subtype-with-text",
            ),
            pytest.param(
                {"subtype": "error_during_execution", "is_error": False},
                "Claude Code result error (error_during_execution)",
                id="error-subtype-without-text",
            ),
            pytest.param(
                {"subtype": "success", "is_error": True, "result": "boom"},
                "Claude Code result error: boom",
                id="is-error-without-terminal-reason",
            ),
            pytest.param(
                {"subtype": "success", "is_error": True, "terminal_reason": "api_error", "result": "boom"},
                "Claude Code result error (api_error): boom",
                id="is-error-with-terminal-reason",
            ),
            pytest.param(
                {"subtype": "partial", "is_error": False, "result": "x"},
                "unsupported Claude Code result status 'partial'",
                id="unsupported-subtype",
            ),
            pytest.param(
                {"is_error": False},
                "unsupported Claude Code result status 'unknown'",
                id="missing-subtype",
            ),
        ],
    )
    def test_non_success_result_returns_matching_error_detail(self, event: dict[str, object], detail: str) -> None:
        """[tier-1/unit] classify_claude_output: a terminal result event of type 'result' with the parametrized subtype returns status 'error' with the parametrized error_detail at returncode 0."""
        outcome = classify_claude_output(0, _stream({"type": "result", **event}), "")

        assert outcome == CliMutationOutcome(status="error", error_detail=detail)


class ClaudeStreamExitCodeTests:
    def test_nonzero_exit_prefers_terminal_result_error_over_stderr(self) -> None:
        """[tier-1/unit] classify_claude_output: returncode 1 with provider_error fixture stdout and stderr '[claude-code:unrecognized_model]' returns status 'error' with the 'Claude Code result error (api_error): ...' detail, not the stderr text."""
        outcome = classify_claude_output(1, _fixture_stdout("provider_error"), "[claude-code:unrecognized_model]")

        assert outcome.status == "error"
        assert (outcome.error_detail or "").startswith("Claude Code result error (api_error): ")

    @pytest.mark.parametrize(
        ("stdout_text", "stderr_text", "detail"),
        [
            pytest.param("", "boom\n", "boom", id="stderr"),
            pytest.param("plain stdout\n", "  \n", "plain stdout", id="stdout"),
            pytest.param("", "", "exit 2", id="exit-code"),
            pytest.param("", "Traceback (most recent call last):\n  File x", "exit 2", id="traceback-only-stderr"),
        ],
    )
    def test_nonzero_exit_falls_back_to_stderr_then_stdout_then_code(
        self, stdout_text: str, stderr_text: str, detail: str
    ) -> None:
        """[tier-1/unit] classify_claude_output: returncode 2 with no terminal result error returns status 'error' with error_detail equal to the parametrized detail."""
        outcome = classify_claude_output(2, stdout_text, stderr_text)

        assert outcome == CliMutationOutcome(status="error", error_detail=detail)

    def test_nonzero_exit_with_successful_stream_returns_error(self) -> None:
        """[tier-1/unit] classify_claude_output: returncode 1 with the planning fixture stdout returns status 'error' with result_text None and error_detail 'exit 1'."""
        outcome = classify_claude_output(1, _fixture_stdout("planning"), "")

        assert outcome == CliMutationOutcome(status="error", error_detail="exit 1")

    def test_nonzero_exit_with_cut_off_stream_returns_ended_without_result_and_exit_code(self) -> None:
        """[tier-1/unit] classify_claude_output: returncode 3 with the truncated_no_result fixture stdout and blank stderr returns error_detail 'Claude Code stream ended without a result (exit 3)', never the raw stream text."""
        outcome = classify_claude_output(3, _fixture_stdout("truncated_no_result"), "")

        assert outcome == CliMutationOutcome(
            status="error", error_detail="Claude Code stream ended without a result (exit 3)"
        )

    def test_nonzero_exit_reads_terminal_error_past_a_malformed_line(self) -> None:
        """[tier-1/unit] classify_claude_output: returncode 1 with a non-JSON line followed by an is_error result returns error_detail starting 'Claude Code result error (' rather than 'invalid stream-json'."""
        stdout_text = "not json\n" + _stream({"type": "result", "subtype": "error_max_turns", "is_error": True})

        outcome = classify_claude_output(1, stdout_text, "")

        assert outcome.error_detail == "Claude Code result error (error_max_turns)"

    def test_unknown_option_stderr_returns_update_required_detail(self) -> None:
        """[tier-1/unit] classify_claude_output: returncode 1 with stderr 'error: Unknown option --foo' returns error_detail 'Installed Claude Code lacks a required flag. Fix: run `claude update` or reinstall Claude Code.' even when stdout holds an error result."""
        outcome = classify_claude_output(1, _fixture_stdout("provider_error"), "error: Unknown option --foo")

        assert outcome == CliMutationOutcome(status="error", error_detail=_UPDATE_REQUIRED_DETAIL)


class ClaudeStreamEmptyResultTests:
    def test_empty_result_string_returns_finished_with_none_text(self) -> None:
        """[tier-1/unit] classify_claude_output: a successful terminal result with result '' at returncode 0 returns CliMutationOutcome(status='finished', result_text=None, error_detail=None, denials=[])."""
        outcome = classify_claude_output(0, _stream({**_SUCCESS_RESULT, "result": ""}), "")

        assert outcome.status == "finished"
        assert outcome.result_text is None


class ClaudeStreamSanitizationTests:
    def test_multiline_stderr_becomes_one_trimmed_line(self) -> None:
        """[tier-1/unit] classify_claude_output: returncode 1 with stderr '  first\nsecond\t third  \n' returns error_detail 'first second third'."""
        outcome = classify_claude_output(1, "", "  first\nsecond\t third  \n")

        assert outcome.error_detail == "first second third"

    def test_overlong_diagnostic_is_capped_at_2000_characters(self) -> None:
        """[tier-1/unit] classify_claude_output: returncode 1 with 5000 'x' characters of stderr returns an error_detail of exactly 2000 characters ending with '…'."""
        outcome = classify_claude_output(1, "", "x" * 5000)

        assert outcome.error_detail == "x" * 1999 + "…"

    def test_diagnostic_of_exactly_2000_characters_is_unchanged(self) -> None:
        """[tier-1/unit] classify_claude_output: returncode 1 with 2000 'x' characters of stderr returns that text unchanged."""
        outcome = classify_claude_output(1, "", "x" * 2000)

        assert outcome.error_detail == "x" * 2000

    @pytest.mark.parametrize(
        ("stderr_text", "detail"),
        [
            pytest.param(
                'failed\nTraceback (most recent call last):\n  File "a.py", line 1\nValueError: x',
                "failed",
                id="python",
            ),
            pytest.param("failed\n    at run (/app/cli.js:10:5)\n    at main (/app/cli.js:20:1)", "failed", id="node"),
        ],
    )
    def test_stack_traces_are_removed_from_diagnostics(self, stderr_text: str, detail: str) -> None:
        """[tier-1/unit] classify_claude_output: returncode 1 with the parametrized stderr returns error_detail equal to the parametrized detail with no traceback or frame text."""
        outcome = classify_claude_output(1, "", stderr_text)

        assert outcome.error_detail == detail


def _imported_top_level_modules(source: str) -> set[str]:
    """Return the top-level module names a source text imports directly, including inside functions."""
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            imported.add(node.module.split(".")[0])

    return imported


class ClaudeStreamPurityTests:
    def test_import_scan_flags_impure_imports_including_nested_ones(self) -> None:
        """[tier-1/unit] _imported_top_level_modules: a source with 'import os.path', 'from subprocess import run' and a function-local 'import socket' reports os, subprocess and socket as impure imports."""
        source = "import os.path\nfrom subprocess import run\n\ndef late() -> None:\n    import socket\n"

        imported = _imported_top_level_modules(source)

        assert imported & _IMPURE_MODULES == {"os", "subprocess", "socket"}

    def test_module_imports_no_process_filesystem_or_environment_apis(self) -> None:
        """[tier-1/unit] dovo.core.agents.claude_stream: the module source directly imports none of os, subprocess, pathlib, shutil, socket, tempfile, asyncio, sys."""
        imported = _imported_top_level_modules(Path(claude_stream_module.__file__).read_text(encoding="utf-8"))

        assert {"json", "re", "dovo"} <= imported
        assert imported & _IMPURE_MODULES == set()
