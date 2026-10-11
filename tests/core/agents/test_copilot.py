"""Tests for the GitHub Copilot CLI direct-mutation agent adapter."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Literal

import pytest

from dovo.common.tool_policy import ToolCapability, ToolPolicy, ToolRule
from dovo.core.agents import (
    AgentEnvMode,
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
    default_copilot_run,
    render_copilot_policy,
    resolve_copilot_token,
)
from dovo.core.agents.models import AgentDenial
from tests.harness import AgentRequestBuilder, FakeAgentRunner

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
        """[tier-1/unit] resolve_copilot_token: with both variables cleared then env applied, returns the first non-blank stripped value, else None."""
        for name in ("GH_TOKEN", "GITHUB_TOKEN"):
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)

        assert resolve_copilot_token() == expected

    def test_preflight_requires_token(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """[tier-1/unit] CopilotAgentAdapter.invoke: GH_TOKEN and GITHUB_TOKEN unset returns PROVIDER_ERROR with the canonical missing-credential error."""
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        adapter = CopilotAgentAdapter()

        resp = adapter.invoke(AgentRequestBuilder().with_worktree_path(tmp_path).build())

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert resp.errors == [
            "Agent provider error (AGENT_PROVIDER_ERROR): missing GH_TOKEN or GITHUB_TOKEN. "
            "Fix: export GH_TOKEN=... or export GITHUB_TOKEN=..."
        ]


class CopilotRunTests:
    def test_missing_token_returns_canonical_error_without_running_gh(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] default_copilot_run: GH_TOKEN and GITHUB_TOKEN unset returns the canonical error outcome, and the patched run_isolated_process is never called."""
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        runner = FakeAgentRunner()
        monkeypatch.setattr("dovo.core.agents.copilot.run_isolated_process", runner)

        outcome = default_copilot_run(
            CliMutationRunRequest(worktree_path=tmp_path, prompt="hi", model=None, timeout_seconds=3, tools=_ALLOW_ALL)
        )

        assert outcome == CliMutationOutcome(
            status="error",
            error_detail="missing GH_TOKEN or GITHUB_TOKEN. Fix: export GH_TOKEN=... or export GITHUB_TOKEN=...",
        )
        assert runner.calls == []

    def test_default_run_parses_jsonl(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """gh copilot runs in the worktree with the prompt on stdin and its JSONL stream is parsed to text."""
        runner = FakeAgentRunner().returning(
            stdout=(
                b'{"type":"assistant.message","data":{"content":"hello"}}\n{"type":"result","data":{"exitCode":0}}\n'
            )
        )
        monkeypatch.setattr("dovo.core.agents.copilot.run_isolated_process", runner)

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

        assert outcome.status == "finished"
        assert outcome.result_text == "hello"
        assert outcome.error_detail is None

        call = runner.last_call
        assert call.cwd == tmp_path
        assert call.input_data == b"hi"
        assert call.timeout_seconds == 3
        assert call.env["GH_TOKEN"] == "test-token"

    def test_missing_gh_binary_returns_error_status(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A missing gh binary maps to an error outcome naming the GitHub CLI."""
        runner = FakeAgentRunner().raising(FileNotFoundError("gh"))
        monkeypatch.setattr("dovo.core.agents.copilot.run_isolated_process", runner)

        outcome = default_copilot_run(
            CliMutationRunRequest(worktree_path=tmp_path, prompt="hi", model=None, timeout_seconds=3, tools=_ALLOW_ALL)
        )

        assert outcome.status == "error"
        assert outcome.result_text is None
        assert outcome.error_detail == (
            "gh is not installed or not on PATH: gh. Fix: install the GitHub CLI (https://cli.github.com)"
        )

    def test_process_timeout_returns_timeout_status(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A subprocess timeout maps to a timeout outcome."""
        runner = FakeAgentRunner().raising(subprocess.TimeoutExpired(cmd="gh", timeout=3))
        monkeypatch.setattr("dovo.core.agents.copilot.run_isolated_process", runner)

        outcome = default_copilot_run(
            CliMutationRunRequest(worktree_path=tmp_path, prompt="hi", model=None, timeout_seconds=3, tools=_ALLOW_ALL)
        )

        assert outcome.status == "timeout"
        assert outcome.result_text is None
        assert outcome.error_detail is None


class CopilotInvokeOutcomeTests:
    def test_invoke_maps_elapsed_subprocess_timeout_to_timeout_status(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] CopilotAgentAdapter.invoke: run_isolated_process raising subprocess.TimeoutExpired returns status TIMEOUT with errors[0].splitlines()[0] == "Agent timed out after 10s (provider=copilot)." and no other status."""
        runner = FakeAgentRunner().raising(subprocess.TimeoutExpired(cmd="gh", timeout=10))
        monkeypatch.setattr("dovo.core.agents.copilot.run_isolated_process", runner)

        resp = CopilotAgentAdapter().invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert resp.status == AgentResponseStatus.TIMEOUT
        assert resp.errors[0].splitlines()[0] == "Agent timed out after 10s (provider=copilot)."

    @pytest.mark.parametrize(
        ("runner", "expected_error"),
        [
            pytest.param(
                FakeAgentRunner().raising(FileNotFoundError("gh")),
                "Agent provider error (AGENT_PROVIDER_ERROR): gh is not installed or not on PATH: gh. "
                "Fix: install the GitHub CLI (https://cli.github.com)",
                id="gh-missing",
            ),
            pytest.param(
                FakeAgentRunner().raising(OSError("exec failed")),
                "Agent provider error (AGENT_PROVIDER_ERROR): exec failed",
                id="os-error",
            ),
            pytest.param(
                FakeAgentRunner().returning(returncode=1, stderr=b"gh failed"),
                "Agent provider error (AGENT_PROVIDER_ERROR): gh failed",
                id="non-zero-exit",
            ),
        ],
    )
    def test_invoke_maps_non_timeout_subprocess_failure_to_provider_error(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch, runner: FakeAgentRunner, expected_error: str
    ) -> None:
        """[tier-1/integration] CopilotAgentAdapter.invoke: gh missing, an OSError, or a non-zero exit returns status PROVIDER_ERROR (never TIMEOUT) with errors == [expected_error]."""
        monkeypatch.setattr("dovo.core.agents.copilot.run_isolated_process", runner)

        resp = CopilotAgentAdapter().invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert resp.errors == [expected_error]


class _FileWritingRunner(FakeAgentRunner):
    """Runner double that adds a file to the worktree before returning, as an editing agent would."""

    def __call__(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        input_data: bytes,
        timeout_seconds: float,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        (cwd / "edited.txt").write_text("edited\n", encoding="utf-8")
        return super().__call__(cmd, cwd=cwd, env=env, input_data=input_data, timeout_seconds=timeout_seconds, **kwargs)


def _assistant_stream(text: str) -> bytes:
    return (
        json.dumps({"type": "assistant.message", "data": {"content": text}}).encode()
        + b'\n{"type":"result","data":{"exitCode":0}}\n'
    )


class CopilotRedactionTests:
    def test_invoke_masks_token_reflected_by_gh_stderr(self, git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """[tier-1/integration] CopilotAgentAdapter.invoke: GH_TOKEN="gho_"+36 chars with FakeAgentRunner returning returncode=1 and stderr containing that token yields PROVIDER_ERROR whose errors, joined, do not contain the token."""
        token = "gho_" + "a" * 36
        monkeypatch.setenv("GH_TOKEN", token)
        runner = FakeAgentRunner().returning(returncode=1, stderr=f"auth failed for {token}".encode())
        monkeypatch.setattr("dovo.core.agents.copilot.run_isolated_process", runner)

        resp = CopilotAgentAdapter().invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert token not in "\n".join(resp.errors)
        assert "[REDACTED:GH_TOKEN]" in "\n".join(resp.errors)

    def test_invoke_masks_both_copilot_credential_alternatives(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] CopilotAgentAdapter.invoke: GH_TOKEN="short1" and GITHUB_TOKEN="short2" with the runner reflecting both in result text yield a response whose raw_text has neither."""
        monkeypatch.setenv("GH_TOKEN", "sh1")
        monkeypatch.setenv("GITHUB_TOKEN", "sh2")
        runner = FakeAgentRunner().returning(stdout=_assistant_stream("saw sh1 and sh2"))
        monkeypatch.setattr("dovo.core.agents.copilot.run_isolated_process", runner)

        resp = CopilotAgentAdapter().invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert resp.raw_text == "saw [REDACTED:GH_TOKEN] and [REDACTED:GITHUB_TOKEN]"

    def test_invoke_masks_reflected_token_in_a_proposed_patch_response(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] CopilotAgentAdapter.invoke: a runner that writes a file and reflects GH_TOKEN in the final message yields PROPOSED_PATCH whose raw_text is masked and whose unified_diff is unchanged."""
        token = "gho_" + "b" * 36
        monkeypatch.setenv("GH_TOKEN", token)
        runner = _FileWritingRunner().returning(stdout=_assistant_stream(f"done with {token}"))
        monkeypatch.setattr("dovo.core.agents.copilot.run_isolated_process", runner)

        resp = CopilotAgentAdapter().invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert resp.status == AgentResponseStatus.PROPOSED_PATCH
        assert resp.raw_text == "done with [REDACTED:GH_TOKEN]"
        assert resp.unified_diff is not None
        assert "+edited" in resp.unified_diff


def _invocation(root: Path) -> AgentInvocationContext:
    return AgentInvocationContext(invocation_id="a" * 32, scratch_path=root / "scratch", control_path=root / "control")


class CopilotEnvBoundaryTests:
    def test_allowlist_spawn_env_lacks_unrelated_secrets_and_carries_resolved_token(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] CopilotAgentAdapter.invoke: FakeAgentRunner at dovo.core.agents.copilot.run_isolated_process records an env without AWS_SECRET_ACCESS_KEY, SSH_AUTH_SOCK, ANTHROPIC_API_KEY, DOVO_TEST_UNRELATED_SECRET and with GH_TOKEN equal to the resolved token."""
        for name in ("AWS_SECRET_ACCESS_KEY", "SSH_AUTH_SOCK", "ANTHROPIC_API_KEY", "DOVO_TEST_UNRELATED_SECRET"):
            monkeypatch.setenv(name, "host-value")
        runner = FakeAgentRunner().returning(stdout=_assistant_stream("ok"))
        monkeypatch.setattr("dovo.core.agents.copilot.run_isolated_process", runner)

        CopilotAgentAdapter().invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        env = runner.last_call.env
        assert env["GH_TOKEN"] == "test-token"
        assert "PATH" in env
        assert not {"AWS_SECRET_ACCESS_KEY", "SSH_AUTH_SOCK", "ANTHROPIC_API_KEY", "DOVO_TEST_UNRELATED_SECRET"} & set(
            env
        )

    def test_inherit_spawn_env_keeps_unrelated_secrets(self, git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """[tier-1/integration] CopilotAgentAdapter.invoke: with env_mode 'inherit' the recorded env contains DOVO_TEST_UNRELATED_SECRET."""
        monkeypatch.setenv("DOVO_TEST_UNRELATED_SECRET", "host-value")
        runner = FakeAgentRunner().returning(stdout=_assistant_stream("ok"))
        monkeypatch.setattr("dovo.core.agents.copilot.run_isolated_process", runner)

        CopilotAgentAdapter().invoke(
            AgentRequestBuilder().with_worktree_path(git_repo).with_env_mode("inherit").build()
        )

        assert runner.last_call.env["DOVO_TEST_UNRELATED_SECRET"] == "host-value"

    @pytest.mark.parametrize("mode", [pytest.param("allowlist", id="allowlist"), pytest.param("inherit", id="inherit")])
    def test_competing_copilot_controls_never_reach_the_subprocess(
        self, mode: AgentEnvMode, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] CopilotAgentAdapter.invoke: ambient COPILOT_GITHUB_TOKEN, COPILOT_ALLOW_ALL, and the non-winning GITHUB_TOKEN are absent from the recorded env while COPILOT_MODEL equals request.model."""
        monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "competing")
        monkeypatch.setenv("COPILOT_ALLOW_ALL", "1")
        monkeypatch.setenv("COPILOT_MODEL", "ambient-model")
        monkeypatch.setenv("GITHUB_TOKEN", "non-winning")
        runner = FakeAgentRunner().returning(stdout=_assistant_stream("ok"))
        monkeypatch.setattr("dovo.core.agents.copilot.run_isolated_process", runner)

        CopilotAgentAdapter().invoke(
            AgentRequestBuilder().with_worktree_path(git_repo).with_model("gpt-x").with_env_mode(mode).build()
        )

        env = runner.last_call.env
        assert env["COPILOT_MODEL"] == "gpt-x"
        assert env["GH_TOKEN"] == "test-token"
        assert not {"COPILOT_GITHUB_TOKEN", "COPILOT_ALLOW_ALL", "GITHUB_TOKEN"} & set(env)

    def test_scratch_keys_follow_each_invocation_across_repeated_calls(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] CopilotAgentAdapter.invoke: two calls with different invocation contexts record DOVO_AGENT_SCRATCH, TMPDIR, TMP, TEMP equal to each call's own scratch_path, never control_path or its parent."""
        runner = FakeAgentRunner().returning(stdout=_assistant_stream("ok"))
        monkeypatch.setattr("dovo.core.agents.copilot.run_isolated_process", runner)
        adapter = CopilotAgentAdapter()
        invocations = [_invocation(tmp_path / "first"), _invocation(tmp_path / "second")]

        for invocation in invocations:
            adapter.invoke(AgentRequestBuilder().with_worktree_path(git_repo).with_invocation(invocation).build())

        keys = ("DOVO_AGENT_SCRATCH", "TMPDIR", "TMP", "TEMP")
        recorded = [[call.env[key] for key in keys] for call in runner.calls]
        assert recorded == [[str(invocation.scratch_path)] * 4 for invocation in invocations]
        forbidden = {
            str(path)
            for invocation in invocations
            for path in (invocation.control_path, invocation.control_path.parent)
        }
        assert not forbidden & {value for call in runner.calls for value in call.env.values()}


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
    monkeypatch.setattr("dovo.core.agents.copilot.run_isolated_process", FakeAgentRunner().returning(stdout=stdout))

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
        """[tier-1/unit] default_copilot_run: recorded JSONL with a denied tool.execution_complete and exit 0 -> CliMutationOutcome(status='finished', denials == [AgentDenial(tool, message, capability, by_rule)]) with by_rule True only for the 'following rules' form."""
        outcome = _run_recorded(tmp_path, monkeypatch, stream)

        assert outcome.status == "finished"
        assert outcome.denials == expected

    def test_successful_tool_events_yield_no_denials(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """[tier-1/unit] default_copilot_run: recorded JSONL with only successful tool events -> denials == []."""
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
        """[tier-1/unit] default_copilot_run: a denied call to read_bash/grep/edit/web_fetch maps to shell/read/write/network and an unlisted tool maps to None."""
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
    def test_unmatched_or_malformed_complete_events(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stream: bytes, expected: list[AgentDenial]
    ) -> None:
        """[tier-1/unit] default_copilot_run: a failed tool.execution_complete with no matching start falls back to tool 'unknown'; non-denial failures and malformed error payloads yield no denial."""
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


def _real_invocation(tmp_path: Path) -> AgentInvocationContext:
    """Create real scratch and control directories outside the worktree."""
    root = tmp_path / "invocation"
    scratch, control = root / "scratch", root / "control"
    scratch.mkdir(parents=True)
    control.mkdir()

    return AgentInvocationContext(
        invocation_id="a" * 32, scratch_path=scratch.resolve(), control_path=control.resolve()
    )


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
    invocation = _real_invocation(tmp_path) if with_invocation else None
    runner = FakeAgentRunner().returning(stdout=_OK_STREAM)
    monkeypatch.setattr("dovo.core.agents.copilot.run_isolated_process", runner)

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
    def test_default_policy_argv_restricts_tools_and_grants_only_exact_scratch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] default_copilot_run: default policy argv has no --allow-all-*, contains '--available-tools', '--no-ask-user', '--disallow-temp-dir', '--disable-builtin-mcps', exactly one '--add-dir' equal to str(scratch); no value equals str(control_path); env['COPILOT_HOME'] == str(control_path / 'copilot-home')."""
        runner, invocation = _run_policy(tmp_path, monkeypatch, default_tool_policy())
        assert invocation is not None

        cmd, env = runner.last_call.cmd, runner.last_call.env

        assert not [part for part in cmd if part.startswith("--allow-all")]
        assert {"--available-tools", "--no-ask-user", "--disallow-temp-dir", "--disable-builtin-mcps"} <= set(cmd)
        assert _flag_values(cmd, "--available-tools") == ["read,write"]
        assert _flag_values(cmd, "--add-dir") == [str(invocation.scratch_path)]
        assert _flag_values(cmd, "--allow-tool") == ["write"]
        assert str(invocation.control_path) not in cmd
        assert env["COPILOT_HOME"] == str(invocation.control_path / "copilot-home")

    def test_worktree_only_policy_emits_no_add_dir_but_disallows_temp(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] default_copilot_run: worktree-only read/write policy argv has no '--add-dir' and contains '--disallow-temp-dir'."""
        runner, _ = _run_policy(tmp_path, monkeypatch, ToolPolicy(allow=_READ_WRITE_WORKTREE))

        assert "--add-dir" not in runner.last_call.cmd
        assert "--disallow-temp-dir" in runner.last_call.cmd

    def test_unrestricted_policy_argv_is_the_original_allow_all_argv(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] default_copilot_run: allow_all=True with no denies yields the original eleven-element argv and an env without COPILOT_HOME."""
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
        """[tier-1/unit] default_copilot_run: allow_all=True + one deny -> argv tail is the three --allow-all-* flags followed by exactly the expected deny flags."""
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
        """[tier-1/unit] render_copilot_policy: every restrictive policy that renders has a non-empty '--available-tools' value built only from read, write, shell, web_fetch."""
        worktree, scratch, control = (tmp_path / name for name in ("worktree", "scratch", "control"))
        roots = PolicyRoots(worktree=worktree, scratch=scratch, control=control)

        rendered = render_copilot_policy(policy, roots)

        assert isinstance(rendered, CopilotPolicyArgs)
        (value,) = _flag_values(list(rendered.flags), "--available-tools")
        assert value
        assert set(value.split(",")) <= {"read", "write", "shell", "web_fetch"}

    def test_network_host_and_subdomain_wildcard_render_both_protocols(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] default_copilot_run: allow network 'example.com' and '*.example.org', deny '*.example.net' -> argv contains '--allow-url' for https:// and http:// of 'example.com' and '*.example.org', and '--deny-url' for https:// and http:// of '*.example.net'."""
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

    def test_shell_and_unscoped_network_allows_render_tool_and_url_flags(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] default_copilot_run: shell allows render one --allow-tool each and an unscoped network allow renders --allow-all-urls."""
        policy = ToolPolicy(
            allow=[
                ToolRule(capability=ToolCapability.SHELL),
                ToolRule(capability=ToolCapability.SHELL, pattern="git add *"),
                ToolRule(capability=ToolCapability.SHELL, pattern="git status"),
                ToolRule(capability=ToolCapability.NETWORK),
            ]
        )
        expected = ["shell", "shell(git add:*)", "shell(git status)"]
        runner, _ = _run_policy(tmp_path, monkeypatch, policy)

        cmd = runner.last_call.cmd
        assert _flag_values(cmd, "--allow-tool") == expected
        assert "--allow-all-urls" in cmd
        assert _flag_values(cmd, "--available-tools") == ["shell,web_fetch"]

    @pytest.mark.parametrize(
        "policy",
        [
            pytest.param("empty-grants", id="empty-grants"),
            pytest.param("read-src-glob", id="sub-root-glob"),
            pytest.param("write-deny", id="read-write-deny"),
            pytest.param("mcp-allow", id="mcp-allow-unsupported"),
            pytest.param("mcp-deny-under-allow-all", id="mcp-deny-unsupported"),
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
        """[tier-1/unit] CopilotAgentAdapter.invoke: PROVIDER_ERROR with errors == [tool_policy_unsupported_message('copilot')] and FakeAgentRunner.calls == []."""
        policies = {
            "empty-grants": ToolPolicy(),
            "read-src-glob": ToolPolicy(allow=_rules(ToolCapability.READ, None, pattern="src/**")),
            "write-deny": ToolPolicy(
                allow=_READ_WRITE_WORKTREE, deny=_rules(ToolCapability.WRITE, None, pattern=".dovo/**")
            ),
            "mcp-allow": ToolPolicy(
                allow=[*_READ_WRITE_WORKTREE, ToolRule(capability=ToolCapability.MCP, pattern="srv/*")]
            ),
            "mcp-deny-under-allow-all": ToolPolicy(
                allow_all=True, deny=[ToolRule(capability=ToolCapability.MCP, pattern="srv/tool")]
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
        monkeypatch.setattr("dovo.core.agents.copilot.run_isolated_process", runner)
        builder = AgentRequestBuilder().with_worktree_path(git_repo).with_tools(policies[policy])
        if with_invocation:
            builder = builder.with_invocation(_real_invocation(tmp_path))

        response = CopilotAgentAdapter().invoke(builder.build())

        assert response.status == AgentResponseStatus.PROVIDER_ERROR
        assert response.errors == [tool_policy_unsupported_message("copilot")]
        assert runner.calls == []

    def test_unknown_option_failure_returns_update_error_without_second_spawn(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] default_copilot_run: gh exits nonzero with stderr 'unknown option --disallow-temp-dir' -> CliMutationOutcome(status='error') naming the update fix; FakeAgentRunner.calls has length 1 and no allow-all flag."""
        invocation = _real_invocation(tmp_path)
        runner = FakeAgentRunner().returning(returncode=1, stderr=b"error: unknown option '--disallow-temp-dir'")
        monkeypatch.setattr("dovo.core.agents.copilot.run_isolated_process", runner)

        outcome = default_copilot_run(
            CliMutationRunRequest(
                worktree_path=_worktree(tmp_path),
                prompt="hi",
                timeout_seconds=3,
                tools=default_tool_policy(),
                invocation=invocation,
            )
        )

        assert outcome == CliMutationOutcome(
            status="error",
            error_detail="Installed GitHub Copilot CLI lacks a required tool-policy control. "
            "Fix: run `copilot update` or reinstall the GitHub Copilot CLI.",
        )
        assert len(runner.calls) == 1
        assert not [part for part in runner.last_call.cmd if part.startswith("--allow-all")]

    def test_unrepresentable_policy_run_returns_error_outcome_without_spawn(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] default_copilot_run: a policy with no grants returns the unsupported error outcome and never spawns gh."""
        runner = FakeAgentRunner().returning(stdout=_OK_STREAM)
        monkeypatch.setattr("dovo.core.agents.copilot.run_isolated_process", runner)

        outcome = default_copilot_run(
            CliMutationRunRequest(
                worktree_path=_worktree(tmp_path),
                prompt="hi",
                timeout_seconds=3,
                tools=ToolPolicy(),
                invocation=_real_invocation(tmp_path),
            )
        )

        assert outcome == CliMutationOutcome(status="error", error_detail=tool_policy_unsupported_message("copilot"))
        assert runner.calls == []

    def test_copilot_descriptor_declares_policy_support_and_home_control(self) -> None:
        """[tier-1/unit] COPILOT_PROVIDER_SPEC: supports_tool_policy is True and 'COPILOT_HOME' is in control_envs (updates tests/core/agents/test_registry.py)."""
        assert COPILOT_PROVIDER_SPEC.supports_tool_policy is True
        assert "COPILOT_HOME" in COPILOT_PROVIDER_SPEC.control_envs
