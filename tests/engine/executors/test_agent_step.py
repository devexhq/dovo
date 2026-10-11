"""Contract tests for agent step execution through resolved providers in a Git worktree."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.common.tool_policy import ToolCapability, ToolPolicy, ToolRule
from dovo.core.agents import (
    AgentAttempt,
    AgentAttemptContext,
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
from dovo.core.catalog.definitions import StepDefinition
from dovo.core.git import GitRunner
from dovo.core.sessions import SessionLogEvent, SessionLogEventType
from dovo.engine.executors.agent_step import (
    AGENT_OUTCOME_EXIT_CODES,
    BLANK_PROMPT_MESSAGE,
    MISSING_SETTINGS_MESSAGE,
    WORKTREE_REQUIRED_MESSAGE,
    build_agent_step_runner,
    execute_agent_step,
)
from dovo.engine.executors.metadata import metadata_to_env
from dovo.engine.executors.models import ExecutionMetadata, StepDispatchOutcome, StepMetadata
from tests.harness import AGENT_ADAPTER_FACTORY, FakeAgentProvider, new_file_diff
from tests.harness.builders import StepBuilder

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
    on_output: Callable[[str, str], None] | None = None,
) -> StepDispatchOutcome:
    return execute_agent_step(
        step or _step(),
        agent=agent or _settings(),
        worktree_path=worktree,
        worktree_active=worktree_active,
        session_tmp_dir=session_tmp_dir or _session_tmp_dir(worktree),
        main_checkout=worktree,
        session_log_dir=session_log_dir,
        metadata=_METADATA,
        on_output=on_output,
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


def _forbid_git_apply(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def _record(name: str) -> Callable[..., tuple[int, str, str]]:
        def _spy(*args: object, **kwargs: object) -> tuple[int, str, str]:
            calls.append(name)
            raise AssertionError(f"GitRunner.{name} must not run")

        return _spy

    monkeypatch.setattr(GitRunner, "apply_check", staticmethod(_record("apply_check")))
    monkeypatch.setattr(GitRunner, "apply", staticmethod(_record("apply")))
    return calls


class AgentOutcomeMappingTests:
    def test_mapping_covers_every_status_with_issue_codes(self) -> None:
        """[tier-1/unit] AGENT_OUTCOME_EXIT_CODES: equals {PROPOSED_PATCH: 0, NO_OP: 0, UNFIXABLE: 201, TIMEOUT: 202, PROVIDER_ERROR: 203, BLOCKED: 204} and its keys equal set(AgentResponseStatus)."""
        assert dict(AGENT_OUTCOME_EXIT_CODES) == {
            AgentResponseStatus.PROPOSED_PATCH: 0,
            AgentResponseStatus.NO_OP: 0,
            AgentResponseStatus.UNFIXABLE: 201,
            AgentResponseStatus.TIMEOUT: 202,
            AgentResponseStatus.PROVIDER_ERROR: 203,
            AgentResponseStatus.BLOCKED: 204,
        }
        assert set(AGENT_OUTCOME_EXIT_CODES) == set(AgentResponseStatus)


class ExecuteAgentStepBlockedTests:
    def test_blocked_attempt_fails_step_with_exit_204_and_blocked_summary(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-2/integration] execute_agent_step: provider returns BLOCKED with one denial -> StepDispatchOutcome(status='failed', exit_code=204), stdout summary line status 'blocked', stderr contains 'AGENT_PERMISSION_BLOCKED'."""
        response = blocked_response(
            duration_ms=1, denials=[AgentDenial(tool="bash", message="denied", capability=ToolCapability.SHELL)]
        )
        _use_provider(monkeypatch, FakeAgentProvider(response))

        outcome = _run(git_repo)

        assert outcome.status == "failed"
        assert outcome.exit_code == 204
        assert _summary(outcome)["status"] == "blocked"
        assert "AGENT_PERMISSION_BLOCKED" in outcome.stderr
        assert "tools: {allow: [{capability: shell}]}" in outcome.stderr


class ExecuteAgentStepUnsupportedPolicyTests:
    def test_unsupported_policy_response_fails_step_with_exit_203(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-2/integration] execute_agent_step: provider returns PROVIDER_ERROR with the unsupported message -> StepDispatchOutcome(status='failed', exit_code=203) and stderr contains 'AGENT_TOOL_POLICY_UNSUPPORTED'."""
        response = provider_error_response(duration_ms=1, errors=[tool_policy_unsupported_message("copilot")])
        _use_provider(monkeypatch, FakeAgentProvider(response))

        outcome = _run(git_repo)

        assert outcome.status == "failed"
        assert outcome.exit_code == 203
        assert "AGENT_TOOL_POLICY_UNSUPPORTED" in outcome.stderr


class ExecuteAgentStepRequestTests:
    def test_valid_step_forwards_prompt_settings_worktree_and_timeout_to_run_direct_attempt(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] execute_agent_step: an active-worktree step with prompt 'Plan the change' and timeout_seconds 45 calls run_direct_attempt once with instruction='Plan the change', the given settings, worktree_path=git_repo, timeout_seconds=45."""
        calls: list[dict[str, object]] = []

        def _record(
            *,
            instruction: str,
            settings: ResolvedAgentSettings,
            worktree_path: Path,
            timeout_seconds: int,
            tools: ToolPolicy,
            context: AgentAttemptContext | None,
        ) -> AgentAttempt:
            calls.append(
                {
                    "instruction": instruction,
                    "settings": settings,
                    "worktree_path": worktree_path,
                    "timeout_seconds": timeout_seconds,
                }
            )
            return AgentAttempt(status=AgentResponseStatus.NO_OP)

        monkeypatch.setattr("dovo.engine.executors.agent_step.run_direct_attempt", _record)

        _run(git_repo)

        assert calls == [
            {
                "instruction": "Plan the change",
                "settings": _settings(),
                "worktree_path": git_repo,
                "timeout_seconds": 45,
            }
        ]

    def test_missing_settings_fail_before_provider_lookup(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] execute_agent_step: agent=None with an active worktree fails with the missing-settings diagnostic and never asks the factory for an adapter."""
        requested = _use_provider(monkeypatch, FakeAgentProvider(_no_op()))

        outcome = execute_agent_step(
            _step(),
            agent=None,
            worktree_path=git_repo,
            worktree_active=True,
            session_tmp_dir=_session_tmp_dir(git_repo),
            main_checkout=git_repo,
            session_log_dir=None,
            metadata=_METADATA,
            on_output=None,
        )

        assert outcome.status == "failed"
        assert outcome.exit_code == 203
        assert outcome.error_message == MISSING_SETTINGS_MESSAGE
        assert _summary(outcome)["status"] == "provider_error"
        assert requested == []

    def test_blank_interpolated_prompt_fails_before_provider_lookup(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] execute_agent_step: a step whose prompt is whitespace fails with the blank-prompt diagnostic and no factory call."""
        requested = _use_provider(monkeypatch, FakeAgentProvider(_no_op()))

        outcome = _run(git_repo, step=_step(prompt="   "))

        assert outcome.status == "failed"
        assert outcome.exit_code == 203
        assert outcome.error_message == BLANK_PROMPT_MESSAGE
        assert requested == []


class ExecuteAgentStepWorktreeTests:
    def test_inactive_worktree_fails_before_provider_or_patch(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] execute_agent_step: worktree_active=False fails with the exact worktree diagnostic and records zero provider, apply_check, and apply calls."""
        requested = _use_provider(monkeypatch, FakeAgentProvider(_patch()))
        apply_calls = _forbid_git_apply(monkeypatch)

        outcome = _run(git_repo, worktree_active=False)

        assert outcome.status == "failed"
        assert outcome.exit_code == 203
        assert outcome.error_message == WORKTREE_REQUIRED_MESSAGE
        assert outcome.stderr == WORKTREE_REQUIRED_MESSAGE
        assert (
            outcome.stdout == '{"status":"provider_error","summary":null,"unfixable_reason":null,"touched_files":[]}\n'
        )
        assert requested == []
        assert apply_calls == []
        assert not (git_repo / "a.txt").exists()


class ExecuteAgentStepOutputTests:
    def test_no_op_writes_exact_summary_line_and_completes(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] execute_agent_step: NO_OP with a summary completes with exit 0, empty stderr, no error_message, and the exact one-line JSON stdout."""
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
        """[tier-1/unit] execute_agent_step: UNFIXABLE/TIMEOUT/PROVIDER_ERROR fail with exit 201/202/203, stderr == error_message == the diagnostic, and the matching stdout status."""
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
        """[tier-1/unit] execute_agent_step: PROPOSED_PATCH with a clean new-file diff completes with exit 0, empty stderr, and one-line stdout status 'proposed_patch' with touched_files ['a.txt']."""
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
        """[tier-1/unit] execute_agent_step: NO_OP with summary None and multi-line raw_text writes a single-line stdout whose JSON summary equals the raw text."""
        _use_provider(monkeypatch, FakeAgentProvider(_no_op(summary=None, raw_text="plan:\n1. do x")))

        outcome = _run(git_repo)

        assert _summary(outcome)["summary"] == "plan:\n1. do x"

    def test_output_callback_receives_summary_line_once(self, git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """[tier-1/unit] execute_agent_step: on_output is called exactly once with ('stdout', <the returned stdout>)."""
        _use_provider(monkeypatch, FakeAgentProvider(_no_op()))
        received: list[tuple[str, str]] = []

        outcome = _run(git_repo, on_output=lambda stream, line: received.append((stream, line)))

        assert received == [("stdout", outcome.stdout)]

    def test_callback_failure_fails_attempt_and_drops_success_classification(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] execute_agent_step: a callback raising RuntimeError('ui down') after a clean patch fails with the callback diagnostic and stored status 'provider_error' keeping touched_files."""
        _use_provider(monkeypatch, FakeAgentProvider(_patch("a.txt")))

        def _broken(stream: str, line: str) -> None:
            raise RuntimeError("ui down")

        outcome = _run(git_repo, on_output=_broken)

        assert outcome.status == "failed"
        assert outcome.exit_code == 203
        assert outcome.error_message == "Agent output callback error: ui down"
        assert _summary(outcome)["status"] == "provider_error"
        assert _summary(outcome)["touched_files"] == ["a.txt"]


class ExecuteAgentStepScratchTests:
    def test_dispatch_allocates_once_and_passes_context_to_the_attempt(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] execute_agent_step: with a real session_tmp_dir, run_direct_attempt receives invocation whose scratch_path and control_path exist and lie under session_tmp_dir/steps/<step.id>/agent/."""
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
        """[tier-1/integration] execute_agent_step: session_tmp_dir None returns StepDispatchOutcome(status='failed', exit_code=203) whose error_message equals scratch_unavailable_message(step.id, detail) followed by a 'Fix:' block, and get_agent_adapter is never called."""
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
            metadata=_METADATA,
            on_output=None,
        )

        assert outcome.status == "failed"
        assert outcome.exit_code == 203
        assert outcome.error_message == (
            scratch_unavailable_message(step.id, "the session temp directory is unavailable")
            + "\nFix:\n- restore access to the session temp directory or correct its storage path."
        )
        assert requested == []

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permission bits")
    def test_directory_creation_failure_leaves_worktree_files_unchanged(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] execute_agent_step: an unwritable session_tmp_dir returns exit_code 203 and the worktree's file contents and git status are byte-identical before and after."""
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
        assert requested == []
        assert (git_repo / "README.md").read_bytes() == readme_before
        assert not (git_repo / "a.txt").exists()
        assert _porcelain(git_repo) == ""

    @pytest.mark.parametrize(
        ("guard", "expected_message"),
        [
            pytest.param("worktree", WORKTREE_REQUIRED_MESSAGE, id="worktree-inactive"),
            pytest.param("settings", MISSING_SETTINGS_MESSAGE, id="no-settings"),
            pytest.param("prompt", BLANK_PROMPT_MESSAGE, id="blank-prompt"),
        ],
    )
    def test_guards_run_before_allocation(self, git_repo: Path, guard: str, expected_message: str) -> None:
        """[tier-1/unit] execute_agent_step: worktree_active False, agent None, and a blank prompt each return their existing diagnostic and create no directory under session_tmp_dir."""
        session_tmp = _session_tmp_dir(git_repo)

        outcome = execute_agent_step(
            _step(prompt="   " if guard == "prompt" else "Plan the change"),
            agent=None if guard == "settings" else _settings(),
            worktree_path=git_repo,
            worktree_active=guard != "worktree",
            session_tmp_dir=session_tmp,
            main_checkout=git_repo,
            session_log_dir=None,
            metadata=_METADATA,
            on_output=None,
        )

        assert outcome.error_message == expected_message
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
        """[tier-1/integration] execute_agent_step: provider timeout and provider-error attempts each return their mapped exit code (202, 203) and the allocated scratch and control directories still exist afterward."""
        provider = FakeAgentProvider(AgentResponse(status=response_status, errors=["down"]))
        _use_provider(monkeypatch, provider)

        outcome = _run(git_repo)

        invocation = provider.requests[0].invocation
        assert outcome.exit_code == exit_code
        assert invocation is not None
        assert invocation.scratch_path.is_dir()
        assert invocation.control_path.is_dir()


def _stub_attempt(monkeypatch: pytest.MonkeyPatch, attempt: AgentAttempt) -> list[dict[str, object]]:
    """Replace run_direct_attempt with a recorder returning ``attempt`` and return the captured keyword arguments."""
    calls: list[dict[str, object]] = []

    def _record(**kwargs: object) -> AgentAttempt:
        calls.append(kwargs)
        return attempt

    monkeypatch.setattr("dovo.engine.executors.agent_step.run_direct_attempt", _record)
    return calls


_FILTERED_LINE = (
    "Agent environment filtered: 3 variables withheld. "
    "Fix: add names to agent.env_passthrough or pass --env-passthrough NAME.\n"
)


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
        """[tier-2/integration] execute_agent_step: run_direct_attempt receives tools == step.tools when set, else agent.tools (parametrized)."""
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
        """[tier-1/integration] execute_agent_step: run_direct_attempt receives env == step.env and metadata_env == metadata_to_env(metadata)."""
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
        """[tier-1/integration] execute_agent_step: env_withheld ['AWS_KEY_X','FOO','ZED'] sends 'Agent environment filtered: 3 variables withheld. Fix: add names to agent.env_passthrough or pass --env-passthrough NAME.\\n' on stream 'stderr' and appends one agent_env_filtered event with env_withheld == 'AWS_KEY_X,FOO,ZED'."""
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
        assert ("stderr", _FILTERED_LINE) in emitted
        assert [(event.event, event.step_id, event.env_withheld) for event in events] == [
            (SessionLogEventType.AGENT_ENV_FILTERED, "plan", "AWS_KEY_X,FOO,ZED")
        ]
        assert "AWS_KEY_X" not in "".join(line for _, line in emitted)

    def test_empty_withheld_list_emits_no_line_and_no_event(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] execute_agent_step: env_withheld [] sends no stderr callback and writes no session.log line."""
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
        """[tier-1/integration] execute_agent_step: a TIMEOUT attempt with env_withheld still emits the stderr line and the event."""
        _stub_attempt(monkeypatch, AgentAttempt(status=AgentResponseStatus.TIMEOUT, env_withheld=["A", "B", "C"]))
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        emitted: list[tuple[str, str]] = []

        outcome = _run(git_repo, session_log_dir=log_dir, on_output=lambda stream, line: emitted.append((stream, line)))

        assert outcome.exit_code == 202
        assert ("stderr", _FILTERED_LINE) in emitted
        assert len((log_dir / "session.log").read_text(encoding="utf-8").splitlines()) == 1

    def test_withheld_report_without_a_session_log_dir_still_prints_the_console_line(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] execute_agent_step: env_withheld set and session_log_dir None sends the stderr line and raises nothing."""
        _stub_attempt(monkeypatch, AgentAttempt(status=AgentResponseStatus.NO_OP, env_withheld=["A", "B", "C"]))
        emitted: list[tuple[str, str]] = []

        _run(git_repo, on_output=lambda stream, line: emitted.append((stream, line)))

        assert ("stderr", _FILTERED_LINE) in emitted


class BuildAgentStepRunnerTests:
    def test_runner_built_without_active_worktree_fails_with_worktree_required_message(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] build_agent_step_runner: build_agent_step_runner(settings, worktree_active=False) returns a runner whose call yields status 'failed' with error_message containing WORKTREE_REQUIRED_MESSAGE and run_direct_attempt is called zero times."""
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
        )
        outcome = runner(_step(), git_repo, None, _METADATA)

        assert outcome.status == "failed"
        assert WORKTREE_REQUIRED_MESSAGE in (outcome.error_message or "")
        assert calls == []

    def test_runner_passes_bound_settings_worktree_path_and_callback_to_the_provider_attempt(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] build_agent_step_runner: build_agent_step_runner(settings, worktree_active=True) runner called with (step, worktree_path, on_output) calls run_direct_attempt once with settings=settings, worktree_path=worktree_path, instruction=step.prompt, and delivers exactly one ('stdout', summary_line) to on_output."""
        calls: list[dict[str, object]] = []

        def _record(
            *,
            instruction: str,
            settings: ResolvedAgentSettings,
            worktree_path: Path,
            timeout_seconds: int,
            tools: ToolPolicy,
            context: AgentAttemptContext | None,
        ) -> AgentAttempt:
            calls.append({"instruction": instruction, "settings": settings, "worktree_path": worktree_path})
            return AgentAttempt(status=AgentResponseStatus.NO_OP)

        monkeypatch.setattr("dovo.engine.executors.agent_step.run_direct_attempt", _record)
        settings = _settings()
        step = _step()
        emitted: list[tuple[str, str]] = []

        runner = build_agent_step_runner(
            settings,
            worktree_active=True,
            session_tmp_dir=_session_tmp_dir(git_repo),
            main_checkout=git_repo,
            session_log_dir=None,
        )
        outcome = runner(step, git_repo, lambda stream, line: emitted.append((stream, line)), _METADATA)

        assert calls == [{"instruction": step.prompt, "settings": settings, "worktree_path": git_repo}]
        assert emitted == [("stdout", outcome.stdout)]
