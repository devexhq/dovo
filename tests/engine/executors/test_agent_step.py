"""Contract tests for agent step execution through resolved providers in a Git sandbox."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.harness import AGENT_ADAPTER_FACTORY, FakeAgentProvider, new_file_diff
from tests.harness.builders import StepBuilder
from worktree.core.agents import (
    AgentAttempt,
    AgentResponse,
    AgentResponseStatus,
    BaseAgentProvider,
    ResolvedAgentSettings,
)
from worktree.core.catalog.definitions import StepDefinition
from worktree.core.git import GitRunner
from worktree.engine.executors.agent_step import (
    AGENT_OUTCOME_EXIT_CODES,
    BLANK_PROMPT_MESSAGE,
    MISSING_SETTINGS_MESSAGE,
    SANDBOX_REQUIRED_MESSAGE,
    build_agent_step_runner,
    execute_agent_step,
)
from worktree.engine.executors.models import StepDispatchOutcome

_NO_OP_SUMMARY = "Inspected the repository; no edits were required."


def _settings(provider: str = "ollama") -> ResolvedAgentSettings:
    return ResolvedAgentSettings(provider=provider, model="m", endpoint="http://e", temperature=0.7, max_tokens=512)


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
    sandbox: Path,
    *,
    step: StepDefinition | None = None,
    agent: ResolvedAgentSettings | None = None,
    sandbox_active: bool = True,
    on_output: Callable[[str, str], None] | None = None,
) -> StepDispatchOutcome:
    return execute_agent_step(
        step or _step(),
        agent=agent or _settings(),
        sandbox_path=sandbox,
        sandbox_active=sandbox_active,
        on_output=on_output,
    )


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
        """[tier-1/unit] AGENT_OUTCOME_EXIT_CODES: equals {PROPOSED_PATCH: 0, NO_OP: 0, UNFIXABLE: 201, TIMEOUT: 202, PROVIDER_ERROR: 203} and its keys equal set(AgentResponseStatus)."""
        assert dict(AGENT_OUTCOME_EXIT_CODES) == {
            AgentResponseStatus.PROPOSED_PATCH: 0,
            AgentResponseStatus.NO_OP: 0,
            AgentResponseStatus.UNFIXABLE: 201,
            AgentResponseStatus.TIMEOUT: 202,
            AgentResponseStatus.PROVIDER_ERROR: 203,
        }
        assert set(AGENT_OUTCOME_EXIT_CODES) == set(AgentResponseStatus)


class ExecuteAgentStepRequestTests:
    def test_valid_step_forwards_prompt_settings_sandbox_and_timeout_to_run_direct_attempt(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] execute_agent_step: an active-sandbox step with prompt 'Plan the change' and timeout_seconds 45 calls run_direct_attempt once with instruction='Plan the change', the given settings, sandbox_path=git_repo, timeout_seconds=45."""
        calls: list[dict[str, object]] = []

        def _record(
            *, instruction: str, settings: ResolvedAgentSettings, sandbox_path: Path, timeout_seconds: int
        ) -> AgentAttempt:
            calls.append(
                {
                    "instruction": instruction,
                    "settings": settings,
                    "sandbox_path": sandbox_path,
                    "timeout_seconds": timeout_seconds,
                }
            )
            return AgentAttempt(status=AgentResponseStatus.NO_OP)

        monkeypatch.setattr("worktree.engine.executors.agent_step.run_direct_attempt", _record)

        _run(git_repo)

        assert calls == [
            {
                "instruction": "Plan the change",
                "settings": _settings(),
                "sandbox_path": git_repo,
                "timeout_seconds": 45,
            }
        ]

    def test_missing_settings_fail_before_provider_lookup(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] execute_agent_step: agent=None with an active sandbox fails with the missing-settings diagnostic and never asks the factory for an adapter."""
        requested = _use_provider(monkeypatch, FakeAgentProvider(_no_op()))

        outcome = execute_agent_step(_step(), agent=None, sandbox_path=git_repo, sandbox_active=True, on_output=None)

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


class ExecuteAgentStepSandboxTests:
    def test_inactive_sandbox_fails_before_provider_or_patch(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] execute_agent_step: sandbox_active=False fails with the exact sandbox diagnostic and records zero provider, apply_check, and apply calls."""
        requested = _use_provider(monkeypatch, FakeAgentProvider(_patch()))
        apply_calls = _forbid_git_apply(monkeypatch)

        outcome = _run(git_repo, sandbox_active=False)

        assert outcome.status == "failed"
        assert outcome.exit_code == 203
        assert outcome.error_message == SANDBOX_REQUIRED_MESSAGE
        assert outcome.stderr == SANDBOX_REQUIRED_MESSAGE
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
                ["Agent timed out after 45s (provider=ollama)."],
                "Agent timed out after 45s (provider=ollama).",
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

    def test_unappliable_patch_fails_as_provider_error_203(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] execute_agent_step: PROPOSED_PATCH whose new-file diff targets an existing a.txt fails with exit 203, stdout status 'provider_error', and error_message starting 'Patch does not apply cleanly'."""
        (git_repo / "a.txt").write_text("existing\n")
        _use_provider(monkeypatch, FakeAgentProvider(_patch("a.txt")))

        outcome = _run(git_repo)

        assert outcome.status == "failed"
        assert outcome.exit_code == 203
        assert (outcome.error_message or "").startswith("Patch does not apply cleanly")
        assert _summary(outcome)["status"] == "provider_error"

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


class BuildAgentStepRunnerTests:
    def test_runner_built_without_active_sandbox_fails_with_sandbox_required_message(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] build_agent_step_runner: build_agent_step_runner(settings, sandbox_active=False) returns a runner whose call yields status 'failed' with error_message containing SANDBOX_REQUIRED_MESSAGE and run_direct_attempt is called zero times."""
        calls: list[object] = []
        monkeypatch.setattr(
            "worktree.engine.executors.agent_step.run_direct_attempt",
            lambda **kwargs: calls.append(kwargs) or AgentAttempt(status=AgentResponseStatus.NO_OP),
        )

        runner = build_agent_step_runner(_settings(), sandbox_active=False)
        outcome = runner(_step(), git_repo, None)

        assert outcome.status == "failed"
        assert SANDBOX_REQUIRED_MESSAGE in (outcome.error_message or "")
        assert calls == []

    def test_runner_passes_bound_settings_sandbox_path_and_callback_to_the_provider_attempt(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] build_agent_step_runner: build_agent_step_runner(settings, sandbox_active=True) runner called with (step, sandbox_path, on_output) calls run_direct_attempt once with settings=settings, sandbox_path=sandbox_path, instruction=step.prompt, and delivers exactly one ('stdout', summary_line) to on_output."""
        calls: list[dict[str, object]] = []

        def _record(
            *, instruction: str, settings: ResolvedAgentSettings, sandbox_path: Path, timeout_seconds: int
        ) -> AgentAttempt:
            calls.append({"instruction": instruction, "settings": settings, "sandbox_path": sandbox_path})
            return AgentAttempt(status=AgentResponseStatus.NO_OP)

        monkeypatch.setattr("worktree.engine.executors.agent_step.run_direct_attempt", _record)
        settings = _settings()
        step = _step()
        emitted: list[tuple[str, str]] = []

        outcome = build_agent_step_runner(settings, sandbox_active=True)(
            step, git_repo, lambda stream, line: emitted.append((stream, line))
        )

        assert calls == [{"instruction": step.prompt, "settings": settings, "sandbox_path": git_repo}]
        assert emitted == [("stdout", outcome.stdout)]
