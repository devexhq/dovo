"""Contract tests for the direct-mode agent attempt pipeline."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.core.agents import (
    AgentAttempt,
    AgentAttemptContext,
    AgentInvocationContext,
    AgentRequest,
    AgentResponse,
    AgentResponseStatus,
    BaseAgentProvider,
    CliDirectMutationAdapter,
    CliMutationOutcome,
    CliMutationRunRequest,
    ResolvedAgentSettings,
    run_direct_attempt,
)
from dovo.core.git import GitRunner
from tests.harness import AGENT_ADAPTER_FACTORY, FakeAgentProvider, new_file_diff

_NO_OP_SUMMARY = "Inspected the repository; no edits were required."


def _settings(provider: str = "copilot") -> ResolvedAgentSettings:
    return ResolvedAgentSettings(provider=provider, model="m", endpoint="http://e", temperature=0.7, max_tokens=512)


def _use_provider(monkeypatch: pytest.MonkeyPatch, provider: BaseAgentProvider) -> list[str]:
    requested: list[str] = []

    def _factory(token: str) -> BaseAgentProvider:
        requested.append(token)
        return provider

    monkeypatch.setattr(AGENT_ADAPTER_FACTORY, _factory)
    return requested


def _attempt(worktree: Path, provider: str = "copilot") -> AgentAttempt:
    return run_direct_attempt(
        instruction="Plan the change", settings=_settings(provider), worktree_path=worktree, timeout_seconds=45
    )


def _porcelain(repo: Path) -> str:
    return subprocess.run(["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True).stdout


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


class RunDirectAttemptRequestTests:
    def test_request_carries_instruction_settings_worktree_and_timeout(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] run_direct_attempt: copilot settings and timeout_seconds 45 ask the factory for 'copilot' once and invoke receives the exact direct AgentRequest."""
        provider = FakeAgentProvider(_no_op())
        requested = _use_provider(monkeypatch, provider)

        _attempt(git_repo)

        assert requested == ["copilot"]
        assert provider.requests == [
            AgentRequest(
                mode="direct",
                instruction="Plan the change",
                payload=None,
                worktree_path=git_repo,
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

    def test_unregistered_provider_returns_provider_error_attempt(self, git_repo: Path) -> None:
        """[tier-1/unit] run_direct_attempt: provider 'unregistered' returns PROVIDER_ERROR with a diagnostic containing "Unsupported agent provider 'unregistered' (AGENT_PROVIDER_UNSUPPORTED)"."""
        attempt = _attempt(git_repo, "unregistered")

        assert attempt.status == AgentResponseStatus.PROVIDER_ERROR
        assert "Unsupported agent provider 'unregistered' (AGENT_PROVIDER_UNSUPPORTED)" in attempt.diagnostics[0]

    def test_provider_exception_returns_provider_error_attempt(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] run_direct_attempt: invoke raising RuntimeError('boom') returns PROVIDER_ERROR with diagnostics == ['Agent provider error: boom']."""

        class _Exploding(BaseAgentProvider):
            def _invoke(self, request: AgentRequest) -> AgentResponse:
                raise RuntimeError("boom")

        _use_provider(monkeypatch, _Exploding())

        attempt = _attempt(git_repo)

        assert attempt.status == AgentResponseStatus.PROVIDER_ERROR
        assert attempt.diagnostics == ["Agent provider error: boom"]


class RunDirectAttemptSettleTests:
    @pytest.mark.parametrize(
        ("summary", "raw_text", "expected_summary"),
        [
            pytest.param(_NO_OP_SUMMARY, None, _NO_OP_SUMMARY, id="summary"),
            pytest.param(None, "plan:\n1. do x", "plan:\n1. do x", id="raw-text-fallback"),
        ],
    )
    def test_no_op_completes_without_touching_worktree(
        self,
        git_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        summary: str | None,
        raw_text: str | None,
        expected_summary: str,
    ) -> None:
        """[tier-1/unit] run_direct_attempt: a NO_OP response returns NO_OP with summary == summary or the raw_text fallback, an unchanged working tree, and zero apply_check/apply calls."""
        _use_provider(monkeypatch, FakeAgentProvider(_no_op(summary=summary, raw_text=raw_text)))
        apply_calls = _forbid_git_apply(monkeypatch)
        before = _porcelain(git_repo)

        attempt = _attempt(git_repo)

        assert attempt.status == AgentResponseStatus.NO_OP
        assert attempt.summary == expected_summary
        assert _porcelain(git_repo) == before
        assert apply_calls == []

    def test_response_fixes_render_as_trailing_fix_diagnostic(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] run_direct_attempt: a TIMEOUT response with errors ["e"] and fixes ["f1", "f2"] returns TIMEOUT with diagnostics == ["e", "Fix:\n- f1\n- f2"]."""
        response = AgentResponse(status=AgentResponseStatus.TIMEOUT, errors=["e"], fixes=["f1", "f2"])
        _use_provider(monkeypatch, FakeAgentProvider(response))

        attempt = _attempt(git_repo)

        assert attempt.status == AgentResponseStatus.TIMEOUT
        assert attempt.diagnostics == ["e", "Fix:\n- f1\n- f2"]


class _EditingAdapter(CliDirectMutationAdapter):
    """Real direct-mutation adapter that writes the given files instead of invoking a tool."""

    def __init__(self, files: dict[str, str]) -> None:
        self._files = files

    def _default_run(self, request: CliMutationRunRequest) -> CliMutationOutcome:
        for rel, content in self._files.items():
            (request.worktree_path / rel).write_text(content, encoding="utf-8")
        return CliMutationOutcome(status="finished", result_text="done")


class RunDirectAttemptDirectMutationTests:
    def test_accepted_mutation_records_sorted_touched_paths_without_reapplying(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] run_direct_attempt: a 'copilot'-token fake that writes b.txt and a.txt then returns PROPOSED_PATCH returns touched_files == ['a.txt', 'b.txt'] and never calls apply or apply_check."""

        def _write_files(request: AgentRequest) -> None:
            (request.worktree_path / "b.txt").write_text("hello\n", encoding="utf-8")
            (request.worktree_path / "a.txt").write_text("hello\n", encoding="utf-8")

        diff = new_file_diff("b.txt") + new_file_diff("a.txt")
        provider = FakeAgentProvider(
            AgentResponse(status=AgentResponseStatus.PROPOSED_PATCH, unified_diff=diff), on_call=_write_files
        )
        requested = _use_provider(monkeypatch, provider)
        apply_calls = _forbid_git_apply(monkeypatch)

        attempt = _attempt(git_repo, "copilot")

        assert requested == ["copilot"]
        assert attempt.status == AgentResponseStatus.PROPOSED_PATCH
        assert attempt.touched_files == ["a.txt", "b.txt"]
        assert apply_calls == []

    def test_mutation_over_shared_gate_limit_returns_provider_error_and_rolls_back(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] run_direct_attempt: a real CliDirectMutationAdapter subclass writing 31 files (default max_files 30) returns PROVIDER_ERROR and leaves git status --porcelain empty."""
        files = {f"f{index}.txt": "x\n" for index in range(31)}
        _use_provider(monkeypatch, _EditingAdapter(files))

        attempt = _attempt(git_repo, "copilot")

        assert attempt.status == AgentResponseStatus.PROVIDER_ERROR
        assert _porcelain(git_repo) == ""

    @pytest.mark.parametrize(
        "status",
        [
            pytest.param(AgentResponseStatus.TIMEOUT, id="timeout"),
            pytest.param(AgentResponseStatus.UNFIXABLE, id="unfixable"),
            pytest.param(AgentResponseStatus.PROVIDER_ERROR, id="provider-error"),
        ],
    )
    def test_failed_statuses_with_partial_edits_map_straight_through(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch, status: AgentResponseStatus
    ) -> None:
        """[tier-1/unit] run_direct_attempt: a 'copilot'-token fake leaving an edit then returning TIMEOUT/UNFIXABLE/PROVIDER_ERROR returns that status with touched_files == [] and the edit still on disk."""

        def _leave_edit(request: AgentRequest) -> None:
            (request.worktree_path / "partial.txt").write_text("partial\n", encoding="utf-8")

        _use_provider(monkeypatch, FakeAgentProvider(AgentResponse(status=status), on_call=_leave_edit))

        attempt = _attempt(git_repo, "copilot")

        assert attempt.status == status
        assert attempt.touched_files == []
        assert (git_repo / "partial.txt").read_text(encoding="utf-8") == "partial\n"


class RunDirectAttemptInvocationTests:
    @pytest.mark.parametrize(
        "with_invocation", [pytest.param(True, id="with-invocation"), pytest.param(False, id="none")]
    )
    def test_request_carries_invocation_and_scratch_path_only_when_supplied(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_invocation: bool
    ) -> None:
        """[tier-1/unit] run_direct_attempt: the adapter receives AgentRequest.invocation == the passed context and agent_scratch_path == context.scratch_path; invocation None yields both fields None."""
        provider = FakeAgentProvider(AgentResponse(status=AgentResponseStatus.NO_OP))
        _use_provider(monkeypatch, provider)
        invocation = AgentInvocationContext(
            invocation_id="a" * 32, scratch_path=tmp_path / "scratch", control_path=tmp_path / "control"
        )

        run_direct_attempt(
            instruction="Plan the change",
            settings=_settings(),
            worktree_path=git_repo,
            timeout_seconds=45,
            context=AgentAttemptContext(invocation=invocation if with_invocation else None),
        )

        request = provider.requests[0]
        assert request.invocation == (invocation if with_invocation else None)
        assert request.agent_scratch_path == (invocation.scratch_path if with_invocation else None)


class RunDirectAttemptEnvTests:
    def test_settings_env_fields_and_step_env_reach_the_agent_request(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] run_direct_attempt: the AgentRequest given to the adapter has env_passthrough, env_mode from settings and env, metadata_env from the arguments."""
        provider = FakeAgentProvider(_no_op())
        _use_provider(monkeypatch, provider)
        settings = _settings().model_copy(update={"env_passthrough": ["DOCKER_*"], "env_mode": "inherit"})

        run_direct_attempt(
            instruction="Plan the change",
            settings=settings,
            worktree_path=git_repo,
            timeout_seconds=45,
            context=AgentAttemptContext(env={"MY_VAR": "x"}, metadata_env={"DOVO_STEP_ID": "s"}),
        )

        request = provider.requests[0]
        assert (request.env_passthrough, request.env_mode) == (["DOCKER_*"], "inherit")
        assert (request.env, request.metadata_env) == ({"MY_VAR": "x"}, {"DOVO_STEP_ID": "s"})

    @pytest.mark.parametrize(
        "status",
        [
            pytest.param(AgentResponseStatus.NO_OP, id="settled"),
            pytest.param(AgentResponseStatus.TIMEOUT, id="timeout"),
        ],
    )
    def test_response_env_withheld_is_copied_onto_the_attempt(
        self, status: AgentResponseStatus, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] run_direct_attempt: a NO_OP and a TIMEOUT response with env_withheld ['A'] both return an AgentAttempt with env_withheld == ['A']."""
        _use_provider(monkeypatch, FakeAgentProvider(AgentResponse(status=status, env_withheld=["A"])))

        assert _attempt(git_repo).env_withheld == ["A"]

    def test_proposed_patch_response_env_withheld_is_copied_onto_the_attempt(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] run_direct_attempt: a PROPOSED_PATCH response with env_withheld ['A'] returns an AgentAttempt with env_withheld == ['A']."""
        response = AgentResponse(
            status=AgentResponseStatus.PROPOSED_PATCH, unified_diff=new_file_diff("a.txt", "x\n"), env_withheld=["A"]
        )
        _use_provider(monkeypatch, FakeAgentProvider(response))

        assert _attempt(git_repo).env_withheld == ["A"]
