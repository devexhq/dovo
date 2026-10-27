"""Tests for the GitHub Copilot CLI direct-mutation agent adapter."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Literal

import pytest

from dovo.common.tool_policy import ToolCapability, ToolPolicy, ToolRule
from dovo.core.agents import (
    AgentInvocationContext,
    AgentResponseStatus,
    PolicyRoots,
    default_tool_policy,
    tool_policy_unsupported_message,
)
from dovo.core.agents.cli_mutation import CliMutationOutcome, CliMutationRunRequest
from dovo.core.agents.copilot import (
    COPILOT_PROVIDER_SPEC,
    CopilotAgentAdapter,
    CopilotPolicyArgs,
    UserMcpConfig,
    default_copilot_run,
    load_user_mcp_config,
    render_copilot_policy,
    resolve_copilot_token,
)
from dovo.core.agents.copilot_progress import map_copilot_line
from dovo.core.agents.models import AgentDenial, AgentProgress, AgentProgressKind
from dovo.core.agents.registry import PROVIDERS
from tests.harness import (
    PROVIDER_FINISHED_STREAMS,
    AgentRequestBuilder,
    FakeAgentRunner,
    StreamingAgentRunner,
    real_invocation,
)

_ALLOW_ALL = ToolPolicy(allow_all=True)


@pytest.fixture(autouse=True)
def _token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "test-token")


class CopilotAuthTests:
    @pytest.mark.parametrize(
        ("env", "expected"),
        [
            pytest.param({"GH_TOKEN": "abc"}, "abc", id="gh-token"),
            pytest.param({"GITHUB_TOKEN": "xyz"}, "xyz", id="github-token-fallback"),
            pytest.param({"GH_TOKEN": "  ", "GITHUB_TOKEN": "xyz"}, "xyz", id="blank-gh-token-falls-through"),
            pytest.param({"GH_TOKEN": "abc", "GITHUB_TOKEN": "xyz"}, "abc", id="gh-token-precedes-github-token"),
            pytest.param({}, None, id="unset"),
        ],
    )
    def test_resolve_checks_gh_token_before_github_token(
        self, monkeypatch: pytest.MonkeyPatch, env: dict[str, str], expected: str | None
    ) -> None:
        for name in ("GH_TOKEN", "GITHUB_TOKEN"):
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)

        assert resolve_copilot_token() == expected

    def test_invoke_without_a_token_returns_provider_error_naming_both_variables(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        adapter = CopilotAgentAdapter()

        resp = adapter.invoke(AgentRequestBuilder().with_worktree_path(tmp_path).build())

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert resp.errors == [
            "Agent provider error: missing GH_TOKEN or GITHUB_TOKEN. "
            "Fix: export GH_TOKEN=... or export GITHUB_TOKEN=..."
        ]


class CopilotRunProgressTests:
    _VIEW_LINE = json.dumps(
        {"type": "tool.execution_start", "data": {"toolName": "view", "arguments": {"path": "{root}/x.py"}}}
    )
    _COMPLETED_STDOUT = (
        b'{"type":"assistant.message","data":{"content":"hello"}}\n{"type":"result","data":{"exitCode":0}}\n'
    )

    def test_descriptor_registers_the_copilot_progress_mapper(self) -> None:
        assert COPILOT_PROVIDER_SPEC.progress_mapper is map_copilot_line

    def _view_line(self, worktree: Path) -> str:
        return self._VIEW_LINE.replace("{root}", str(worktree))

    def _run_streaming_view_line(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> tuple[str, list[AgentProgress], list[tuple[str, str]]]:
        """Run with one streamed view line and return the line, the progress items and the raw lines received."""
        line = self._view_line(tmp_path)
        runner = StreamingAgentRunner(stdout_lines=[line], stderr_lines=[], completed_stdout=self._COMPLETED_STDOUT)
        monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)
        progress: list[AgentProgress] = []
        raw: list[tuple[str, str]] = []

        default_copilot_run(
            CliMutationRunRequest(
                worktree_path=tmp_path,
                prompt="hi",
                timeout_seconds=3,
                tools=_ALLOW_ALL,
                on_line=lambda stream_name, text: raw.append((stream_name, text)),
                on_progress=progress.append,
                progress_mapper=map_copilot_line,
            )
        )

        return line, progress, raw

    def test_default_run_maps_stdout_lines_into_on_progress(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, progress, _ = self._run_streaming_view_line(tmp_path, monkeypatch)

        assert progress == [AgentProgress(kind=AgentProgressKind.READ, target="x.py")]

    def test_default_run_passes_raw_stdout_lines_to_on_line(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        line, _, raw = self._run_streaming_view_line(tmp_path, monkeypatch)

        assert raw == [("stdout", line)]


def _run_default_copilot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[CliMutationOutcome, FakeAgentRunner]:
    """Run default_copilot_run against a process that finishes with 'hello' and return the outcome and the runner."""
    runner = FakeAgentRunner().returning(stdout=PROVIDER_FINISHED_STREAMS["copilot"]("hello"))
    monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)

    outcome = default_copilot_run(
        CliMutationRunRequest(
            worktree_path=tmp_path,
            prompt="hi",
            model=None,
            timeout_seconds=3,
            tools=_ALLOW_ALL,
            env={"GH_TOKEN": "test-token"},
        )
    )

    return outcome, runner


class CopilotRunTests:
    def test_missing_token_returns_missing_credential_error_without_running_gh(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        runner = FakeAgentRunner()
        monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)

        outcome = default_copilot_run(
            CliMutationRunRequest(worktree_path=tmp_path, prompt="hi", model=None, timeout_seconds=3, tools=_ALLOW_ALL)
        )

        assert outcome == CliMutationOutcome(
            status="error",
            error_detail="missing GH_TOKEN or GITHUB_TOKEN. Fix: export GH_TOKEN=... or export GITHUB_TOKEN=...",
        )
        assert runner.calls == []

    def test_default_run_returns_the_assistant_message_from_the_jsonl_stream(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return a finished outcome whose result_text is the assistant message of the JSONL stream."""
        outcome, _ = _run_default_copilot(tmp_path, monkeypatch)

        assert (outcome.status, outcome.result_text, outcome.error_detail) == ("finished", "hello", None)

    def test_default_run_spawns_in_the_worktree_with_the_prompt_on_stdin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Record cwd == worktree_path, input_data == b'hi', timeout_seconds == 3 and GH_TOKEN == 'test-token'."""
        _, runner = _run_default_copilot(tmp_path, monkeypatch)

        call = runner.last_call
        assert (call.cwd, call.input_data, call.timeout_seconds) == (tmp_path, b"hi", 3)
        assert call.env["GH_TOKEN"] == "test-token"

    def test_missing_gh_binary_returns_install_hint_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeAgentRunner().raising(FileNotFoundError("gh"))
        monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)

        outcome = default_copilot_run(
            CliMutationRunRequest(worktree_path=tmp_path, prompt="hi", model=None, timeout_seconds=3, tools=_ALLOW_ALL)
        )

        assert outcome.status == "error"
        assert outcome.result_text is None
        assert outcome.error_detail == (
            "gh is not installed or not on PATH: gh. Fix: install the GitHub CLI (https://cli.github.com)"
        )


class CopilotInvokeOutcomeTests:
    def test_invoke_maps_elapsed_subprocess_timeout_to_timeout_status(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeAgentRunner().raising(subprocess.TimeoutExpired(cmd="gh", timeout=10))
        monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)

        resp = CopilotAgentAdapter().invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert resp.status == AgentResponseStatus.TIMEOUT
        assert resp.errors[0].splitlines()[0] == "Agent timed out after 10s (provider=copilot)."

    @pytest.mark.parametrize(
        ("runner", "expected_error"),
        [
            pytest.param(
                FakeAgentRunner().raising(FileNotFoundError("gh")),
                "Agent provider error: gh is not installed or not on PATH: gh. "
                "Fix: install the GitHub CLI (https://cli.github.com)",
                id="gh-missing",
            ),
            pytest.param(
                FakeAgentRunner().raising(OSError("exec failed")),
                "Agent provider error: exec failed",
                id="os-error",
            ),
            pytest.param(
                FakeAgentRunner().returning(returncode=1, stderr=b"gh failed"),
                "Agent provider error: gh failed",
                id="non-zero-exit",
            ),
        ],
    )
    def test_invoke_maps_non_timeout_subprocess_failure_to_provider_error(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch, runner: FakeAgentRunner, expected_error: str
    ) -> None:
        monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)

        resp = CopilotAgentAdapter().invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert resp.errors == [expected_error]


class CopilotDescriptorTests:
    def test_descriptor_declares_the_expected_provider_fields(self) -> None:
        spec = PROVIDERS["copilot"]

        assert (spec.credential_envs, spec.requires_model, spec.binary) == (("GH_TOKEN", "GITHUB_TOKEN"), False, "gh")
        assert (spec.supports_tool_policy, spec.supports_os_sandbox) == (True, False)

    def test_descriptor_reserves_copilot_home_as_a_control_variable(self) -> None:
        assert "COPILOT_HOME" in COPILOT_PROVIDER_SPEC.control_envs


class CopilotEnvBoundaryTests:
    def test_request_model_overrides_ambient_copilot_model(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spawn with COPILOT_MODEL 'gpt-x' when the host sets 'ambient-model' and the request model is 'gpt-x'."""
        monkeypatch.setenv("COPILOT_MODEL", "ambient-model")
        runner = FakeAgentRunner().returning(stdout=PROVIDER_FINISHED_STREAMS["copilot"]("ok"))
        monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)

        CopilotAgentAdapter().invoke(AgentRequestBuilder().with_worktree_path(git_repo).with_model("gpt-x").build())

        assert runner.last_call.env["COPILOT_MODEL"] == "gpt-x"


# Trimmed from events recorded with `gh copilot -- -p ... --output-format json --silent` (copilot 1.0.92, 2026-10-09); `result` lines are hand-written.
# The tool name lives only on tool.execution_start; tool.execution_complete carries toolCallId.
_DENY_RULE_STREAM = (
    b'{"type":"tool.execution_start","data":{"toolCallId":"toolu_01Hn1pVSWvaFS56NSN83qahb","toolName":"bash",'
    b'"arguments":{"command":"echo hi","description":"Run echo hi"},"turnId":"0","model":"claude-sonnet-5.5",'
    b'"toolTitle":"Running command","shellToolInfo":{"possiblePaths":[],"hasWriteFileRedirection":false}},'
    b'"id":"2926c830-c1d5-412b-aa7d-b260aaaa3ca4","timestamp":"2026-10-09T18:43:43.392Z",'
    b'"parentId":"3195c9ad-0ee7-4b6c-9a57-1c63c9578d3c"}\n'
    b'{"type":"tool.execution_complete","data":{"toolCallId":"toolu_01Hn1pVSWvaFS56NSN83qahb",'
    b'"model":"claude-sonnet-5.5","interactionId":"ae061869-f957-4c33-862a-5d9dbebe8e6f","turnId":"0","rte":true,'
    b'"success":false,"error":{"message":"Permission to run this tool was denied due to the following rules: `shell`",'
    b'"code":"denied"},"toolTelemetry":{"properties":{"shell_error_category":"permission_denied"}}},'
    b'"id":"627afeaf-f8b5-4260-a0b2-760e1655d975","timestamp":"2026-10-09T18:43:43.498Z",'
    b'"parentId":"2926c830-c1d5-412b-aa7d-b260aaaa3ca4"}\n'
    b'{"type":"result","timestamp":"2026-10-09T18:43:45.405Z","sessionId":"6b55cb24-7fcc-4016-84e3-fe5f27faf9e0",'
    b'"exitCode":0,"usage":{"premiumRequests":1,"totalApiDurationMs":3350,"sessionDurationMs":6182,'
    b'"codeChanges":{"linesAdded":0,"linesRemoved":0,"filesModified":[]}}}\n'
)
_TOOL_SUCCESS_STREAM = (
    b'{"type":"tool.execution_start","data":{"toolCallId":"toolu_01V1HBoYdXQCFJHrweaTYCZM","toolName":"bash",'
    b'"arguments":{"command":"echo hi","description":"Run echo hi"},"turnId":"0","model":"claude-sonnet-5.5",'
    b'"toolTitle":"Running command","shellToolInfo":{"possiblePaths":[],"hasWriteFileRedirection":false}},'
    b'"id":"8592b5cd-0b03-48b2-9d15-65dd9ac92d0a","timestamp":"2026-10-09T18:44:00.581Z",'
    b'"parentId":"1c974296-bdf2-47de-b70e-d8162b546b42"}\n'
    b'{"type":"tool.execution_complete","data":{"toolCallId":"toolu_01V1HBoYdXQCFJHrweaTYCZM",'
    b'"model":"claude-sonnet-5.5","interactionId":"b5373959-d265-4e77-b99b-f82f60e1a10e","turnId":"0","rte":true,'
    b'"shellExecution":{"exitCode":0},"success":true,"result":{"content":"hi\\n<shellId: 0 completed with exit code 0>",'
    b'"detailedContent":"hi\\n<shellId: 0 completed with exit code 0>"}},'
    b'"id":"647512b5-15e1-46d8-bdc4-0a2753404f30","timestamp":"2026-10-09T18:44:00.667Z",'
    b'"parentId":"8592b5cd-0b03-48b2-9d15-65dd9ac92d0a"}\n'
    b'{"type":"result","timestamp":"2026-10-09T18:44:01.471Z","sessionId":"49f9af7b-0cf2-4ae5-91ef-cc07442009c4",'
    b'"exitCode":0,"usage":{"premiumRequests":1}}\n'
)


_NO_INTERACTIVE_MESSAGE = "Permission denied because no interactive user response was available. Retry in an interactive session so the user can approve it, or try an alternative that does not require this permission."
_NO_INTERACTIVE_STREAM = (
    b'{"type":"tool.execution_start","data":{"toolCallId":"toolu_01KBgTcL9noQsoEREAJuivys","toolName":"bash",'
    b'"arguments":{"command":"touch x.txt && ls -l x.txt","description":"Create x.txt"},"turnId":"0",'
    b'"model":"claude-sonnet-5.5","toolTitle":"Running command",'
    b'"shellToolInfo":{"possiblePaths":["x.txt"],"hasWriteFileRedirection":false}},'
    b'"id":"5916ac8a-b69a-4d54-9789-6a8a56a603a0","timestamp":"2026-10-09T18:47:23.845Z",'
    b'"parentId":"756447ef-5088-4659-85cd-c02cffa9e95d"}\n'
    b'{"type":"tool.execution_complete","data":{"toolCallId":"toolu_01KBgTcL9noQsoEREAJuivys",'
    b'"model":"claude-sonnet-5.5","interactionId":"1c16f980-16a8-4667-8784-fdb5183c9d40","turnId":"0","rte":true,'
    b'"success":false,"error":{"message":"Permission denied because no interactive user response was available. Retry in an interactive session so the user can approve it, or try an alternative that does not require this permission.","code":"denied"},'
    b'"toolTelemetry":{"properties":{"shell_error_category":"permission_denied"}}},'
    b'"id":"e0f94c8f-a03d-4b1a-a4df-85e14476f665","timestamp":"2026-10-09T18:47:23.903Z",'
    b'"parentId":"c7ab090e-4c1a-428e-80f1-a523b270180c"}\n'
    b'{"type":"tool.execution_start","data":{"toolCallId":"toolu_01TT9THqrXqhmQGwdHq9j8wW","toolName":"create",'
    b'"arguments":{"path":"/tmp/tmp.sLAA8pnvAx/x.txt","file_text":""},"turnId":"1","model":"claude-sonnet-5.5",'
    b'"toolTitle":"Creating file"},"id":"b8b7ee8f-b2ff-4511-add4-bde2a67a21b8",'
    b'"timestamp":"2026-10-09T18:47:25.998Z","parentId":"a571fd88-8b51-4905-bd88-7bbad7ff3fcc"}\n'
    b'{"type":"tool.execution_complete","data":{"toolCallId":"toolu_01TT9THqrXqhmQGwdHq9j8wW",'
    b'"model":"claude-sonnet-5.5","interactionId":"1c16f980-16a8-4667-8784-fdb5183c9d40","turnId":"1","rte":true,'
    b'"success":false,"error":{"message":"Permission denied because no interactive user response was available. Retry in an interactive session so the user can approve it, or try an alternative that does not require this permission.","code":"denied"},'
    b'"toolTelemetry":{"properties":{"command":"create","fileExtension":"[\\".txt\\"]"}}},'
    b'"id":"45fe525d-106e-4f2f-b3ce-e398ba6b01d8","timestamp":"2026-10-09T18:47:26.007Z",'
    b'"parentId":"0bd32f71-af50-4063-9f9c-8eb6ca879866"}\n'
    b'{"type":"result","timestamp":"2026-10-09T18:47:30.000Z","sessionId":"00000000-0000-0000-0000-000000000000",'
    b'"exitCode":0}\n'
)


def _run_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: bytes) -> CliMutationOutcome:
    monkeypatch.setattr(
        "dovo.core.agents.cli_mutation.stream_isolated_process", FakeAgentRunner().returning(stdout=stdout)
    )

    return default_copilot_run(
        CliMutationRunRequest(worktree_path=tmp_path, prompt="hi", model=None, timeout_seconds=3, tools=_ALLOW_ALL)
    )


class CopilotDenialClassificationTests:
    @pytest.mark.parametrize(
        ("stream", "expected"),
        [
            pytest.param(
                _DENY_RULE_STREAM,
                [
                    AgentDenial(
                        tool="bash",
                        message="Permission to run this tool was denied due to the following rules: `shell`",
                        capability=ToolCapability.SHELL,
                        by_rule=True,
                    )
                ],
                id="code-denied",
            ),
            pytest.param(
                _NO_INTERACTIVE_STREAM,
                [
                    AgentDenial(tool="bash", message=_NO_INTERACTIVE_MESSAGE, capability=ToolCapability.SHELL),
                    AgentDenial(tool="create", message=_NO_INTERACTIVE_MESSAGE, capability=ToolCapability.WRITE),
                ],
                id="ungranted-tool",
            ),
        ],
    )
    def test_denied_tool_event_is_collected_as_a_denial(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stream: bytes, expected: list[AgentDenial]
    ) -> None:
        outcome = _run_recorded(tmp_path, monkeypatch, stream)

        assert outcome.status == "finished"
        assert outcome.denials == expected

    def test_successful_tool_events_yield_no_denials(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        outcome = _run_recorded(tmp_path, monkeypatch, _TOOL_SUCCESS_STREAM)

        assert outcome.status == "finished"
        assert outcome.denials == []

    @pytest.mark.parametrize(
        ("tool_name", "capability"),
        [
            pytest.param("read_bash", ToolCapability.SHELL, id="shell-family"),
            pytest.param("grep", ToolCapability.READ, id="read"),
            pytest.param("edit", ToolCapability.WRITE, id="write"),
            pytest.param("web_fetch", ToolCapability.NETWORK, id="network"),
            pytest.param("mystery_tool", None, id="unknown-tool"),
        ],
    )
    def test_denied_tool_maps_to_its_capability(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool_name: str, capability: ToolCapability | None
    ) -> None:
        stream = _DENY_RULE_STREAM.replace(b'"toolName":"bash"', f'"toolName":"{tool_name}"'.encode())

        outcome = _run_recorded(tmp_path, monkeypatch, stream)

        assert [denial.capability for denial in outcome.denials] == [capability]

    @pytest.mark.parametrize(
        ("stream", "expected"),
        [
            pytest.param(
                b'{"type":"tool.execution_complete","data":{"toolCallId":"orphan","success":false,'
                b'"error":{"code":"denied","message":"Permission to run this tool was denied due to the following rules: `shell`"}}}\n',
                [
                    AgentDenial(
                        tool="unknown",
                        message="Permission to run this tool was denied due to the following rules: `shell`",
                        by_rule=True,
                    )
                ],
                id="complete-without-start",
            ),
            pytest.param(
                b'{"type":"tool.execution_complete","data":{"toolCallId":7,"success":false,'
                b'"error":{"code":"denied","message":"Permission denied"}}}\n',
                [AgentDenial(tool="unknown", message="Permission denied")],
                id="non-string-call-id",
            ),
            pytest.param(
                b'{"type":"tool.execution_complete","data":{"toolCallId":"a","success":false,'
                b'"error":{"code":"timeout","message":"timed out"}}}\n',
                [],
                id="non-denial-failure",
            ),
            pytest.param(
                b'{"type":"tool.execution_complete","data":{"toolCallId":"a","success":false,"error":"denied"}}\n',
                [],
                id="non-dict-error",
            ),
            pytest.param(
                b'{"type":"tool.execution_complete","data":{"toolCallId":"a","success":false,'
                b'"error":{"code":"denied","message":5}}}\n',
                [],
                id="non-string-message",
            ),
        ],
    )
    def test_orphan_and_malformed_complete_events_yield_only_well_formed_denials(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stream: bytes, expected: list[AgentDenial]
    ) -> None:
        outcome = _run_recorded(tmp_path, monkeypatch, stream)

        assert outcome.status == "finished"
        assert outcome.denials == expected


_OK_STREAM = b'{"type":"assistant.message","data":{"content":"ok"}}\n{"type":"result","data":{"exitCode":0}}\n'
_ORIGINAL_ARGV = [
    "gh",
    "copilot",
    "--",
    "-p",
    "",
    "--output-format",
    "json",
    "--silent",
    "--allow-all-tools",
    "--allow-all-paths",
    "--allow-all-urls",
]


def _worktree(tmp_path: Path) -> Path:
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    return worktree


def _rules(
    capability: ToolCapability, *roots: Literal["worktree", "scratch"] | None, pattern: str | None = None
) -> list[ToolRule]:
    return [ToolRule(capability=capability, root=root, pattern=pattern) for root in roots]


def _run_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: ToolPolicy, *, with_invocation: bool = True
) -> tuple[FakeAgentRunner, AgentInvocationContext | None]:
    invocation = real_invocation(tmp_path / "invocation") if with_invocation else None
    runner = FakeAgentRunner().returning(stdout=_OK_STREAM)
    monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)

    default_copilot_run(
        CliMutationRunRequest(
            worktree_path=_worktree(tmp_path),
            prompt="hi",
            timeout_seconds=3,
            tools=policy,
            invocation=invocation,
            env={"GH_TOKEN": "test-token"},
        )
    )

    return runner, invocation


def _flag_values(cmd: list[str], flag: str) -> list[str]:
    return [cmd[index + 1] for index, value in enumerate(cmd) if value == flag]


_READ_WRITE_WORKTREE = [
    *_rules(ToolCapability.READ, None, pattern="**"),
    *_rules(ToolCapability.WRITE, None, pattern="**"),
]


class CopilotPolicyRenderTests:
    def test_default_policy_argv_restricts_tools_without_allow_all(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner, _ = _run_policy(tmp_path, monkeypatch, default_tool_policy())

        cmd = runner.last_call.cmd
        assert not [part for part in cmd if part.startswith("--allow-all")]
        assert _flag_values(cmd, "--available-tools") == ["read,write"]
        assert _flag_values(cmd, "--allow-tool") == ["write"]

    def test_default_policy_argv_grants_only_the_exact_scratch_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner, invocation = _run_policy(tmp_path, monkeypatch, default_tool_policy())
        assert invocation is not None

        cmd = runner.last_call.cmd
        assert _flag_values(cmd, "--add-dir") == [str(invocation.scratch_path)]
        assert str(invocation.control_path) not in cmd

    def test_default_policy_argv_carries_the_hardening_flags(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner, _ = _run_policy(tmp_path, monkeypatch, default_tool_policy())

        assert {"--no-ask-user", "--disallow-temp-dir", "--disable-builtin-mcps"} <= set(runner.last_call.cmd)

    def test_default_policy_points_copilot_home_at_the_control_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner, invocation = _run_policy(tmp_path, monkeypatch, default_tool_policy())
        assert invocation is not None

        assert runner.last_call.env["COPILOT_HOME"] == str(invocation.control_path / "copilot-home")

    def test_worktree_only_policy_emits_no_add_dir_but_disallows_temp(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner, _ = _run_policy(tmp_path, monkeypatch, ToolPolicy(allow=_READ_WRITE_WORKTREE))

        assert "--add-dir" not in runner.last_call.cmd
        assert "--disallow-temp-dir" in runner.last_call.cmd

    def test_unrestricted_policy_argv_is_the_original_allow_all_argv(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner, _ = _run_policy(tmp_path, monkeypatch, ToolPolicy(allow_all=True), with_invocation=False)

        assert runner.last_call.cmd == _ORIGINAL_ARGV
        assert "COPILOT_HOME" not in runner.last_call.env

    @pytest.mark.parametrize(
        ("rule", "flags"),
        [
            pytest.param(
                ToolRule(capability=ToolCapability.SHELL, pattern="rm -rf"),
                ["--deny-tool", "shell(rm -rf)"],
                id="shell-exact",
            ),
            pytest.param(
                ToolRule(capability=ToolCapability.SHELL, pattern="rm *"),
                ["--deny-tool", "shell(rm:*)"],
                id="shell-prefix",
            ),
            pytest.param(ToolRule(capability=ToolCapability.SHELL), ["--deny-tool", "shell"], id="shell-all"),
            pytest.param(
                ToolRule(capability=ToolCapability.NETWORK, pattern="example.com"),
                ["--deny-url", "https://example.com", "--deny-url", "http://example.com"],
                id="network-host-both-protocols",
            ),
            pytest.param(ToolRule(capability=ToolCapability.NETWORK), ["--deny-tool", "url"], id="network-all"),
        ],
    )
    def test_allow_all_with_deny_appends_deny_flags_after_permissive_flags(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rule: ToolRule, flags: list[str]
    ) -> None:
        runner, _ = _run_policy(tmp_path, monkeypatch, ToolPolicy(allow_all=True, deny=[rule]))

        assert runner.last_call.cmd == [*_ORIGINAL_ARGV, *flags]
        assert "COPILOT_HOME" not in runner.last_call.env

    @pytest.mark.parametrize(
        "policy",
        [
            pytest.param(ToolPolicy(allow=_rules(ToolCapability.READ, None)), id="read-only"),
            pytest.param(ToolPolicy(allow=_READ_WRITE_WORKTREE), id="read-write"),
            pytest.param(default_tool_policy(), id="default"),
            pytest.param(
                ToolPolicy(allow=[ToolRule(capability=ToolCapability.SHELL, pattern="git status")]), id="shell-only"
            ),
            pytest.param(
                ToolPolicy(allow=[ToolRule(capability=ToolCapability.NETWORK, pattern="example.com")]),
                id="network-only",
            ),
        ],
    )
    def test_restrictive_policy_never_emits_a_blank_available_tools_value(
        self, tmp_path: Path, policy: ToolPolicy
    ) -> None:
        worktree, scratch, control = (tmp_path / name for name in ("worktree", "scratch", "control"))
        roots = PolicyRoots(worktree=worktree, scratch=scratch, control=control)

        rendered = render_copilot_policy(policy, roots, None)

        assert isinstance(rendered, CopilotPolicyArgs)
        (value,) = _flag_values(list(rendered.flags), "--available-tools")
        assert value
        assert set(value.split(",")) <= {"read", "write", "shell", "web_fetch"}

    @pytest.mark.parametrize(
        ("allow", "expected"),
        [
            pytest.param(_READ_WRITE_WORKTREE, "read,write", id="read-write"),
            pytest.param(
                [ToolRule(capability=ToolCapability.SHELL), ToolRule(capability=ToolCapability.NETWORK)],
                "shell,web_fetch",
                id="shell-network",
            ),
            pytest.param(
                [
                    ToolRule(capability=ToolCapability.NETWORK),
                    ToolRule(capability=ToolCapability.SHELL),
                    *reversed(_READ_WRITE_WORKTREE),
                ],
                "read,write,shell,web_fetch",
                id="all-capabilities-in-reverse-rule-order",
            ),
        ],
    )
    def test_multiple_capabilities_render_one_available_tools_value_in_fixed_order(
        self, tmp_path: Path, allow: list[ToolRule], expected: str
    ) -> None:
        roots = PolicyRoots(worktree=tmp_path / "worktree", scratch=tmp_path / "scratch", control=tmp_path / "control")

        rendered = render_copilot_policy(ToolPolicy(allow=allow), roots, None)

        assert isinstance(rendered, CopilotPolicyArgs)
        assert _flag_values(list(rendered.flags), "--available-tools") == [expected]

    def test_network_host_and_subdomain_wildcard_render_both_protocols(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        policy = ToolPolicy(
            allow=[
                ToolRule(capability=ToolCapability.NETWORK, pattern="example.com"),
                ToolRule(capability=ToolCapability.NETWORK, pattern="*.example.org"),
            ],
            deny=[ToolRule(capability=ToolCapability.NETWORK, pattern="*.example.net")],
        )

        runner, _ = _run_policy(tmp_path, monkeypatch, policy)

        cmd = runner.last_call.cmd
        assert _flag_values(cmd, "--allow-url") == [
            "https://example.com",
            "http://example.com",
            "https://*.example.org",
            "http://*.example.org",
        ]
        assert _flag_values(cmd, "--deny-url") == ["https://*.example.net", "http://*.example.net"]
        assert _flag_values(cmd, "--available-tools") == ["web_fetch"]

    def test_shell_allows_render_allow_tool_flags_and_make_shell_available(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        policy = ToolPolicy(
            allow=[
                ToolRule(capability=ToolCapability.SHELL),
                ToolRule(capability=ToolCapability.SHELL, pattern="git add *"),
                ToolRule(capability=ToolCapability.SHELL, pattern="git status"),
            ]
        )

        runner, _ = _run_policy(tmp_path, monkeypatch, policy)

        cmd = runner.last_call.cmd
        assert _flag_values(cmd, "--allow-tool") == ["shell", "shell(git add:*)", "shell(git status)"]
        assert _flag_values(cmd, "--available-tools") == ["shell"]

    def test_unscoped_network_allow_renders_allow_all_urls_and_makes_web_fetch_available(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        policy = ToolPolicy(allow=[ToolRule(capability=ToolCapability.NETWORK)])

        runner, _ = _run_policy(tmp_path, monkeypatch, policy)

        cmd = runner.last_call.cmd
        assert "--allow-all-urls" in cmd
        assert _flag_values(cmd, "--available-tools") == ["web_fetch"]

    @pytest.mark.parametrize(
        "policy",
        [
            pytest.param("empty-grants", id="empty-grants"),
            pytest.param("read-src-glob", id="sub-root-glob"),
            pytest.param("write-deny", id="read-write-deny"),
            pytest.param("scratch-only", id="worktree-not-granted"),
            pytest.param("scratch-write-without-read", id="mixed-root-scope"),
            pytest.param("read-scratch-write-worktree", id="scratch-readable-not-writable"),
            pytest.param("restrictive-without-control-path", id="no-control-dir"),
            pytest.param("scratch-without-context", id="no-invocation-context"),
        ],
    )
    def test_unrepresentable_policy_is_rejected_before_spawn(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: str
    ) -> None:
        policies = {
            "empty-grants": ToolPolicy(),
            "read-src-glob": ToolPolicy(allow=_rules(ToolCapability.READ, None, pattern="src/**")),
            "write-deny": ToolPolicy(
                allow=_READ_WRITE_WORKTREE, deny=_rules(ToolCapability.WRITE, None, pattern=".dovo/**")
            ),
            "scratch-only": ToolPolicy(allow=_rules(ToolCapability.READ, "scratch")),
            "scratch-write-without-read": ToolPolicy(
                allow=[*_rules(ToolCapability.READ, None), *_rules(ToolCapability.WRITE, None, "scratch")]
            ),
            "read-scratch-write-worktree": ToolPolicy(
                allow=[*_rules(ToolCapability.READ, None, "scratch"), *_rules(ToolCapability.WRITE, None)]
            ),
            "restrictive-without-control-path": ToolPolicy(allow=_rules(ToolCapability.READ, None)),
            "scratch-without-context": ToolPolicy(allow=_rules(ToolCapability.READ, None, "scratch")),
        }
        with_invocation = policy not in {"restrictive-without-control-path", "scratch-without-context"}
        runner = FakeAgentRunner().returning(stdout=_OK_STREAM)
        monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)
        builder = AgentRequestBuilder().with_worktree_path(git_repo).with_tools(policies[policy])
        if with_invocation:
            builder = builder.with_invocation(real_invocation(tmp_path / "invocation"))

        response = CopilotAgentAdapter().invoke(builder.build())

        assert response.status == AgentResponseStatus.PROVIDER_ERROR
        assert response.errors == [tool_policy_unsupported_message("copilot")]
        assert runner.calls == []

    def _run_with_unknown_option_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> tuple[CliMutationOutcome, FakeAgentRunner]:
        """Run the default policy against a gh that rejects '--disallow-temp-dir' and return outcome and runner."""
        runner = FakeAgentRunner().returning(returncode=1, stderr=b"error: unknown option '--disallow-temp-dir'")
        monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)

        outcome = default_copilot_run(
            CliMutationRunRequest(
                worktree_path=_worktree(tmp_path),
                prompt="hi",
                timeout_seconds=3,
                tools=default_tool_policy(),
                invocation=real_invocation(tmp_path / "invocation"),
            )
        )

        return outcome, runner

    def test_unknown_option_failure_returns_update_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        outcome, _ = self._run_with_unknown_option_failure(tmp_path, monkeypatch)

        assert outcome == CliMutationOutcome(
            status="error",
            error_detail="Installed GitHub Copilot CLI lacks a required tool-policy control. "
            "Fix: run `copilot update` or reinstall the GitHub Copilot CLI.",
        )

    def test_unknown_option_failure_does_not_retry_without_restrictions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spawn exactly once, with no '--allow-all*' flag."""
        _, runner = self._run_with_unknown_option_failure(tmp_path, monkeypatch)

        assert len(runner.calls) == 1
        assert not [part for part in runner.last_call.cmd if part.startswith("--allow-all")]

    def test_unrepresentable_policy_run_returns_error_outcome_without_spawn(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeAgentRunner().returning(stdout=_OK_STREAM)
        monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)

        outcome = default_copilot_run(
            CliMutationRunRequest(
                worktree_path=_worktree(tmp_path),
                prompt="hi",
                timeout_seconds=3,
                tools=ToolPolicy(),
                invocation=real_invocation(tmp_path / "invocation"),
            )
        )

        assert outcome == CliMutationOutcome(status="error", error_detail=tool_policy_unsupported_message("copilot"))
        assert runner.calls == []


def _mcp_rule(pattern: str) -> ToolRule:
    return ToolRule(capability=ToolCapability.MCP, pattern=pattern)


def _user_mcp_config(tmp_path: Path, *servers: str) -> UserMcpConfig:
    """Build a user MCP config under tmp_path that defines the given server names."""
    return UserMcpConfig(path=tmp_path / "user" / "mcp-config.json", servers=frozenset(servers))


def _patch_user_mcp(monkeypatch: pytest.MonkeyPatch, user_mcp: UserMcpConfig | None) -> None:
    monkeypatch.setattr("dovo.core.agents.copilot.load_user_mcp_config", lambda: user_mcp)


class CopilotMcpRenderTests:
    def _run_mcp_allow(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rule: ToolRule, user_config: UserMcpConfig
    ) -> list[str]:
        """Run an allow-only policy with rule against the given user MCP config and return the argv."""
        _patch_user_mcp(monkeypatch, user_config)

        runner, _ = _run_policy(tmp_path, monkeypatch, ToolPolicy(allow=[rule]))

        return runner.last_call.cmd

    def test_mcp_allow_passes_the_user_config_and_disables_its_other_servers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Allow 'probe-srv' from a user config that also defines 'other-srv'; only 'other-srv' is disabled."""
        user_config = _user_mcp_config(tmp_path, "probe-srv", "other-srv")

        cmd = self._run_mcp_allow(tmp_path, monkeypatch, _mcp_rule("probe-srv/mark"), user_config)

        assert _flag_values(cmd, "--additional-mcp-config") == [f"@{user_config.path}"]
        assert _flag_values(cmd, "--disable-mcp-server") == ["other-srv"]

    def test_mcp_allow_disables_the_builtin_mcp_servers(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        user_config = _user_mcp_config(tmp_path, "probe-srv")

        cmd = self._run_mcp_allow(tmp_path, monkeypatch, _mcp_rule("probe-srv/mark"), user_config)

        assert "--disable-builtin-mcps" in cmd

    @pytest.mark.parametrize(
        ("rule", "available", "grant"),
        [
            pytest.param(_mcp_rule("probe-srv/mark"), "probe-srv-mark", "probe-srv(mark)", id="server-tool"),
            pytest.param(_mcp_rule("probe-srv/*"), "probe-srv", "probe-srv", id="server-wildcard"),
        ],
    )
    def test_mcp_allow_rule_renders_available_tools_and_allow_tool_flags(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rule: ToolRule, available: str, grant: str
    ) -> None:
        cmd = self._run_mcp_allow(tmp_path, monkeypatch, rule, _user_mcp_config(tmp_path, "probe-srv"))

        assert _flag_values(cmd, "--available-tools") == [available]
        assert _flag_values(cmd, "--allow-tool") == [grant]

    def test_mcp_deny_under_allow_all_renders_deny_flags_without_user_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_user_mcp(monkeypatch, None)
        policy = ToolPolicy(allow_all=True, deny=[_mcp_rule("probe-srv/mark"), _mcp_rule("other-srv/*")])

        runner, _ = _run_policy(tmp_path, monkeypatch, policy, with_invocation=False)

        assert runner.last_call.cmd == [
            *_ORIGINAL_ARGV,
            "--deny-tool",
            "probe-srv(mark)",
            "--deny-tool",
            "other-srv",
        ]

    def test_mcp_deny_under_a_restrictive_policy_renders_deny_flag_without_user_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_user_mcp(monkeypatch, None)
        policy = ToolPolicy(allow=_READ_WRITE_WORKTREE, deny=[_mcp_rule("probe-srv/mark")])

        runner, _ = _run_policy(tmp_path, monkeypatch, policy)

        cmd = runner.last_call.cmd
        assert _flag_values(cmd, "--deny-tool") == ["probe-srv(mark)"]
        assert "--disable-builtin-mcps" in cmd
        assert "--additional-mcp-config" not in cmd

    def _run_builtin_github_allow(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, user_servers: frozenset[str] | None
    ) -> tuple[list[str], Path]:
        """Run an allow for the built-in GitHub server and return the argv and the user config path."""
        config_path = tmp_path / "user" / "mcp-config.json"
        _patch_user_mcp(
            monkeypatch, None if user_servers is None else UserMcpConfig(path=config_path, servers=user_servers)
        )

        runner, _ = _run_policy(tmp_path, monkeypatch, ToolPolicy(allow=[_mcp_rule("github-mcp-server/search_users")]))

        return runner.last_call.cmd, config_path

    def test_builtin_github_server_allow_keeps_builtin_mcps_enabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cmd, _ = self._run_builtin_github_allow(tmp_path, monkeypatch, None)

        assert "--disable-builtin-mcps" not in cmd

    @pytest.mark.parametrize(
        ("user_servers", "disabled"),
        [
            pytest.param(None, ["githubiq"], id="no-user-config"),
            pytest.param(frozenset({"probe-srv"}), ["githubiq", "probe-srv"], id="user-config-servers-disabled"),
        ],
    )
    def test_builtin_github_server_allow_disables_githubiq_and_user_config_servers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, user_servers: frozenset[str] | None, disabled: list[str]
    ) -> None:
        cmd, config_path = self._run_builtin_github_allow(tmp_path, monkeypatch, user_servers)

        assert _flag_values(cmd, "--additional-mcp-config") == ([] if user_servers is None else [f"@{config_path}"])
        assert _flag_values(cmd, "--disable-mcp-server") == disabled

    def test_builtin_github_server_allow_renders_available_tools_and_allow_tool_flags(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cmd, _ = self._run_builtin_github_allow(tmp_path, monkeypatch, None)

        assert _flag_values(cmd, "--available-tools") == ["github-mcp-server-search_users"]
        assert _flag_values(cmd, "--allow-tool") == ["github-mcp-server(search_users)"]


class UserMcpConfigTests:
    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            pytest.param('{"mcpServers": {"a": {}, "b": {}}}', frozenset({"a", "b"}), id="two-servers"),
            pytest.param('{"mcpServers": {}}', frozenset(), id="no-servers"),
            pytest.param("not json", None, id="unparseable"),
            pytest.param('{"mcpServers": []}', None, id="servers-not-an-object"),
            pytest.param(None, None, id="missing-file"),
        ],
    )
    def test_load_reads_server_names_from_the_host_copilot_home(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str | None, expected: frozenset[str] | None
    ) -> None:
        monkeypatch.setenv("COPILOT_HOME", str(tmp_path))
        if content is not None:
            (tmp_path / "mcp-config.json").write_text(content)

        loaded = load_user_mcp_config()

        assert loaded == (
            None if expected is None else UserMcpConfig(path=tmp_path / "mcp-config.json", servers=expected)
        )

    def test_load_without_copilot_home_reads_the_dot_copilot_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("COPILOT_HOME", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        (tmp_path / ".copilot").mkdir()
        (tmp_path / ".copilot" / "mcp-config.json").write_text('{"mcpServers": {"a": {}}}')

        assert load_user_mcp_config() == UserMcpConfig(
            path=tmp_path / ".copilot" / "mcp-config.json", servers=frozenset({"a"})
        )

    def test_load_without_a_home_directory_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:

        def no_home() -> Path:
            raise RuntimeError("Could not determine home directory.")

        monkeypatch.delenv("COPILOT_HOME", raising=False)
        monkeypatch.setattr(Path, "home", no_home)

        assert load_user_mcp_config() is None


class CopilotMcpUnsupportedTests:
    @pytest.mark.parametrize(
        "policy",
        [
            pytest.param("mcp-allow-unknown-server", id="mcp-unknown-server"),
            pytest.param("mcp-allow-without-user-config", id="mcp-no-user-config"),
            pytest.param("mcp-allow-without-pattern", id="mcp-no-pattern"),
        ],
    )
    def test_mcp_allow_for_an_unavailable_server_is_rejected_before_spawn(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: str
    ) -> None:
        user_config = UserMcpConfig(path=tmp_path / "mcp-config.json", servers=frozenset({"other-srv"}))
        cases = {
            "mcp-allow-unknown-server": (ToolPolicy(allow=[_mcp_rule("ghost-srv/tool")]), user_config),
            "mcp-allow-without-user-config": (ToolPolicy(allow=[_mcp_rule("probe-srv/mark")]), None),
            "mcp-allow-without-pattern": (
                ToolPolicy(allow=[ToolRule(capability=ToolCapability.MCP)]),
                user_config,
            ),
        }
        mcp_policy, loaded = cases[policy]
        _patch_user_mcp(monkeypatch, loaded)
        runner = FakeAgentRunner().returning(stdout=_OK_STREAM)
        monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)
        request = (
            AgentRequestBuilder()
            .with_worktree_path(git_repo)
            .with_tools(mcp_policy)
            .with_invocation(real_invocation(tmp_path / "invocation"))
            .build()
        )

        response = CopilotAgentAdapter().invoke(request)

        assert response.status == AgentResponseStatus.PROVIDER_ERROR
        assert response.errors == [tool_policy_unsupported_message("copilot")]
        assert runner.calls == []

    def test_mcp_deny_without_a_pattern_is_rejected_before_spawn(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_user_mcp(monkeypatch, None)
        runner = FakeAgentRunner().returning(stdout=_OK_STREAM)
        monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)

        outcome = default_copilot_run(
            CliMutationRunRequest(
                worktree_path=_worktree(tmp_path),
                prompt="hi",
                timeout_seconds=3,
                tools=ToolPolicy(allow_all=True, deny=[ToolRule(capability=ToolCapability.MCP)]),
            )
        )

        assert outcome == CliMutationOutcome(status="error", error_detail=tool_policy_unsupported_message("copilot"))
        assert runner.calls == []
