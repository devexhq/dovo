"""Contract tests for agent step execution through resolved providers in a Git worktree."""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.common.tool_policy import ToolCapability, ToolPolicy, ToolRule
from dovo.core.agents import (
    AgentAttempt,
    AgentAttemptContext,
    AgentProgress,
    AgentProgressKind,
    AgentRequest,
    AgentResponse,
    AgentResponseStatus,
    BaseAgentProvider,
    ResolvedAgentSettings,
    default_tool_policy,
    tool_policy_unsupported_message,
)
from dovo.core.agents.models import AgentDenial
from dovo.core.agents.responses import blocked_response, provider_error_response
from dovo.core.agents.scratch import scratch_unavailable_message
from dovo.core.catalog.definitions import StepDefinition, StepType
from dovo.core.sessions import SessionLogEvent, SessionLogEventType
from dovo.engine.executors.agent_step import (
    BLANK_PROMPT_MESSAGE,
    MISSING_SETTINGS_MESSAGE,
    WORKTREE_REQUIRED_MESSAGE,
    build_agent_step_runner,
    execute_agent_step,
)
from dovo.engine.executors.metadata import metadata_to_env
from dovo.engine.executors.models import (
    AGENT_OUTCOME_EXIT_CODES,
    AgentProgressSink,
    ExecutionMetadata,
    StepDispatchOutcome,
    StepMetadata,
)
from tests.harness import AGENT_ADAPTER_FACTORY, FakeAgentProvider, RecordingProgressSink, new_file_diff
from tests.harness.builders import StepBuilder
from tests.harness.diagnostic_log import diagnostic_hint, read_single_event

_NO_OP_SUMMARY = "Inspected the repository; no edits were required."
_METADATA = ExecutionMetadata(step=StepMetadata(id="plan", name="plan", index=1))


def _settings(provider: str = "copilot") -> ResolvedAgentSettings:
    return ResolvedAgentSettings(
        provider=provider,
        model="m",
        endpoint="http://e",
        temperature=0.7,
        max_tokens=512,
        tools=default_tool_policy(),
    )


def _session_tmp_dir(worktree: Path) -> Path:
    session_tmp = worktree.parent / "session-tmp"
    session_tmp.mkdir(exist_ok=True)
    return session_tmp


def _step(prompt: str = "Plan the change", timeout_seconds: int = 45) -> StepDefinition:
    return StepBuilder.agent(prompt).with_timeout(timeout_seconds).build()


def _use_provider(monkeypatch: pytest.MonkeyPatch, provider: BaseAgentProvider) -> list[str]:
    requested: list[str] = []

    def _factory(token: str) -> BaseAgentProvider:
        requested.append(token)
        return provider

    monkeypatch.setattr(AGENT_ADAPTER_FACTORY, _factory)
    return requested


def _run(
    worktree: Path,
    *,
    step: StepDefinition | None = None,
    agent: ResolvedAgentSettings | None = None,
    worktree_active: bool = True,
    session_tmp_dir: Path | None = None,
    session_log_dir: Path | None = None,
    session_dir: Path | None = None,
    metadata: ExecutionMetadata = _METADATA,
    on_output: Callable[[str, str], None] | None = None,
    progress: AgentProgressSink | None = None,
) -> StepDispatchOutcome:
    return execute_agent_step(
        step or _step(),
        agent=agent or _settings(),
        worktree_path=worktree,
        worktree_active=worktree_active,
        session_tmp_dir=session_tmp_dir or _session_tmp_dir(worktree),
        main_checkout=worktree,
        session_log_dir=session_log_dir,
        session_dir=session_dir,
        metadata=metadata,
        on_output=on_output,
        on_agent_line=None,
        progress=progress or RecordingProgressSink(),
    )


def _porcelain(repo: Path) -> str:
    return subprocess.run(["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True).stdout


def _summary(outcome: StepDispatchOutcome) -> dict[str, object]:
    assert outcome.stdout.endswith("\n")
    assert outcome.stdout.count("\n") == 1
    return json.loads(outcome.stdout)


def _patch(name: str = "a.txt", summary: str | None = None) -> AgentResponse:
    return AgentResponse(status=AgentResponseStatus.PROPOSED_PATCH, unified_diff=new_file_diff(name), summary=summary)


def _no_op(summary: str | None = _NO_OP_SUMMARY, raw_text: str | None = None) -> AgentResponse:
    return AgentResponse(status=AgentResponseStatus.NO_OP, summary=summary, raw_text=raw_text)


class AgentOutcomeMappingTests:
    def test_mapping_covers_every_status_with_issue_codes(self) -> None:
        assert dict(AGENT_OUTCOME_EXIT_CODES) == {
            AgentResponseStatus.PROPOSED_PATCH: 0,
            AgentResponseStatus.NO_OP: 0,
            AgentResponseStatus.UNFIXABLE: 201,
            AgentResponseStatus.TIMEOUT: 202,
            AgentResponseStatus.PROVIDER_ERROR: 203,
            AgentResponseStatus.BLOCKED: 204,
        }
        assert set(AGENT_OUTCOME_EXIT_CODES) == set(AgentResponseStatus)


class ExecuteAgentStepToolPolicyFailureTests:
    @pytest.mark.parametrize(
        ("response", "exit_code", "summary_status", "stderr_fragments"),
        [
            pytest.param(
                blocked_response(
                    provider="copilot",
                    duration_seconds=1.0,
                    denials=[AgentDenial(tool="bash", message="denied", capability=ToolCapability.SHELL)],
                ),
                204,
                "blocked",
                ["AGENT_PERMISSION_BLOCKED", "tools: {allow: [{capability: shell}]}"],
                id="blocked",
            ),
            pytest.param(
                provider_error_response(duration_seconds=1.0, errors=[tool_policy_unsupported_message("copilot")]),
                203,
                "provider_error",
                ["AGENT_TOOL_POLICY_UNSUPPORTED"],
                id="unsupported_policy",
            ),
        ],
    )
    def test_tool_policy_failure_fails_step_with_mapped_exit_code_and_diagnostic(
        self,
        git_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        response: AgentResponse,
        exit_code: int,
        summary_status: str,
        stderr_fragments: list[str],
    ) -> None:
        _use_provider(monkeypatch, FakeAgentProvider(response))

        outcome = _run(git_repo)

        assert (outcome.status, outcome.exit_code) == ("failed", exit_code)
        assert _summary(outcome)["status"] == summary_status
        assert [fragment for fragment in stderr_fragments if fragment not in outcome.stderr] == []


def _record_direct_attempts(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    def _record(
        *,
        instruction: str,
        settings: ResolvedAgentSettings,
        worktree_path: Path,
        timeout_seconds: int,
        tools: ToolPolicy,
        max_files: int | None,
        context: AgentAttemptContext | None,
    ) -> AgentAttempt:
        calls.append(
            {
                "instruction": instruction,
                "settings": settings,
                "worktree_path": worktree_path,
                "timeout_seconds": timeout_seconds,
                "max_files": max_files,
            }
        )
        return AgentAttempt(status=AgentResponseStatus.NO_OP)

    monkeypatch.setattr("dovo.engine.executors.agent_step.run_direct_attempt", _record)
    return calls


class ExecuteAgentStepRequestTests:
    def test_valid_step_forwards_prompt_settings_worktree_and_timeout_to_run_direct_attempt(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _record_direct_attempts(monkeypatch)

        _run(git_repo)

        assert calls == [
            {
                "instruction": "Plan the change",
                "settings": _settings(),
                "worktree_path": git_repo,
                "timeout_seconds": 45,
                "max_files": None,
            }
        ]

    def test_agent_max_files_forwards_to_direct_attempt(self, git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _record_direct_attempts(monkeypatch)
        step = StepDefinition(id="code", type=StepType.AGENT, prompt="Plan the change", max_files=100)

        _run(git_repo, step=step)

        assert [call["max_files"] for call in calls] == [100]


class ExecuteAgentStepOutputTests:
    def test_no_op_writes_exact_summary_line_and_completes(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _use_provider(monkeypatch, FakeAgentProvider(_no_op()))

        outcome = _run(git_repo)

        assert outcome.status == "completed"
        assert outcome.exit_code == 0
        assert outcome.stderr == ""
        assert outcome.error_message is None
        assert outcome.stdout == (
            '{"status":"no_op","summary":"Inspected the repository; no edits were required.",'
            '"unfixable_reason":null,"touched_files":[]}\n'
        )

    @pytest.mark.parametrize(
        ("status", "code", "unfixable_reason", "errors", "expected_error"),
        [
            pytest.param(
                AgentResponseStatus.UNFIXABLE,
                201,
                "needs a human",
                [],
                "Agent reported the task unfixable: needs a human",
                id="unfixable",
            ),
            pytest.param(
                AgentResponseStatus.UNFIXABLE,
                201,
                "needs a human",
                ["Discarded edit saved to /tmp/x.patch."],
                "Agent reported the task unfixable: needs a human\nDiscarded edit saved to /tmp/x.patch.",
                id="unfixable-with-diagnostics",
            ),
            pytest.param(
                AgentResponseStatus.TIMEOUT,
                202,
                None,
                ["Agent timed out after 45s (provider=copilot)."],
                "Agent timed out after 45s (provider=copilot).",
                id="timeout",
            ),
            pytest.param(
                AgentResponseStatus.PROVIDER_ERROR,
                203,
                None,
                ["Agent provider error (AGENT_PROVIDER_ERROR): down"],
                "Agent provider error (AGENT_PROVIDER_ERROR): down",
                id="provider-error",
            ),
        ],
    )
    def test_failure_statuses_fail_with_mapped_code_and_summary(
        self,
        git_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        status: AgentResponseStatus,
        code: int,
        unfixable_reason: str | None,
        errors: list[str],
        expected_error: str,
    ) -> None:
        response = AgentResponse(status=status, unfixable_reason=unfixable_reason, errors=errors)
        _use_provider(monkeypatch, FakeAgentProvider(response))

        outcome = _run(git_repo)

        assert outcome.status == "failed"
        assert outcome.exit_code == code
        assert outcome.stderr == expected_error
        assert outcome.error_message == expected_error
        assert _summary(outcome)["status"] == status.value
        assert _summary(outcome)["unfixable_reason"] == unfixable_reason

    def test_proposed_patch_completes_with_exit_zero_and_sorted_touched_files(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _use_provider(monkeypatch, FakeAgentProvider(_patch("a.txt")))

        outcome = _run(git_repo)

        assert outcome.status == "completed"
        assert outcome.exit_code == 0
        assert outcome.stderr == ""
        summary = _summary(outcome)
        assert summary["status"] == "proposed_patch"
        assert summary["touched_files"] == ["a.txt"]

    def test_missing_summary_falls_back_to_raw_text_and_escapes_newlines(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _use_provider(monkeypatch, FakeAgentProvider(_no_op(summary=None, raw_text="plan:\n1. do x")))

        outcome = _run(git_repo)

        assert _summary(outcome)["summary"] == "plan:\n1. do x"

    def test_output_callback_receives_summary_line_once(self, git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _use_provider(monkeypatch, FakeAgentProvider(_no_op()))
        received: list[tuple[str, str]] = []

        outcome = _run(git_repo, on_output=lambda stream, line: received.append((stream, line)))

        assert received == [("stdout", outcome.stdout)]

    def test_callback_failure_fails_attempt_and_drops_success_classification(
        self, git_repo: Path, frozen_diagnostic_clock: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fail with the unhinted callback error, carry the hint in warnings and write one engine.step record."""
        _use_provider(monkeypatch, FakeAgentProvider(_patch("a.txt")))
        step = _step()

        def _broken(stream: str, line: str) -> None:
            raise RuntimeError("ui down")

        outcome = _run(git_repo, step=step, on_output=_broken)

        assert outcome.status == "failed"
        assert outcome.exit_code == 203
        assert outcome.error_message == "Agent output callback error: ui down"
        assert _summary(outcome)["status"] == "provider_error"
        assert _summary(outcome)["touched_files"] == ["a.txt"]
        assert outcome.warnings == [diagnostic_hint(frozen_diagnostic_clock).lstrip("\n")]
        event = read_single_event(frozen_diagnostic_clock)
        assert (event["level"], event["operation"], event["context"]) == (
            "error",
            "engine.step",
            {"session_id": None, "step_id": step.id, "path": str(git_repo)},
        )

    def test_attempt_warnings_carry_into_outcome_warnings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Copy AgentAttempt.warnings ['w'] onto the dispatch outcome's warnings unchanged."""
        _stub_attempt(monkeypatch, AgentAttempt(status=AgentResponseStatus.PROVIDER_ERROR, warnings=["w"]))

        outcome = _run(tmp_path)

        assert outcome.warnings == ["w"]


class ExecuteAgentStepScratchTests:
    def test_dispatch_allocates_once_and_passes_context_to_the_attempt(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = FakeAgentProvider(_no_op())
        _use_provider(monkeypatch, provider)
        step = _step()
        session_tmp = _session_tmp_dir(git_repo)

        outcome = _run(git_repo, step=step, session_tmp_dir=session_tmp)

        invocation = provider.requests[0].invocation
        agent_root = session_tmp.resolve() / "steps" / step.id / "agent"
        assert outcome.status == "completed"
        assert len(provider.requests) == 1
        assert invocation is not None
        assert invocation.scratch_path == agent_root / invocation.invocation_id / "scratch"
        assert invocation.control_path == agent_root / invocation.invocation_id / "control"
        assert invocation.scratch_path.is_dir()
        assert invocation.control_path.is_dir()

    def test_allocation_failure_returns_203_with_diagnostic_and_never_looks_up_the_provider(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        requested = _use_provider(monkeypatch, FakeAgentProvider(_no_op()))
        step = _step()

        outcome = execute_agent_step(
            step,
            agent=_settings(),
            worktree_path=git_repo,
            worktree_active=True,
            session_tmp_dir=None,
            main_checkout=git_repo,
            session_log_dir=None,
            session_dir=None,
            metadata=_METADATA,
            on_output=None,
            on_agent_line=None,
            progress=RecordingProgressSink(),
        )

        assert outcome.status == "failed"
        assert outcome.exit_code == 203
        assert outcome.error_message == (
            scratch_unavailable_message(step.id, "the session temp directory is unavailable")
            + "\nFix:\n- Restore access to the session temp directory or correct its storage path."
        )
        assert requested == []

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permission bits")
    def test_directory_creation_failure_leaves_worktree_files_unchanged(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch, frozen_diagnostic_clock: Path
    ) -> None:
        requested = _use_provider(monkeypatch, FakeAgentProvider(_patch("a.txt")))
        session_tmp = _session_tmp_dir(git_repo)
        session_tmp.chmod(0o500)
        readme_before = (git_repo / "README.md").read_bytes()

        try:
            outcome = _run(git_repo, session_tmp_dir=session_tmp)
        finally:
            session_tmp.chmod(0o700)

        assert outcome.exit_code == 203
        assert "AGENT_SCRATCH_UNAVAILABLE" in (outcome.error_message or "")
        assert (outcome.error_message or "").splitlines()[1:] == [
            diagnostic_hint(frozen_diagnostic_clock).lstrip("\n"),
            "Fix:",
            "- Restore access to the session temp directory or correct its storage path.",
        ]
        assert read_single_event(frozen_diagnostic_clock)["operation"] == "engine.step"
        assert requested == []
        assert (git_repo / "README.md").read_bytes() == readme_before
        assert not (git_repo / "a.txt").exists()
        assert _porcelain(git_repo) == ""

    @pytest.mark.parametrize(
        ("step", "agent", "worktree_active", "expected_message"),
        [
            pytest.param(_step(), _settings(), False, WORKTREE_REQUIRED_MESSAGE, id="worktree-inactive"),
            pytest.param(_step(), None, True, MISSING_SETTINGS_MESSAGE, id="no-settings"),
            pytest.param(_step(prompt="   "), _settings(), True, BLANK_PROMPT_MESSAGE, id="blank-prompt"),
        ],
    )
    def test_guards_run_before_allocation(
        self,
        git_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        step: StepDefinition,
        agent: ResolvedAgentSettings | None,
        worktree_active: bool,
        expected_message: str,
    ) -> None:
        requested = _use_provider(monkeypatch, FakeAgentProvider(_patch()))
        session_tmp = _session_tmp_dir(git_repo)

        outcome = execute_agent_step(
            step,
            agent=agent,
            worktree_path=git_repo,
            worktree_active=worktree_active,
            session_tmp_dir=session_tmp,
            main_checkout=git_repo,
            session_log_dir=None,
            session_dir=None,
            metadata=_METADATA,
            on_output=None,
            on_agent_line=None,
            progress=RecordingProgressSink(),
        )

        assert (outcome.status, outcome.exit_code) == ("failed", 203)
        assert outcome.error_message == outcome.stderr == expected_message
        assert (
            outcome.stdout == '{"status":"provider_error","summary":null,"unfixable_reason":null,"touched_files":[]}\n'
        )
        assert requested == []
        assert list(session_tmp.iterdir()) == []

    @pytest.mark.parametrize(
        ("response_status", "exit_code"),
        [
            pytest.param(AgentResponseStatus.TIMEOUT, 202, id="timeout"),
            pytest.param(AgentResponseStatus.PROVIDER_ERROR, 203, id="provider-error"),
        ],
    )
    def test_provider_failures_leave_directories_in_place(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch, response_status: AgentResponseStatus, exit_code: int
    ) -> None:
        provider = FakeAgentProvider(AgentResponse(status=response_status, errors=["down"]))
        _use_provider(monkeypatch, provider)

        outcome = _run(git_repo)

        invocation = provider.requests[0].invocation
        assert outcome.exit_code == exit_code
        assert invocation is not None
        assert invocation.scratch_path.is_dir()
        assert invocation.control_path.is_dir()


def _stub_attempt(monkeypatch: pytest.MonkeyPatch, attempt: AgentAttempt) -> list[dict[str, object]]:
    """Replace run_direct_attempt with a recorder returning ``attempt`` and return the captured keyword arguments.

    The recorder reports ``attempt.env_withheld`` through the context callback before returning, as a provider does.
    """
    calls: list[dict[str, object]] = []

    def _record(**kwargs: object) -> AgentAttempt:
        calls.append(kwargs)
        context = kwargs["context"]
        assert isinstance(context, AgentAttemptContext)
        if attempt.env_withheld and context.on_env_withheld is not None:
            context.on_env_withheld(attempt.env_withheld)

        return attempt

    monkeypatch.setattr("dovo.engine.executors.agent_step.run_direct_attempt", _record)
    return calls


_FILTERED_LINE = "  ! Agent environment filtered: 3 variables withheld.\n"
_FIX_LINE = "  ! Fix: add names to agents.copilot.env_passthrough or pass --env-passthrough NAME.\n"


class ExecuteAgentStepToolsTests:
    @pytest.mark.parametrize(
        ("step_policy", "expected"),
        [
            pytest.param(None, "configured", id="omitted-uses-settings-policy"),
            pytest.param(ToolPolicy(), "step", id="explicit-empty-replaces-settings-policy"),
            pytest.param(
                ToolPolicy(allow=[ToolRule(capability=ToolCapability.SHELL, pattern="git status")]),
                "step",
                id="explicit-policy-replaces-settings-policy",
            ),
        ],
    )
    def test_step_policy_or_configured_policy_reaches_run_direct_attempt(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch, step_policy: ToolPolicy | None, expected: str
    ) -> None:
        calls = _stub_attempt(monkeypatch, AgentAttempt(status=AgentResponseStatus.NO_OP))
        configured = ToolPolicy(allow=[ToolRule(capability=ToolCapability.NETWORK, pattern="example.com")])
        settings = _settings().model_copy(update={"tools": configured})
        step = _step().model_copy(update={"tools": step_policy})

        _run(git_repo, step=step, agent=settings)

        assert calls[0]["tools"] == (configured if expected == "configured" else step_policy)


class ExecuteAgentStepEnvTests:
    def test_step_env_and_generated_metadata_are_forwarded_to_run_direct_attempt(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _stub_attempt(monkeypatch, AgentAttempt(status=AgentResponseStatus.NO_OP))
        step = StepBuilder.agent("Plan the change").with_env("MY_VAR", "x").build()

        _run(git_repo, step=step)

        context = calls[0]["context"]
        assert isinstance(context, AgentAttemptContext)
        assert context.env == {"MY_VAR": "x"}
        assert context.metadata_env == metadata_to_env(_METADATA)

    def test_withheld_names_emit_operator_stderr_line_and_one_session_log_event(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_attempt(
            monkeypatch, AgentAttempt(status=AgentResponseStatus.NO_OP, env_withheld=["AWS_KEY_X", "FOO", "ZED"])
        )
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        emitted: list[tuple[str, str]] = []

        _run(git_repo, session_log_dir=log_dir, on_output=lambda stream, line: emitted.append((stream, line)))

        events = [
            SessionLogEvent.model_validate_json(line)
            for line in (log_dir / "session.log").read_text(encoding="utf-8").splitlines()
        ]
        assert [stream for stream, _ in emitted] == ["stderr", "stdout"]
        assert emitted[0] == ("stderr", _FILTERED_LINE)
        assert [(event.event, event.step_id, event.env_withheld) for event in events] == [
            (SessionLogEventType.AGENT_ENV_FILTERED, "plan", "AWS_KEY_X,FOO,ZED")
        ]
        assert "AWS_KEY_X" not in "".join(line for _, line in emitted)

    def test_empty_withheld_list_emits_no_line_and_no_event(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_attempt(monkeypatch, AgentAttempt(status=AgentResponseStatus.NO_OP))
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        emitted: list[tuple[str, str]] = []

        _run(git_repo, session_log_dir=log_dir, on_output=lambda stream, line: emitted.append((stream, line)))

        assert [stream for stream, _ in emitted] == ["stdout"]
        assert not (log_dir / "session.log").exists()

    def test_withheld_report_is_emitted_for_a_timed_out_attempt(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_attempt(monkeypatch, AgentAttempt(status=AgentResponseStatus.TIMEOUT, env_withheld=["A", "B", "C"]))
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        emitted: list[tuple[str, str]] = []

        outcome = _run(git_repo, session_log_dir=log_dir, on_output=lambda stream, line: emitted.append((stream, line)))

        assert outcome.exit_code == 202
        assert [line for stream, line in emitted if stream == "stderr"] == [_FILTERED_LINE, _FIX_LINE]
        assert len((log_dir / "session.log").read_text(encoding="utf-8").splitlines()) == 1

    def test_withheld_report_without_a_session_log_dir_still_prints_the_console_line(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_attempt(monkeypatch, AgentAttempt(status=AgentResponseStatus.NO_OP, env_withheld=["A", "B", "C"]))
        emitted: list[tuple[str, str]] = []

        _run(git_repo, on_output=lambda stream, line: emitted.append((stream, line)))

        assert ("stderr", _FILTERED_LINE) in emitted


class BuildAgentStepRunnerTests:
    def test_runner_built_without_active_worktree_fails_with_worktree_required_message(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[object] = []
        monkeypatch.setattr(
            "dovo.engine.executors.agent_step.run_direct_attempt",
            lambda **kwargs: calls.append(kwargs) or AgentAttempt(status=AgentResponseStatus.NO_OP),
        )

        runner = build_agent_step_runner(
            _settings(),
            worktree_active=False,
            session_tmp_dir=_session_tmp_dir(git_repo),
            main_checkout=git_repo,
            session_log_dir=None,
            session_dir=None,
        )
        outcome = runner(_step(), git_repo, None, None, RecordingProgressSink(), _METADATA)

        assert outcome.status == "failed"
        assert WORKTREE_REQUIRED_MESSAGE in (outcome.error_message or "")
        assert calls == []

    def test_runner_passes_bound_settings_worktree_path_and_callback_to_the_provider_attempt(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _record_direct_attempts(monkeypatch)
        settings = _settings()
        step = _step()
        emitted: list[tuple[str, str]] = []

        runner = build_agent_step_runner(
            settings,
            worktree_active=True,
            session_tmp_dir=_session_tmp_dir(git_repo),
            main_checkout=git_repo,
            session_log_dir=None,
            session_dir=None,
        )
        outcome = runner(
            step,
            git_repo,
            lambda stream, line: emitted.append((stream, line)),
            None,
            RecordingProgressSink(),
            _METADATA,
        )

        assert [(call["instruction"], call["settings"], call["worktree_path"]) for call in calls] == [
            (step.prompt, settings, git_repo)
        ]
        assert emitted == [("stdout", outcome.stdout)]


def _record_contexts(monkeypatch: pytest.MonkeyPatch, attempt: AgentAttempt | None = None) -> list[AgentAttemptContext]:
    contexts: list[AgentAttemptContext] = []

    def _record(*, context: AgentAttemptContext | None, **kwargs: object) -> AgentAttempt:
        assert context is not None
        contexts.append(context)
        return attempt or AgentAttempt(status=AgentResponseStatus.NO_OP)

    monkeypatch.setattr("dovo.engine.executors.agent_step.run_direct_attempt", _record)
    return contexts


class AgentStepRejectedPatchTests:
    def test_rejected_attempt_failed_outcome_carries_path_and_error_message_lists_it(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _record_contexts(
            monkeypatch,
            AgentAttempt(
                status=AgentResponseStatus.PROVIDER_ERROR,
                diagnostics=["gate said no", "Discarded edit saved to P."],
                rejected_patch_path="P",
            ),
        )

        outcome = _run(git_repo)

        assert outcome.status == "failed"
        assert outcome.exit_code == 203
        assert outcome.rejected_patch_path == "P"
        assert (outcome.error_message or "").endswith("Discarded edit saved to P.")

    @pytest.mark.parametrize(
        ("metadata", "filename"),
        [
            pytest.param(
                ExecutionMetadata(step=StepMetadata(id="code", name="code", index=1, attempt=2)),
                "code_attempt_2.patch",
                id="step_id_attempt",
            ),
            pytest.param(
                ExecutionMetadata(step=StepMetadata(id="code", name="code", index=1), loop_iteration=2),
                "code_iter_2_attempt_1.patch",
                id="loop_iteration_adds_iter_segment",
            ),
        ],
    )
    def test_destination_is_session_dir_rejected_patch_named_for_the_attempt(
        self,
        git_repo: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        metadata: ExecutionMetadata,
        filename: str,
    ) -> None:
        contexts = _record_contexts(monkeypatch)

        _run(git_repo, step=StepBuilder.agent("Plan").with_id("code").build(), session_dir=tmp_path, metadata=metadata)

        assert [context.rejected_patch_destination for context in contexts] == [tmp_path / "rejected" / filename]

    def test_missing_session_dir_passes_no_destination(self, git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        contexts = _record_contexts(monkeypatch)

        _run(git_repo, session_dir=None)

        assert [context.rejected_patch_destination for context in contexts] == [None]


_HEARTBEAT_WAIT_SECONDS = 20.0


class ExecuteAgentStepHeartbeatTests:
    @staticmethod
    def _use_fast_heartbeat(monkeypatch: pytest.MonkeyPatch, beat_readings: list[float]) -> None:
        """Shrink the heartbeat interval and script the Stopwatch clock: 0.0 off the heartbeat thread, the given readings then 91.0 forever on it."""
        readings = itertools.chain(beat_readings, itertools.repeat(91.0))

        def clock() -> float:
            return next(readings) if threading.current_thread().name.startswith("agent-heartbeat-") else 0.0

        monkeypatch.setattr("dovo.engine.executors.agent_step.AGENT_HEARTBEAT_INTERVAL_SECONDS", 0.01)
        monkeypatch.setattr("dovo.common.timing.monotonic", clock)

    def test_running_attempt_emits_elapsed_heartbeats_before_the_final_summary(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._use_fast_heartbeat(monkeypatch, [30.4, 60.9])
        lines: list[str] = []
        two_heartbeats_seen = threading.Event()
        provider_released: list[bool] = []

        def on_output(stream_name: str, line: str) -> None:
            lines.append(line)
            if sum("agent running" in seen for seen in lines) >= 2:
                two_heartbeats_seen.set()

        def hold_until_heartbeats(request: object) -> None:
            provider_released.append(two_heartbeats_seen.wait(_HEARTBEAT_WAIT_SECONDS))

        _use_provider(monkeypatch, FakeAgentProvider(_no_op(), on_call=hold_until_heartbeats))

        _run(git_repo, step=StepBuilder.agent("Plan the change").with_id("plan").build(), on_output=on_output)

        assert provider_released == [True]
        assert lines[:2] == ["  agent running (30s)\n", "  agent running (60s)\n"]
        assert json.loads(lines[-1])["status"] == "no_op"

    def test_heartbeat_thread_is_gone_after_the_step_returns(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._use_fast_heartbeat(monkeypatch, [30.4])
        lines: list[str] = []
        heartbeat_seen = threading.Event()
        names_during_attempt: list[list[str]] = []

        def on_output(stream_name: str, line: str) -> None:
            lines.append(line)
            heartbeat_seen.set()

        def hold_until_heartbeat(request: object) -> None:
            heartbeat_seen.wait(_HEARTBEAT_WAIT_SECONDS)
            names_during_attempt.append([thread.name for thread in threading.enumerate()])

        _use_provider(monkeypatch, FakeAgentProvider(_no_op(), on_call=hold_until_heartbeat))

        _run(git_repo, step=StepBuilder.agent("Plan the change").with_id("plan").build(), on_output=on_output)

        assert "agent-heartbeat-plan" in names_during_attempt[0]
        assert "agent-heartbeat-plan" not in [thread.name for thread in threading.enumerate()]
        assert json.loads(lines[-1])["status"] == "no_op"

    def test_heartbeat_callback_failure_leaves_the_attempt_outcome_unchanged(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._use_fast_heartbeat(monkeypatch, [30.4])
        heartbeat_attempted = threading.Event()

        def on_output(stream_name: str, line: str) -> None:
            if "agent running" in line:
                heartbeat_attempted.set()
                raise RuntimeError("terminal gone")

        def hold_until_heartbeat(request: object) -> None:
            heartbeat_attempted.wait(_HEARTBEAT_WAIT_SECONDS)

        _use_provider(monkeypatch, FakeAgentProvider(_no_op(), on_call=hold_until_heartbeat))

        outcome = _run(git_repo, step=StepBuilder.agent("Plan the change").with_id("plan").build(), on_output=on_output)

        assert heartbeat_attempted.is_set()
        assert (outcome.status, outcome.exit_code) == ("completed", 0)


class ExecuteAgentStepProgressTests:
    def test_progress_is_forwarded_to_the_attempt_context(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sink = RecordingProgressSink()
        item = AgentProgress(kind=AgentProgressKind.READ, target="x.py")

        def report_through_request(request: object) -> None:
            assert isinstance(request, AgentRequest)
            assert request.on_progress is not None
            request.on_progress(item)

        _use_provider(monkeypatch, FakeAgentProvider(_no_op(), on_call=report_through_request))

        _run(git_repo, progress=sink)

        assert sink.reported == [item]

    @pytest.mark.parametrize(
        ("quiet_seconds", "heartbeat_expected"),
        [
            pytest.param(0.0, False, id="recent-progress-suppresses"),
            pytest.param(None, True, id="no-progress-beats"),
        ],
    )
    def test_heartbeat_follows_the_progress_quiet_window(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch, quiet_seconds: float | None, heartbeat_expected: bool
    ) -> None:
        ExecuteAgentStepHeartbeatTests._use_fast_heartbeat(monkeypatch, [30.4])
        second_tick_seen = threading.Event()
        lines: list[str] = []

        class TickCountingSink(RecordingProgressSink):
            ticks = 0

            def seconds_since_last_report(self) -> float | None:
                self.ticks += 1
                if self.ticks >= 2:
                    second_tick_seen.set()
                return super().seconds_since_last_report()

        def hold_until_second_tick(request: object) -> None:
            second_tick_seen.wait(_HEARTBEAT_WAIT_SECONDS)

        _use_provider(monkeypatch, FakeAgentProvider(_no_op(), on_call=hold_until_second_tick))

        _run(
            git_repo,
            step=StepBuilder.agent("Plan the change").with_id("plan").build(),
            on_output=lambda stream_name, line: lines.append(line),
            progress=TickCountingSink(quiet_seconds),
        )

        assert any("agent running" in line for line in lines) is heartbeat_expected
