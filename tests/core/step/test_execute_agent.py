"""Contract tests for agent step execution through resolved providers in a Git sandbox."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.harness import AGENT_ADAPTER_FACTORY, FakeAgentProvider, new_file_diff
from tests.harness.builders import StepBuilder
from worktree.core.agents import (
    AgentRequest,
    AgentResponse,
    AgentResponseStatus,
    BaseAgentProvider,
    CliDirectMutationAdapter,
    CliMutationOutcome,
    CliMutationRunRequest,
    ResolvedAgentSettings,
)
from worktree.core.git import GitPlumbingTimeoutError, GitRunner
from worktree.core.step.models import StepDefinition, StepDispatchOutcome
from worktree.core.step.services.execute_agent import (
    BLANK_PROMPT_MESSAGE,
    MISSING_SETTINGS_MESSAGE,
    SANDBOX_REQUIRED_MESSAGE,
    execute_agent_step,
)

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


def _porcelain(repo: Path) -> str:
    return subprocess.run(["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True).stdout


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


class ExecuteAgentStepRequestTests:
    def test_request_carries_instruction_settings_sandbox_and_timeout(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] execute_agent_step: with ollama settings and timeout_seconds 45, the factory is asked for 'ollama' once and propose_fix receives the exact direct AgentRequest."""
        provider = FakeAgentProvider(_no_op())
        requested = _use_provider(monkeypatch, provider)

        _run(git_repo)

        assert requested == ["ollama"]
        assert provider.requests == [
            AgentRequest(
                mode="direct",
                instruction="Plan the change",
                payload=None,
                sandbox_path=git_repo,
                timeout_seconds=45,
                model="m",
                endpoint="http://e",
                temperature=0.7,
                max_tokens=512,
                max_files=None,
                max_patch_kb=None,
                reject_binary_changes=None,
            )
        ]

    def test_missing_settings_fail_before_provider_lookup(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] execute_agent_step: agent=None with an active sandbox fails with the missing-settings diagnostic and never asks the factory for an adapter."""
        requested = _use_provider(monkeypatch, FakeAgentProvider(_no_op()))

        outcome = execute_agent_step(_step(), agent=None, sandbox_path=git_repo, sandbox_active=True, on_output=None)

        assert outcome.status == "failed"
        assert outcome.exit_code == 1
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
        assert outcome.error_message == BLANK_PROMPT_MESSAGE
        assert requested == []

    def test_unregistered_provider_fails_with_classified_diagnostic(self, git_repo: Path) -> None:
        """[tier-1/unit] execute_agent_step: provider 'openai' fails with the AGENT_PROVIDER_UNSUPPORTED diagnostic and stdout status 'provider_error'."""
        outcome = _run(git_repo, agent=_settings("openai"))

        assert outcome.status == "failed"
        assert "Unsupported agent provider 'openai' (AGENT_PROVIDER_UNSUPPORTED)" in (outcome.error_message or "")
        assert _summary(outcome)["status"] == "provider_error"

    def test_provider_exception_returns_failed_provider_error(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] execute_agent_step: a provider whose propose_fix raises RuntimeError('boom') fails with exit 1 and 'Agent provider error: boom'."""

        class _Exploding(BaseAgentProvider):
            def propose_fix(self, request: AgentRequest) -> AgentResponse:
                raise RuntimeError("boom")

        _use_provider(monkeypatch, _Exploding())

        outcome = _run(git_repo)

        assert outcome.status == "failed"
        assert outcome.exit_code == 1
        assert outcome.error_message == "Agent provider error: boom"


class ExecuteAgentStepSandboxTests:
    def test_inactive_sandbox_fails_before_provider_or_patch(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] execute_agent_step: sandbox_active=False fails with the exact sandbox diagnostic and records zero provider, apply_check, and apply calls."""
        requested = _use_provider(monkeypatch, FakeAgentProvider(_patch()))
        apply_calls = _forbid_git_apply(monkeypatch)

        outcome = _run(git_repo, sandbox_active=False)

        assert outcome.status == "failed"
        assert outcome.exit_code == 1
        assert outcome.error_message == SANDBOX_REQUIRED_MESSAGE
        assert outcome.stderr == SANDBOX_REQUIRED_MESSAGE
        assert (
            outcome.stdout == '{"status":"provider_error","summary":null,"unfixable_reason":null,"touched_files":[]}\n'
        )
        assert requested == []
        assert apply_calls == []
        assert not (git_repo / "a.txt").exists()


class ExecuteAgentStepDiffReturningTests:
    def test_clean_patch_applies_unstaged_and_completes(self, git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """[tier-1/unit] execute_agent_step: an 'ollama'-token fake returning a clean new-file patch completes with exit 0, writes the file unstaged, and lists it in touched_files."""
        _use_provider(monkeypatch, FakeAgentProvider(_patch("a.txt")))

        outcome = _run(git_repo)

        assert outcome.status == "completed"
        assert outcome.exit_code == 0
        assert (git_repo / "a.txt").read_text(encoding="utf-8") == "hello\n"
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"], cwd=git_repo, check=True, capture_output=True, text=True
        ).stdout
        assert staged == ""
        assert _summary(outcome)["touched_files"] == ["a.txt"]

    @pytest.mark.parametrize(
        ("diff_kind", "message_fragment"),
        [
            pytest.param("empty", "Agent returned a proposed patch without a diff.", id="empty-diff"),
            pytest.param("unsafe_path", "escapes the sandbox", id="unsafe-path"),
            pytest.param("not_applicable", "Patch does not apply cleanly:", id="not-applicable"),
        ],
    )
    def test_unusable_patch_fails_as_provider_error_without_changing_sandbox(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch, diff_kind: str, message_fragment: str
    ) -> None:
        """[tier-1/unit] execute_agent_step: an empty, '../x'-targeting, or context-mismatched PROPOSED_PATCH fails with exit 1, provider_error, no touched files, and an unchanged working tree."""
        diffs = {
            "empty": "",
            "unsafe_path": new_file_diff("../x"),
            "not_applicable": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1 @@\n"
                "-this line is not in the file\n"
                "+replacement\n"
            ),
        }
        _use_provider(
            monkeypatch,
            FakeAgentProvider(AgentResponse(status=AgentResponseStatus.PROPOSED_PATCH, unified_diff=diffs[diff_kind])),
        )
        before = _porcelain(git_repo)

        outcome = _run(git_repo)

        assert outcome.status == "failed"
        assert outcome.exit_code == 1
        assert message_fragment in (outcome.error_message or "")
        assert _summary(outcome)["status"] == "provider_error"
        assert _summary(outcome)["touched_files"] == []
        assert _porcelain(git_repo) == before

    def test_apply_failure_after_successful_check_fails_as_provider_error(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] execute_agent_step: GitRunner.apply returning (1, '', 'boom') after a passing apply_check fails with 'Patch application failed: boom'."""
        _use_provider(monkeypatch, FakeAgentProvider(_patch()))
        monkeypatch.setattr(GitRunner, "apply", staticmethod(lambda path, diff_text: (1, "", "boom")))

        outcome = _run(git_repo)

        assert outcome.status == "failed"
        assert outcome.error_message == "Patch application failed: boom"
        assert _summary(outcome)["status"] == "provider_error"

    def test_git_error_during_apply_fails_as_provider_error(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] execute_agent_step: GitRunner.apply_check raising GitPlumbingTimeoutError fails with a 'Patch application failed:' diagnostic."""
        _use_provider(monkeypatch, FakeAgentProvider(_patch()))

        def _timeout(path: Path, diff_text: str) -> tuple[int, str, str]:
            raise GitPlumbingTimeoutError("git apply timed out")

        monkeypatch.setattr(GitRunner, "apply_check", staticmethod(_timeout))

        outcome = _run(git_repo)

        assert outcome.status == "failed"
        assert (outcome.error_message or "").startswith("Patch application failed:")
        assert _summary(outcome)["status"] == "provider_error"

    def test_no_op_leaves_sandbox_unchanged_and_completes(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] execute_agent_step: a NO_OP response completes with exit 0, an unchanged working tree, and zero apply calls."""
        _use_provider(monkeypatch, FakeAgentProvider(_no_op()))
        apply_calls = _forbid_git_apply(monkeypatch)
        before = _porcelain(git_repo)

        outcome = _run(git_repo)

        assert outcome.status == "completed"
        assert outcome.exit_code == 0
        assert _porcelain(git_repo) == before
        assert apply_calls == []


class _EditingAdapter(CliDirectMutationAdapter):
    """Real direct-mutation adapter that writes the given files instead of invoking a tool."""

    def __init__(self, files: dict[str, str]) -> None:
        self._files = files

    def _default_run(self, request: CliMutationRunRequest) -> CliMutationOutcome:
        for rel, content in self._files.items():
            (request.sandbox_path / rel).write_text(content, encoding="utf-8")
        return CliMutationOutcome(status="finished", result_text="done")


class ExecuteAgentStepDirectMutationTests:
    def test_accepted_mutation_records_touched_paths_without_reapplying(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] execute_agent_step: a 'cursor'-token fake that writes b.txt and a.txt then returns PROPOSED_PATCH with their diff completes with sorted touched_files and never calls apply or apply_check."""

        def _write_files(request: AgentRequest) -> None:
            (request.sandbox_path / "b.txt").write_text("hello\n", encoding="utf-8")
            (request.sandbox_path / "a.txt").write_text("hello\n", encoding="utf-8")

        diff = new_file_diff("b.txt") + new_file_diff("a.txt")
        provider = FakeAgentProvider(
            AgentResponse(status=AgentResponseStatus.PROPOSED_PATCH, unified_diff=diff), on_call=_write_files
        )
        requested = _use_provider(monkeypatch, provider)
        apply_calls = _forbid_git_apply(monkeypatch)

        outcome = _run(git_repo, agent=_settings("cursor"))

        assert requested == ["cursor"]
        assert outcome.status == "completed"
        assert outcome.exit_code == 0
        assert _summary(outcome)["touched_files"] == ["a.txt", "b.txt"]
        assert apply_calls == []

    def test_mutation_over_shared_gate_limit_fails_and_rolls_back(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] execute_agent_step: a real CliDirectMutationAdapter subclass writing 31 files (default max_files 30) fails with exit 1, provider_error, and a clean working tree after the shared discard."""
        files = {f"f{index}.txt": "x\n" for index in range(31)}
        _use_provider(monkeypatch, _EditingAdapter(files))

        outcome = _run(git_repo, agent=_settings("cursor"))

        assert outcome.status == "failed"
        assert outcome.exit_code == 1
        assert _summary(outcome)["status"] == "provider_error"
        assert _porcelain(git_repo) == ""

    @pytest.mark.parametrize(
        "status",
        [
            pytest.param(AgentResponseStatus.TIMEOUT, id="timeout"),
            pytest.param(AgentResponseStatus.UNFIXABLE, id="unfixable"),
            pytest.param(AgentResponseStatus.PROVIDER_ERROR, id="provider-error"),
        ],
    )
    def test_failed_statuses_with_partial_edits_remain_failures(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch, status: AgentResponseStatus
    ) -> None:
        """[tier-1/unit] execute_agent_step: a 'cursor'-token fake leaving an edit then returning TIMEOUT/UNFIXABLE/PROVIDER_ERROR fails with exit 1, the matching status, no touched files, and the edit still on disk."""

        def _leave_edit(request: AgentRequest) -> None:
            (request.sandbox_path / "partial.txt").write_text("partial\n", encoding="utf-8")

        _use_provider(monkeypatch, FakeAgentProvider(AgentResponse(status=status), on_call=_leave_edit))

        outcome = _run(git_repo, agent=_settings("cursor"))

        assert outcome.status == "failed"
        assert outcome.exit_code == 1
        assert _summary(outcome)["status"] == status.value
        assert _summary(outcome)["touched_files"] == []
        assert (git_repo / "partial.txt").read_text(encoding="utf-8") == "partial\n"


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
        ("status", "unfixable_reason", "errors", "expected_error"),
        [
            pytest.param(
                AgentResponseStatus.UNFIXABLE,
                "needs a human",
                [],
                "Agent reported the task unfixable: needs a human",
                id="unfixable",
            ),
            pytest.param(
                AgentResponseStatus.TIMEOUT,
                None,
                ["Agent timed out after 45s (provider=ollama)."],
                "Agent timed out after 45s (provider=ollama).",
                id="timeout",
            ),
            pytest.param(
                AgentResponseStatus.PROVIDER_ERROR,
                None,
                ["Agent provider error (AGENT_PROVIDER_ERROR): down"],
                "Agent provider error (AGENT_PROVIDER_ERROR): down",
                id="provider-error",
            ),
        ],
    )
    def test_failure_statuses_fail_with_exit_one_and_summary(
        self,
        git_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        status: AgentResponseStatus,
        unfixable_reason: str | None,
        errors: list[str],
        expected_error: str,
    ) -> None:
        """[tier-1/unit] execute_agent_step: UNFIXABLE/TIMEOUT/PROVIDER_ERROR fail with exit 1, stderr == error_message == the diagnostic, and the matching stdout status."""
        response = AgentResponse(status=status, unfixable_reason=unfixable_reason, errors=errors)
        _use_provider(monkeypatch, FakeAgentProvider(response))

        outcome = _run(git_repo)

        assert outcome.status == "failed"
        assert outcome.exit_code == 1
        assert outcome.stderr == expected_error
        assert outcome.error_message == expected_error
        assert _summary(outcome)["status"] == status.value
        assert _summary(outcome)["unfixable_reason"] == unfixable_reason

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
        assert outcome.exit_code == 1
        assert outcome.error_message == "Agent output callback error: ui down"
        assert _summary(outcome)["status"] == "provider_error"
        assert _summary(outcome)["touched_files"] == ["a.txt"]
