"""Contract tests for the direct-mode agent attempt pipeline."""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from dovo.common.mcp_servers import McpServerDefinition
from dovo.common.models import Remediation
from dovo.common.tool_policy import ToolCapability, ToolPolicy, ToolRule
from dovo.core.agents import (
    AgentAttempt,
    AgentAttemptContext,
    AgentInvocationContext,
    AgentProgress,
    AgentRequest,
    AgentResponse,
    AgentResponseStatus,
    BaseAgentProvider,
    CliDirectMutationAdapter,
    CliMutationOutcome,
    CliMutationRunRequest,
    ResolvedAgentSettings,
    default_tool_policy,
    run_direct_attempt,
)
from dovo.core.agents.registry import PROVIDERS
from tests.harness import AGENT_ADAPTER_FACTORY, FakeAgentProvider, new_file_diff
from tests.harness.diagnostic_log import diagnostic_record, read_single_event

_NO_OP_SUMMARY = "Inspected the repository; no edits were required."


def _settings(provider: str = "copilot") -> ResolvedAgentSettings:
    return ResolvedAgentSettings(
        provider=provider,
        model="m",
        endpoint="http://e",
        temperature=0.7,
        max_tokens=512,
        tools=default_tool_policy(),
    )


def _use_provider(monkeypatch: pytest.MonkeyPatch, provider: BaseAgentProvider) -> list[str]:
    requested: list[str] = []

    def _factory(token: str) -> BaseAgentProvider:
        requested.append(token)
        return provider

    monkeypatch.setattr(AGENT_ADAPTER_FACTORY, _factory)
    return requested


def _attempt(
    worktree: Path,
    provider: str = "copilot",
    tools: ToolPolicy | None = None,
    max_files: int | None = None,
    context: AgentAttemptContext | None = None,
) -> AgentAttempt:
    return run_direct_attempt(
        instruction="Plan the change",
        settings=_settings(provider),
        worktree_path=worktree,
        timeout_seconds=45,
        tools=tools or default_tool_policy(),
        max_files=max_files,
        context=context,
    )


def _porcelain(repo: Path) -> str:
    return subprocess.run(["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True).stdout


def _no_op(summary: str | None = _NO_OP_SUMMARY, raw_text: str | None = None) -> AgentResponse:
    return AgentResponse(status=AgentResponseStatus.NO_OP, summary=summary, raw_text=raw_text)


class RunDirectAttemptRequestTests:
    def test_request_carries_instruction_settings_worktree_and_timeout(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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
                tools=default_tool_policy(),
            )
        ]

    def test_request_carries_configured_mcp_servers_to_provider(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = FakeAgentProvider(_no_op())
        _use_provider(monkeypatch, provider)
        servers = {"github": McpServerDefinition(command="npx")}

        run_direct_attempt(
            instruction="Plan the change",
            settings=_settings().model_copy(update={"mcp_servers": servers}),
            worktree_path=git_repo,
            timeout_seconds=45,
            tools=default_tool_policy(),
        )

        assert [request.mcp_servers for request in provider.requests] == [servers]

    def test_request_carries_step_max_files_to_provider(self, git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = FakeAgentProvider(_no_op())
        _use_provider(monkeypatch, provider)

        run_direct_attempt(
            instruction="Plan the change",
            settings=_settings(),
            worktree_path=git_repo,
            timeout_seconds=45,
            tools=default_tool_policy(),
            max_files=100,
        )

        assert [request.max_files for request in provider.requests] == [100]

    def test_unregistered_provider_returns_provider_error_attempt(
        self, git_repo: Path, frozen_diagnostic_clock: Path
    ) -> None:
        """Return provider_error naming the unsupported provider with no diagnostics and no record."""
        attempt = _attempt(git_repo, "unregistered")

        assert attempt.status == AgentResponseStatus.PROVIDER_ERROR
        assert "Unsupported agent provider 'unregistered'." in attempt.errors[0]
        assert attempt.diagnostics == []
        assert not frozen_diagnostic_clock.parent.parent.exists()

    def test_provider_exception_returns_provider_error_attempt(
        self, git_repo: Path, frozen_diagnostic_clock: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return provider_error with errors == ["Agent provider error: boom"] and diagnostics == [the agents.run record]."""

        class _Exploding(BaseAgentProvider):
            def _invoke(self, request: AgentRequest) -> AgentResponse:
                raise RuntimeError("boom")

        _use_provider(monkeypatch, _Exploding())

        attempt = _attempt(git_repo)

        assert attempt.status == AgentResponseStatus.PROVIDER_ERROR
        assert attempt.errors == ["Agent provider error: boom"]
        assert attempt.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert (event["level"], event["operation"], event["context"]) == (
            "error",
            "agents.run",
            {"provider": "copilot", "path": str(git_repo)},
        )

    @pytest.mark.parametrize("error_type", [RuntimeError, ValueError])
    def test_adapter_build_exception_records_error_into_attempt_records(
        self,
        git_repo: Path,
        frozen_diagnostic_clock: Path,
        monkeypatch: pytest.MonkeyPatch,
        error_type: type[Exception],
    ) -> None:
        """Return provider_error with errors == ["Agent provider error: build failed"] and diagnostics == [the agents.run record] when the spec's build raises."""

        def _build_failed() -> BaseAgentProvider:
            raise error_type("build failed")

        monkeypatch.setitem(PROVIDERS, "copilot", replace(PROVIDERS["copilot"], build=_build_failed))

        attempt = _attempt(git_repo)

        assert attempt.status == AgentResponseStatus.PROVIDER_ERROR
        assert attempt.errors == ["Agent provider error: build failed"]
        assert attempt.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert (event["level"], event["operation"]) == ("error", "agents.run")


class RunDirectToolsTests:
    def test_tools_argument_lands_on_agent_request(self, git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = FakeAgentProvider(_no_op())
        _use_provider(monkeypatch, provider)
        policy = ToolPolicy(allow=[ToolRule(capability=ToolCapability.SHELL, pattern="git status")])

        _attempt(git_repo, tools=policy)

        assert provider.requests[0].tools == policy


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
        _use_provider(monkeypatch, FakeAgentProvider(_no_op(summary=summary, raw_text=raw_text)))
        before = _porcelain(git_repo)

        attempt = _attempt(git_repo)

        assert attempt.status == AgentResponseStatus.NO_OP
        assert attempt.summary == expected_summary
        assert _porcelain(git_repo) == before

    def test_response_remediations_render_as_trailing_fix_diagnostic(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        response = AgentResponse(
            status=AgentResponseStatus.TIMEOUT,
            errors=["e"],
            remediations=[Remediation(message="Do one"), Remediation(message="Run 'two'", command="two")],
        )
        _use_provider(monkeypatch, FakeAgentProvider(response))

        attempt = _attempt(git_repo)

        assert attempt.status == AgentResponseStatus.TIMEOUT
        assert attempt.errors == ["e", "Fix:\n- Do one\n- Run 'two'"]

    def test_settle_copies_response_records_onto_attempt(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch, frozen_diagnostic_clock: Path
    ) -> None:
        """Copy a PROVIDER_ERROR response's diagnostics [record] onto the attempt unchanged, with its errors in the attempt's errors."""
        record = diagnostic_record(frozen_diagnostic_clock)
        response = AgentResponse(
            status=AgentResponseStatus.PROVIDER_ERROR,
            errors=["Unexpected error during agents.invoke: boom"],
            diagnostics=[record],
        )
        _use_provider(monkeypatch, FakeAgentProvider(response))

        attempt = _attempt(git_repo)

        assert attempt.errors == ["Unexpected error during agents.invoke: boom"]
        assert attempt.diagnostics == [record]


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

        def _write_files(request: AgentRequest) -> None:
            (request.worktree_path / "b.txt").write_text("hello\n", encoding="utf-8")
            (request.worktree_path / "a.txt").write_text("hello\n", encoding="utf-8")

        diff = new_file_diff("b.txt") + new_file_diff("a.txt")
        provider = FakeAgentProvider(
            AgentResponse(status=AgentResponseStatus.PROPOSED_PATCH, unified_diff=diff), on_call=_write_files
        )
        requested = _use_provider(monkeypatch, provider)

        attempt = _attempt(git_repo, "copilot")

        assert requested == ["copilot"]
        assert attempt.status == AgentResponseStatus.PROPOSED_PATCH
        assert attempt.touched_files == ["a.txt", "b.txt"]

    def test_mutation_over_step_max_files_returns_provider_error_and_rolls_back(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _use_provider(monkeypatch, _EditingAdapter({"a.txt": "x\n", "b.txt": "x\n"}))

        context = AgentAttemptContext(rejected_patch_destination=tmp_path / "rejected" / "code_attempt_1.patch")

        attempt = _attempt(git_repo, "copilot", max_files=1, context=context)

        assert attempt.status == AgentResponseStatus.PROVIDER_ERROR
        assert _porcelain(git_repo) == ""

    def test_mutation_without_step_max_files_is_not_limited(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        files = {f"f{index}.txt": "x\n" for index in range(31)}
        _use_provider(monkeypatch, _EditingAdapter(files))

        attempt = _attempt(git_repo, "copilot")

        assert attempt.status == AgentResponseStatus.PROPOSED_PATCH
        assert len(attempt.touched_files) == 31

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
            tools=default_tool_policy(),
            context=AgentAttemptContext(invocation=invocation if with_invocation else None),
        )

        request = provider.requests[0]
        assert request.invocation == (invocation if with_invocation else None)
        assert request.agent_scratch_path == (invocation.scratch_path if with_invocation else None)


class RunDirectAttemptCallbackTests:
    @pytest.mark.parametrize("with_callback", [pytest.param(True, id="with-callback"), pytest.param(False, id="none")])
    def test_request_carries_the_context_callback_only_when_supplied(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch, with_callback: bool
    ) -> None:
        provider = FakeAgentProvider(_no_op())
        _use_provider(monkeypatch, provider)

        def on_line(stream_name: str, line: str) -> None:
            """Line callback used only as a distinguishable callable value."""

        _attempt(git_repo, context=AgentAttemptContext(on_line=on_line if with_callback else None))

        assert provider.requests[0].on_line is (on_line if with_callback else None)

    @pytest.mark.parametrize("with_callback", [pytest.param(True, id="with-callback"), pytest.param(False, id="none")])
    def test_request_carries_the_progress_callback_only_when_supplied(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch, with_callback: bool
    ) -> None:
        provider = FakeAgentProvider(_no_op())
        _use_provider(monkeypatch, provider)

        def on_progress(progress: AgentProgress) -> None:
            """Progress callback used only as a distinguishable callable value."""

        _attempt(git_repo, context=AgentAttemptContext(on_progress=on_progress if with_callback else None))

        assert provider.requests[0].on_progress is (on_progress if with_callback else None)


class RunDirectAttemptEnvTests:
    def test_settings_env_fields_and_step_env_reach_the_agent_request(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = FakeAgentProvider(_no_op())
        _use_provider(monkeypatch, provider)
        settings = _settings().model_copy(update={"env_passthrough": ["DOCKER_*"], "env_mode": "inherit"})

        run_direct_attempt(
            instruction="Plan the change",
            settings=settings,
            worktree_path=git_repo,
            timeout_seconds=45,
            tools=default_tool_policy(),
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
        _use_provider(monkeypatch, FakeAgentProvider(AgentResponse(status=status, env_withheld=["A"])))

        assert _attempt(git_repo).env_withheld == ["A"]

    def test_proposed_patch_response_env_withheld_is_copied_onto_the_attempt(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        response = AgentResponse(
            status=AgentResponseStatus.PROPOSED_PATCH, unified_diff=new_file_diff("a.txt", "x\n"), env_withheld=["A"]
        )
        _use_provider(monkeypatch, FakeAgentProvider(response))

        assert _attempt(git_repo).env_withheld == ["A"]


class RunDirectRejectedPatchTests:
    def test_provider_error_response_path_is_carried_onto_the_attempt(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _use_provider(
            monkeypatch,
            FakeAgentProvider(
                AgentResponse(
                    status=AgentResponseStatus.PROVIDER_ERROR,
                    errors=["gate said no"],
                    rejected_patch_path="/s/rejected/code_attempt_1.patch",
                )
            ),
        )

        attempt = _attempt(git_repo)

        assert attempt.status == AgentResponseStatus.PROVIDER_ERROR
        assert attempt.rejected_patch_path == "/s/rejected/code_attempt_1.patch"

    def test_context_destination_reaches_the_agent_request(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = FakeAgentProvider(_no_op())
        _use_provider(monkeypatch, provider)
        destination = Path("/s/rejected/code_attempt_1.patch")

        _attempt(git_repo, context=AgentAttemptContext(rejected_patch_destination=destination))

        assert [request.rejected_patch_destination for request in provider.requests] == [destination]
