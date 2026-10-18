"""Tests for the shared direct-mutation agent adapter base."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import pytest

from dovo.core.agents import (
    AgentInvocationContext,
    AgentProgress,
    AgentProgressKind,
    AgentRequest,
    AgentResponseStatus,
    default_tool_policy,
)
from dovo.core.agents.base import ProgressMapper, ProviderSpec
from dovo.core.agents.cli_mutation import (
    CliDirectMutationAdapter,
    CliMutationOutcome,
    CliMutationRunFn,
    CliMutationRunRequest,
    CliMutationRunStatus,
    build_mutation_prompt,
    decode_output,
    forward_line,
    forward_stdout,
    run_cli_process,
    unknown_option_detail,
    validate_request_patch,
)
from dovo.core.agents.environment import env_filtered_prompt_line
from dovo.core.agents.models import AgentDenial
from dovo.core.agents.mutation_git import (
    MutationGitError,
    capture_diff_since,
    discard_since,
    resolve_pre_agent_baseline,
)
from dovo.core.git import PatchApplyResult, PatchApplyStatus
from tests.harness import (
    AgentRequestBuilder,
    FakeAgentRunner,
    FakeAgentRunnerCall,
    StreamingAgentRunner,
    new_file_diff,
)


def _fake_run(
    *,
    edits: dict[str, str] | None = None,
    status: CliMutationRunStatus = "finished",
    error_detail: str | None = None,
    result_text: str | None = "done",
    denials: list[AgentDenial] | None = None,
) -> CliMutationRunFn:
    def _run(request: CliMutationRunRequest) -> CliMutationOutcome:
        if edits:
            for rel, content in edits.items():
                path = request.worktree_path / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
        return CliMutationOutcome(
            status=status, result_text=result_text, error_detail=error_detail, denials=denials or []
        )

    return _run


def _invocation(tmp_path: Path) -> AgentInvocationContext:
    """Create real scratch and control directories outside the repository."""
    root = tmp_path / "invocation"
    scratch, control = root / "scratch", root / "control"
    scratch.mkdir(parents=True)
    control.mkdir()

    return AgentInvocationContext(invocation_id="a" * 32, scratch_path=scratch, control_path=control)


def _request_with_invocation(git_repo: Path, invocation: AgentInvocationContext) -> AgentRequest:
    return AgentRequest(
        mode="direct",
        instruction="Plan the change",
        worktree_path=git_repo,
        timeout_seconds=10,
        tools=default_tool_policy(),
        agent_scratch_path=invocation.scratch_path,
        invocation=invocation,
    )


def _gated_request(git_repo: Path, destination: Path | None) -> AgentRequest:
    """Build a request whose max_files=1 gate rejects any multi-file edit, saving it to ``destination``."""
    builder = AgentRequestBuilder().with_worktree_path(git_repo).with_max_files(1)
    if destination is not None:
        builder = builder.with_rejected_patch_destination(destination)

    return builder.build()


def _porcelain(repo: Path) -> str:
    return subprocess.run(["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True).stdout


class UnitTestAdapter(CliDirectMutationAdapter):
    """Minimal concrete adapter exercising only the shared base's invoke flow."""

    def __init__(self, run_fn: CliMutationRunFn) -> None:
        self._run_fn = run_fn

    def _provider_name(self) -> str:
        return "unit-test"

    def _default_run(self, request: CliMutationRunRequest) -> CliMutationOutcome:
        return self._run_fn(request)


class PreflightAdapter(UnitTestAdapter):
    """Adapter double whose preflight always fails, to prove it blocks before baseline."""

    def _preflight(self, request: AgentRequest) -> str | None:
        return "preflight failed"


class CredentialedAdapter(UnitTestAdapter):
    """Adapter double declaring both credential envs with an additive provider-specific preflight."""

    def _provider_spec(self) -> ProviderSpec:
        return ProviderSpec(
            token="unit-test",
            credential_envs=("TEST_CREDENTIAL_PRIMARY", "TEST_CREDENTIAL_FALLBACK"),
            requires_model=False,
            supports_tool_policy=False,
            supports_os_sandbox=False,
            build=lambda: self,
        )

    def _preflight(self, request: AgentRequest) -> str | None:
        return "extra"


class EnvironmentAdapter(UnitTestAdapter):
    """Adapter double declaring a credential-free descriptor so the shared base builds its subprocess environment."""

    def _provider_spec(self) -> ProviderSpec:
        return ProviderSpec(
            token="unit-test",
            credential_envs=(),
            requires_model=False,
            supports_tool_policy=False,
            supports_os_sandbox=False,
            build=lambda: self,
        )


class MappedAdapter(UnitTestAdapter):
    """Adapter double whose credential-free descriptor declares the given progress mapper."""

    def __init__(self, progress_mapper: ProgressMapper, run_fn: CliMutationRunFn) -> None:
        super().__init__(run_fn=run_fn)
        self._progress_mapper = progress_mapper

    def _provider_spec(self) -> ProviderSpec:
        return ProviderSpec(
            token="unit-test",
            credential_envs=(),
            requires_model=False,
            supports_tool_policy=False,
            supports_os_sandbox=False,
            build=lambda: self,
            progress_mapper=self._progress_mapper,
        )


class BuildMutationPromptTests:
    @pytest.mark.parametrize(
        "mode",
        [
            pytest.param("fix_failure", id="fix-failure"),
            pytest.param("review_remediation", id="review-remediation"),
        ],
    )
    def test_remediation_mode_prompt_carries_repair_header_instruction_and_payload(
        self, git_repo: Path, mode: Literal["fix_failure", "review_remediation"]
    ) -> None:
        built = AgentRequestBuilder().with_worktree_path(git_repo).build()
        request = built.model_copy(update={"mode": mode})
        assert request.payload is not None
        expected_body = {
            "mode": mode,
            "worktree_path": str(git_repo),
            "instruction": "Fix the failing test.",
            "payload": request.payload.model_dump(mode="json"),
        }
        expected = (
            "You are a coding agent running directly in this worktree checkout. "
            "Fix the failure described below.\n"
            "- Make the smallest change that fixes the failure.\n"
            "- Stay inside this working directory; do not push, open a PR, or "
            "touch remotes.\n"
            "- Prefer leaving tests green.\n"
            "- Do not modify files under .dovo/.\n"
            "- When finished, leave the working tree containing only the fix.\n\n"
        ) + json.dumps(expected_body, indent=2, ensure_ascii=False)

        assert build_mutation_prompt(request) == expected

    def test_direct_mode_prompt_carries_instruction_without_repair_directive_or_payload(self, git_repo: Path) -> None:
        request = AgentRequest(
            mode="direct",
            instruction="Plan the change",
            worktree_path=git_repo,
            timeout_seconds=10,
            tools=default_tool_policy(),
        )
        expected_body = {"mode": "direct", "worktree_path": str(git_repo), "instruction": "Plan the change"}
        expected = (
            "You are a coding agent running directly in this worktree checkout.\n"
            "- Carry out the instruction below.\n"
            "- If it asks for planning or review, report your findings in your final message and leave the working tree unchanged.\n"
            "- Stay inside this working directory; do not push, open a PR, or touch remotes.\n"
            "- Do not modify files under .dovo/.\n\n"
        ) + json.dumps(expected_body, indent=2, ensure_ascii=False)

        prompt = build_mutation_prompt(request)

        assert prompt == expected
        assert "Fix the failure" not in prompt


class ScratchPromptTests:
    def test_prompt_with_scratch_adds_lines_and_exact_path_field(self, git_repo: Path, tmp_path: Path) -> None:
        invocation = _invocation(tmp_path)
        expected_body = {
            "mode": "direct",
            "worktree_path": str(git_repo),
            "agent_scratch_path": str(invocation.scratch_path),
            "instruction": "Plan the change",
        }
        expected = (
            "You are a coding agent running directly in this worktree checkout.\n"
            "- Carry out the instruction below.\n"
            "- If it asks for planning or review, report your findings in your final message and leave the working tree unchanged.\n"
            "- Stay inside this working directory; do not push, open a PR, or touch remotes.\n"
            "- Do not modify files under .dovo/.\n\n"
            "- Put temporary files only in the directory named by agent_scratch_path; make source edits in this checkout.\n"
            "- Do not read or write logs, artifacts, or session directories.\n"
            "- If you cannot complete the instruction, write the reason, in 100 words or less, "
            f"to {invocation.scratch_path}/unfixable.txt and stop.\n\n"
        ) + json.dumps(expected_body, indent=2, ensure_ascii=False)

        prompt = build_mutation_prompt(_request_with_invocation(git_repo, invocation))

        assert prompt == expected
        assert list(json.loads(prompt[prompt.index("{") :])) == list(expected_body)

    def test_prompt_never_contains_control_path_or_invocation_id(self, git_repo: Path, tmp_path: Path) -> None:
        invocation = _invocation(tmp_path)

        prompt = build_mutation_prompt(_request_with_invocation(git_repo, invocation))

        assert str(invocation.control_path) not in prompt
        assert invocation.invocation_id not in prompt


class CliMutationInvocationPropagationTests:
    def test_invoke_forwards_invocation_to_the_runner_request(self, git_repo: Path, tmp_path: Path) -> None:
        invocation = _invocation(tmp_path)
        received: list[CliMutationRunRequest] = []

        def _record(request: CliMutationRunRequest) -> CliMutationOutcome:
            received.append(request)
            return CliMutationOutcome(status="finished")

        UnitTestAdapter(run_fn=_record).invoke(_request_with_invocation(git_repo, invocation))

        assert [request.invocation for request in received] == [invocation]


class CliMutationOnLinePropagationTests:
    @pytest.mark.parametrize("with_callback", [pytest.param(True, id="with-callback"), pytest.param(False, id="none")])
    def test_invoke_forwards_on_line_to_the_runner_request(self, git_repo: Path, with_callback: bool) -> None:

        def on_line(stream_name: str, line: str) -> None:
            """Line callback used only as a distinguishable callable value."""

        received: list[CliMutationRunRequest] = []

        def _record(request: CliMutationRunRequest) -> CliMutationOutcome:
            received.append(request)
            return CliMutationOutcome(status="finished")

        builder = AgentRequestBuilder().with_worktree_path(git_repo)
        agent_request = builder.build().model_copy(update={"on_line": on_line if with_callback else None})

        UnitTestAdapter(run_fn=_record).invoke(agent_request)

        assert [request.on_line for request in received] == [on_line if with_callback else None]

    @pytest.mark.parametrize("with_callback", [pytest.param(True, id="with-callback"), pytest.param(False, id="none")])
    def test_invoke_forwards_on_progress_to_the_runner_request(self, git_repo: Path, with_callback: bool) -> None:

        def on_progress(progress: AgentProgress) -> None:
            """Progress callback used only as a distinguishable callable value."""

        received: list[CliMutationRunRequest] = []

        def _record(request: CliMutationRunRequest) -> CliMutationOutcome:
            received.append(request)
            return CliMutationOutcome(status="finished")

        builder = AgentRequestBuilder().with_worktree_path(git_repo)
        agent_request = builder.build().model_copy(update={"on_progress": on_progress if with_callback else None})

        UnitTestAdapter(run_fn=_record).invoke(agent_request)

        assert [request.on_progress for request in received] == [on_progress if with_callback else None]


class CliMutationProgressMapperPropagationTests:
    @pytest.mark.parametrize("with_descriptor", [pytest.param(True, id="descriptor"), pytest.param(False, id="none")])
    def test_descriptor_progress_mapper_reaches_the_run_request(self, git_repo: Path, with_descriptor: bool) -> None:
        mapper = _mapped("a")
        received: list[CliMutationRunRequest] = []

        def _record(request: CliMutationRunRequest) -> CliMutationOutcome:
            received.append(request)
            return CliMutationOutcome(status="finished")

        adapter = MappedAdapter(mapper, run_fn=_record) if with_descriptor else UnitTestAdapter(run_fn=_record)

        adapter.invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert [request.progress_mapper for request in received] == [mapper if with_descriptor else None]


class ScratchDiffIsolationTests:
    def test_scratch_file_never_appears_in_the_captured_diff(self, git_repo: Path, tmp_path: Path) -> None:
        invocation = _invocation(tmp_path)
        baseline = resolve_pre_agent_baseline(git_repo)

        def _write(request: CliMutationRunRequest) -> CliMutationOutcome:
            assert request.invocation is not None
            (request.invocation.scratch_path / "notes.py").write_text("tmp\n", encoding="utf-8")
            (request.worktree_path / "src").mkdir()
            (request.worktree_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
            return CliMutationOutcome(status="finished")

        response = UnitTestAdapter(run_fn=_write).invoke(_request_with_invocation(git_repo, invocation))

        assert response.status == AgentResponseStatus.PROPOSED_PATCH
        assert response.unified_diff is not None
        assert "src/a.py" in response.unified_diff
        assert "notes.py" not in response.unified_diff
        assert capture_diff_since(git_repo, baseline)[1] == ["src/a.py"]

    def test_control_directory_content_never_appears_in_worktree_status(self, git_repo: Path, tmp_path: Path) -> None:
        invocation = _invocation(tmp_path)

        def _write(request: CliMutationRunRequest) -> CliMutationOutcome:
            assert request.invocation is not None
            (request.invocation.control_path / "settings.json").write_text("{}\n", encoding="utf-8")
            return CliMutationOutcome(status="finished")

        response = UnitTestAdapter(run_fn=_write).invoke(_request_with_invocation(git_repo, invocation))

        assert response.status == AgentResponseStatus.NO_OP
        assert (invocation.control_path / "settings.json").is_file()
        assert _porcelain(git_repo) == ""

    def test_scratch_survives_discard_since(self, git_repo: Path, tmp_path: Path) -> None:
        invocation = _invocation(tmp_path)
        baseline = resolve_pre_agent_baseline(git_repo)
        (invocation.scratch_path / "notes.txt").write_text("scratch", encoding="utf-8")
        (invocation.control_path / "settings.json").write_text("control", encoding="utf-8")
        (git_repo / "edit.txt").write_text("edit\n", encoding="utf-8")

        discard_since(git_repo, baseline)

        assert not (git_repo / "edit.txt").exists()
        assert (invocation.scratch_path / "notes.txt").read_text(encoding="utf-8") == "scratch"
        assert (invocation.control_path / "settings.json").read_text(encoding="utf-8") == "control"


class ValidateRequestPatchTests:
    def test_absent_bounds_apply_no_limits_to_a_large_binary_multi_file_diff(self, git_repo: Path) -> None:
        request = AgentRequest(
            mode="direct", instruction="go", worktree_path=git_repo, timeout_seconds=10, tools=default_tool_policy()
        )
        diff = (
            "".join(new_file_diff(f"f{index}.txt") for index in range(31))
            + new_file_diff("big.txt", "x" * (1024 * 1024 + 1))
            + "diff --git a/logo.png b/logo.png\nBinary files a/logo.png and b/logo.png differ\n"
        )

        result = validate_request_patch(request, diff)

        assert result.status == PatchApplyStatus.CHECKED_OK
        assert len(result.touched_files) == 33

    @pytest.mark.parametrize(
        ("max_patch_kb", "reject_binary_changes", "diff", "status"),
        [
            pytest.param(1, None, new_file_diff("a.txt", "x" * 2048), PatchApplyStatus.TOO_LARGE, id="size"),
            pytest.param(
                None,
                True,
                "diff --git a/logo.png b/logo.png\nBinary files a/logo.png and b/logo.png differ\n",
                PatchApplyStatus.BINARY_REJECTED,
                id="binary",
            ),
        ],
    )
    def test_explicit_bound_rejects_matching_diff(
        self,
        git_repo: Path,
        max_patch_kb: int | None,
        reject_binary_changes: bool | None,
        diff: str,
        status: PatchApplyStatus,
    ) -> None:
        request = AgentRequest(
            mode="direct",
            instruction="go",
            worktree_path=git_repo,
            timeout_seconds=10,
            max_patch_kb=max_patch_kb,
            reject_binary_changes=reject_binary_changes,
            tools=default_tool_policy(),
        )

        result = validate_request_patch(request, diff)

        assert result.status == status

    def test_explicit_step_bound_rejects_diff_over_limit_with_agent_step_fix(self, git_repo: Path) -> None:
        request = AgentRequest(
            mode="direct",
            instruction="go",
            worktree_path=git_repo,
            timeout_seconds=10,
            max_files=1,
            tools=default_tool_policy(),
        )
        diff = new_file_diff("a.txt") + new_file_diff("b.txt")

        result = validate_request_patch(request, diff)

        assert result.status == PatchApplyStatus.TOO_MANY_FILES
        assert result.fixes == ["Set max_files on the agent step."]


class SharedMutationAdapterTests:
    def test_proposed_patch(self, git_repo: Path) -> None:
        adapter = UnitTestAdapter(run_fn=_fake_run(edits={"a.txt": "fixed\n"}))

        resp = adapter.invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert resp.status == AgentResponseStatus.PROPOSED_PATCH
        assert resp.unified_diff is not None and "fixed" in resp.unified_diff
        assert resp.raw_text == "done"
        assert resp.mutation_baseline_ref is not None
        assert (git_repo / "a.txt").read_text(encoding="utf-8") == "fixed\n"

    def test_no_op_when_no_edits(self, git_repo: Path) -> None:
        adapter = UnitTestAdapter(run_fn=_fake_run())

        resp = adapter.invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert resp.status == AgentResponseStatus.NO_OP
        assert resp.unified_diff is None
        assert resp.raw_text == "done"
        assert resp.mutation_baseline_ref is not None
        assert resp.errors == []

    def test_timeout_labels_provider_name(self, git_repo: Path) -> None:
        adapter = UnitTestAdapter(run_fn=_fake_run(status="timeout"))

        resp = adapter.invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert resp.status == AgentResponseStatus.TIMEOUT
        assert resp.raw_text == "done"
        assert resp.mutation_baseline_ref is not None
        assert resp.errors == ["Agent timed out after 10s (provider=unit-test)."]
        assert resp.fixes == ["Raise timeout_seconds on the agent step"]

    def test_provider_error(self, git_repo: Path) -> None:
        adapter = UnitTestAdapter(run_fn=_fake_run(status="error", error_detail="boom"))

        resp = adapter.invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert resp.raw_text == "done"
        assert resp.mutation_baseline_ref is not None
        assert resp.errors == ["Agent provider error (AGENT_PROVIDER_ERROR): boom"]

    def test_gate_violation_discards_edits(self, git_repo: Path, tmp_path: Path) -> None:
        adapter = UnitTestAdapter(run_fn=_fake_run(edits={"README.md": "edit one\n", "b.txt": "edit two\n"}))
        destination = tmp_path / "rejected" / "code_attempt_1.patch"

        resp = adapter.invoke(_gated_request(git_repo, destination))

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert resp.raw_text == "done"
        assert resp.mutation_baseline_ref is not None
        assert resp.errors == ["Patch touches 2 files; max_files is 1.", f"Discarded edit saved to {destination}."]
        assert (git_repo / "README.md").read_text(encoding="utf-8") == "# Test Repo\n"
        assert not (git_repo / "b.txt").exists()

    def test_gate_violation_discard_git_error_precedes_gate_errors(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = UnitTestAdapter(run_fn=_fake_run(edits={"a.txt": "1\n", "b.txt": "2\n"}))

        def _fail_discard(*a: object, **k: object) -> None:
            raise MutationGitError("git reset failed: index locked")

        monkeypatch.setattr("dovo.core.agents.cli_mutation.discard_since", _fail_discard)

        destination = tmp_path / "rejected" / "code_attempt_1.patch"

        resp = adapter.invoke(_gated_request(git_repo, destination))

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert resp.raw_text == "done"
        assert resp.mutation_baseline_ref is not None
        assert resp.errors == [
            "Agent provider error (AGENT_PROVIDER_ERROR): "
            "Failed to discard rejected worktree edit: git reset failed: index locked",
            "Patch touches 2 files; max_files is 1.",
            f"Rejected edit saved to {destination}.",
        ]

    @pytest.mark.parametrize(
        ("patched_name", "expected_error"),
        [
            pytest.param(
                "resolve_pre_agent_baseline",
                "Agent provider error (AGENT_PROVIDER_ERROR): Failed to resolve worktree baseline: git broke",
                id="baseline",
            ),
            pytest.param(
                "capture_diff_since",
                "Agent provider error (AGENT_PROVIDER_ERROR): Failed to capture worktree diff: git broke",
                id="capture-diff",
            ),
        ],
    )
    def test_git_failure_before_proposal_returns_provider_error(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch, patched_name: str, expected_error: str
    ) -> None:

        def _fail_git(*a: object, **k: object) -> None:
            raise MutationGitError("git broke")

        monkeypatch.setattr(f"dovo.core.agents.cli_mutation.{patched_name}", _fail_git)
        adapter = UnitTestAdapter(run_fn=_fake_run())

        resp = adapter.invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert resp.errors == [expected_error]

    def test_gate_violation_preserves_wip(self, git_repo: Path, tmp_path: Path) -> None:
        (git_repo / "a.txt").write_text("wip content\n", encoding="utf-8")
        adapter = UnitTestAdapter(run_fn=_fake_run(edits={"a.txt": "edit 1\n", "b.txt": "edit 2\n"}))

        destination = tmp_path / "rejected" / "code_attempt_1.patch"

        resp = adapter.invoke(_gated_request(git_repo, destination))

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert resp.raw_text == "done"
        assert resp.mutation_baseline_ref is not None
        assert resp.errors == ["Patch touches 2 files; max_files is 1.", f"Discarded edit saved to {destination}."]
        assert (git_repo / "a.txt").read_text(encoding="utf-8") == "wip content\n"
        assert not (git_repo / "b.txt").exists()

    def test_preflight_blocks_before_baseline(self, git_repo: Path) -> None:
        run_function_called = False

        def run_fn(request: CliMutationRunRequest) -> CliMutationOutcome:
            nonlocal run_function_called
            run_function_called = True
            return CliMutationOutcome(status="finished", result_text="nope")

        adapter = PreflightAdapter(run_fn=run_fn)

        resp = adapter.invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert resp.errors == ["Agent provider error (AGENT_PROVIDER_ERROR): preflight failed"]
        assert not run_function_called


class BlockedClassificationTests:
    @pytest.mark.parametrize(
        ("denials", "writes_file", "expected", "expected_errors"),
        [
            pytest.param(
                [AgentDenial(tool="bash", message="denied")],
                False,
                AgentResponseStatus.BLOCKED,
                ["Agent run was blocked by tool permissions (AGENT_PERMISSION_BLOCKED): bash: denied"],
                id="denial-and-empty-diff",
            ),
            pytest.param(
                [AgentDenial(tool="bash", message="denied")],
                True,
                AgentResponseStatus.PROPOSED_PATCH,
                [],
                id="denial-with-diff",
            ),
            pytest.param([], False, AgentResponseStatus.NO_OP, [], id="no-denial-empty-diff"),
        ],
    )
    def test_denials_block_only_when_the_worktree_diff_is_empty(
        self,
        git_repo: Path,
        denials: list[AgentDenial],
        writes_file: bool,
        expected: AgentResponseStatus,
        expected_errors: list[str],
    ) -> None:
        adapter = UnitTestAdapter(
            run_fn=_fake_run(edits={"a.txt": "fixed\n"} if writes_file else None, denials=denials)
        )

        resp = adapter.invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert resp.status == expected
        assert resp.errors == expected_errors


class PreflightOrderingTests:
    @pytest.mark.parametrize(
        ("credential_set", "expected_errors"),
        [
            pytest.param(
                False,
                [
                    "Agent provider error (AGENT_PROVIDER_ERROR): "
                    "missing TEST_CREDENTIAL_PRIMARY or TEST_CREDENTIAL_FALLBACK. "
                    "Fix: export TEST_CREDENTIAL_PRIMARY=... or export TEST_CREDENTIAL_FALLBACK=..."
                ],
                id="credential-check-wins-over-override",
            ),
            pytest.param(
                True, ["Agent provider error (AGENT_PROVIDER_ERROR): extra"], id="override-runs-after-credential-passes"
            ),
        ],
    )
    def test_preflight_override_is_additive_to_credential_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, credential_set: bool, expected_errors: list[str]
    ) -> None:
        for name in ("TEST_CREDENTIAL_PRIMARY", "TEST_CREDENTIAL_FALLBACK"):
            monkeypatch.delenv(name, raising=False)
        if credential_set:
            monkeypatch.setenv("TEST_CREDENTIAL_PRIMARY", "k")
        run_calls: list[CliMutationRunRequest] = []

        def run_fn(request: CliMutationRunRequest) -> CliMutationOutcome:
            run_calls.append(request)
            return CliMutationOutcome(status="finished", result_text="nope")

        adapter = CredentialedAdapter(run_fn=run_fn)

        resp = adapter.invoke(AgentRequestBuilder().with_worktree_path(tmp_path).build())

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert resp.errors == expected_errors
        assert run_calls == []

    def test_missing_credential_skips_baseline_resolution(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in ("TEST_CREDENTIAL_PRIMARY", "TEST_CREDENTIAL_FALLBACK"):
            monkeypatch.delenv(name, raising=False)
        baseline_calls: list[Path] = []
        monkeypatch.setattr(
            "dovo.core.agents.cli_mutation.resolve_pre_agent_baseline",
            lambda worktree_path: baseline_calls.append(worktree_path) or "ref",
        )
        adapter = CredentialedAdapter(run_fn=_fake_run())

        resp = adapter.invoke(AgentRequestBuilder().with_worktree_path(tmp_path).build())

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert baseline_calls == []


class MutationEnvReportTests:
    def test_withheld_names_prefix_the_prompt_and_return_on_the_response(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(os, "environ", {"PATH": os.environ["PATH"], "ZED_HOST": "1", "FOO_HOST": "2"})
        received: list[CliMutationRunRequest] = []

        def _record(request: CliMutationRunRequest) -> CliMutationOutcome:
            received.append(request)
            return CliMutationOutcome(status="finished")

        response = EnvironmentAdapter(run_fn=_record).invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert response.env_withheld == ["FOO_HOST", "ZED_HOST"]
        assert received[0].prompt.startswith(env_filtered_prompt_line("unit-test") + "\n")
        assert received[0].env["DOVO_ENV_WITHHELD"] == "FOO_HOST,ZED_HOST"

    def test_withheld_names_reach_the_callback_before_the_runner_starts(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(os, "environ", {"PATH": os.environ["PATH"], "ZED_HOST": "1", "FOO_HOST": "2"})
        events: list[object] = []

        def _record(request: CliMutationRunRequest) -> CliMutationOutcome:
            events.append("run")
            return CliMutationOutcome(status="finished")

        request = AgentRequestBuilder().with_worktree_path(git_repo).build()
        request = request.model_copy(update={"on_env_withheld": events.append})

        EnvironmentAdapter(run_fn=_record).invoke(request)

        assert events == [["FOO_HOST", "ZED_HOST"], "run"]

    def test_failing_withheld_callback_does_not_stop_the_run(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(os, "environ", {"PATH": os.environ["PATH"], "ZED_HOST": "1"})

        def _explode(names: list[str]) -> None:
            raise RuntimeError(names)

        request = AgentRequestBuilder().with_worktree_path(git_repo).build()
        request = request.model_copy(update={"on_env_withheld": _explode})

        response = EnvironmentAdapter(run_fn=lambda _request: CliMutationOutcome(status="finished")).invoke(request)

        assert response.env_withheld == ["ZED_HOST"]

    def test_nothing_withheld_leaves_prompt_and_response_unchanged(self, git_repo: Path) -> None:
        received: list[CliMutationRunRequest] = []

        def _record(request: CliMutationRunRequest) -> CliMutationOutcome:
            received.append(request)
            return CliMutationOutcome(status="finished")

        request = AgentRequestBuilder().with_worktree_path(git_repo).with_env_mode("inherit").build()
        response = EnvironmentAdapter(run_fn=_record).invoke(request)

        assert response.env_withheld == []
        assert received[0].prompt.startswith("You are a coding agent")

    def test_adapter_without_descriptor_receives_an_empty_env_and_reports_nothing_withheld(
        self, git_repo: Path
    ) -> None:
        received: list[CliMutationRunRequest] = []

        def _record(request: CliMutationRunRequest) -> CliMutationOutcome:
            received.append(request)
            return CliMutationOutcome(status="finished")

        response = UnitTestAdapter(run_fn=_record).invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert (received[0].env, response.env_withheld) == ({}, [])

    def test_run_request_dump_excludes_env(self, tmp_path: Path) -> None:
        request = CliMutationRunRequest(
            worktree_path=tmp_path,
            prompt="p",
            timeout_seconds=1,
            tools=default_tool_policy(),
            env={"A_KEY": "secret1"},
        )

        assert "env" not in request.model_dump()


class RejectedPatchSaveTests:
    def test_too_many_files_rejection_leaves_patch_that_applies_to_baseline(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        """Save the patch, reset to baseline and make git apply recreate the tracked, untracked and binary edits."""
        expected = {"README.md": b"# Test Repo\nagent edit\n", "new.txt": b"fresh\n", "image.png": bytes(range(256))}

        def _write(request: CliMutationRunRequest) -> CliMutationOutcome:
            for name, content in expected.items():
                (request.worktree_path / name).write_bytes(content)
            return CliMutationOutcome(status="finished", result_text="done")

        destination = tmp_path / "rejected" / "code_attempt_1.patch"

        response = UnitTestAdapter(run_fn=_write).invoke(_gated_request(git_repo, destination))

        assert response.rejected_patch_path == str(destination)
        assert _porcelain(git_repo) == ""
        subprocess.run(["git", "apply", str(destination)], cwd=git_repo, check=True)
        assert {name: (git_repo / name).read_bytes() for name in expected} == expected

    @pytest.mark.parametrize(
        "status",
        [
            pytest.param(PatchApplyStatus.TOO_LARGE, id="too-large"),
            pytest.param(PatchApplyStatus.TOO_MANY_FILES, id="too-many-files"),
            pytest.param(PatchApplyStatus.BINARY_REJECTED, id="binary-rejected"),
            pytest.param(PatchApplyStatus.UNSAFE_PATH, id="unsafe-path"),
            pytest.param(PatchApplyStatus.INVALID_DIFF, id="invalid-diff"),
            pytest.param(PatchApplyStatus.WORKTREE_MISSING, id="worktree-missing"),
        ],
    )
    def test_every_gate_status_saves_then_discards(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: PatchApplyStatus
    ) -> None:
        monkeypatch.setattr(
            "dovo.core.agents.cli_mutation.validate_request_patch",
            lambda request, diff: PatchApplyResult(status=status, errors=["gate said no"]),
        )
        destination = tmp_path / "rejected" / "code_attempt_1.patch"

        response = UnitTestAdapter(run_fn=_fake_run(edits={"agent.txt": "agent\n"})).invoke(
            AgentRequestBuilder().with_worktree_path(git_repo).with_rejected_patch_destination(destination).build()
        )

        assert response.status == AgentResponseStatus.PROVIDER_ERROR
        assert response.rejected_patch_path == str(destination)
        assert "agent.txt" in destination.read_text(encoding="utf-8")
        assert not (git_repo / "agent.txt").exists()

    def test_saved_rejection_errors_are_gate_error_then_discarded_line(self, git_repo: Path, tmp_path: Path) -> None:
        destination = tmp_path / "rejected" / "code_attempt_1.patch"

        response = UnitTestAdapter(run_fn=_fake_run(edits={"a.txt": "1\n", "b.txt": "2\n"})).invoke(
            _gated_request(git_repo, destination)
        )

        assert response.errors == ["Patch touches 2 files; max_files is 1.", f"Discarded edit saved to {destination}."]


class RejectedPatchCaptureFailureTests:
    def test_unwritable_destination_keeps_edits_and_reports_could_not_save(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        blocker = tmp_path / "blocker"
        blocker.write_text("file", encoding="utf-8")
        destination = blocker / "code_attempt_1.patch"

        response = UnitTestAdapter(run_fn=_fake_run(edits={"a.txt": "1\n", "b.txt": "2\n"})).invoke(
            _gated_request(git_repo, destination)
        )

        assert response.status == AgentResponseStatus.PROVIDER_ERROR
        assert response.rejected_patch_path is None
        assert response.errors[0] == "Patch touches 2 files; max_files is 1."
        assert len(response.errors) == 2
        assert response.errors[1].startswith(f"Could not save the rejected edit (cannot write {destination}: ")
        assert response.errors[1].endswith("); the worktree was left unchanged.")
        assert (git_repo / "a.txt").read_text(encoding="utf-8") == "1\n"
        assert (git_repo / "b.txt").read_text(encoding="utf-8") == "2\n"

    def test_missing_destination_reports_no_session_storage_and_keeps_edits(self, git_repo: Path) -> None:
        response = UnitTestAdapter(run_fn=_fake_run(edits={"a.txt": "1\n", "b.txt": "2\n"})).invoke(
            _gated_request(git_repo, None)
        )

        assert response.errors[-1] == (
            "Could not save the rejected edit (no session storage directory is available); "
            "the worktree was left unchanged."
        )
        assert (git_repo / "a.txt").exists()
        assert (git_repo / "b.txt").exists()


class RejectedPatchContentTests:
    def test_saved_patch_keeps_secret_looking_text_unredacted_and_response_never_contains_it(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        destination = tmp_path / "rejected" / "code_attempt_1.patch"

        response = UnitTestAdapter(run_fn=_fake_run(edits={"a.txt": "API_KEY=sk-test-123\n", "b.txt": "2\n"})).invoke(
            _gated_request(git_repo, destination)
        )

        assert "API_KEY=sk-test-123" in destination.read_text(encoding="utf-8")
        surfaced = [*response.errors, response.raw_text, response.rejected_patch_path]
        assert not any("sk-test-123" in (item or "") for item in surfaced)


class RejectedPatchDiscardOrderTests:
    def test_empty_capture_discards_without_a_patch_or_path(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("dovo.core.agents.cli_mutation.save_rejected_patch", lambda *args, **kwargs: None)
        destination = tmp_path / "rejected" / "code_attempt_1.patch"

        response = UnitTestAdapter(run_fn=_fake_run(edits={"a.txt": "1\n", "b.txt": "2\n"})).invoke(
            _gated_request(git_repo, destination)
        )

        assert response.errors == ["Patch touches 2 files; max_files is 1."]
        assert response.rejected_patch_path is None
        assert _porcelain(git_repo) == ""
        assert not destination.exists()

    def test_discard_failure_after_save_reports_rejected_line_and_keeps_path(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:

        def _fail_discard(*args: object, **kwargs: object) -> None:
            raise MutationGitError("git reset failed: index locked")

        monkeypatch.setattr("dovo.core.agents.cli_mutation.discard_since", _fail_discard)
        destination = tmp_path / "rejected" / "code_attempt_1.patch"

        response = UnitTestAdapter(run_fn=_fake_run(edits={"a.txt": "1\n", "b.txt": "2\n"})).invoke(
            _gated_request(git_repo, destination)
        )

        assert response.errors == [
            "Agent provider error (AGENT_PROVIDER_ERROR): "
            "Failed to discard rejected worktree edit: git reset failed: index locked",
            "Patch touches 2 files; max_files is 1.",
            f"Rejected edit saved to {destination}.",
        ]
        assert response.rejected_patch_path == str(destination)


def _unfixable_request(git_repo: Path, tmp_path: Path, destination: Path | None) -> AgentRequest:
    """Build a request with a real scratch directory, saving a discarded edit to ``destination`` when given."""
    builder = AgentRequestBuilder().with_worktree_path(git_repo).with_invocation(_invocation(tmp_path))
    if destination is not None:
        builder = builder.with_rejected_patch_destination(destination)

    return builder.build()


def _reporting_run(
    content: str | None,
    *,
    edits: dict[str, str] | None = None,
    status: CliMutationRunStatus = "finished",
    denials: list[AgentDenial] | None = None,
) -> CliMutationRunFn:
    """Return a run that writes ``content`` to <scratch>/unfixable.txt (unless None), then behaves like ``_fake_run``."""
    inner = _fake_run(edits=edits, status=status, error_detail="runner failed", denials=denials)

    def _run(request: CliMutationRunRequest) -> CliMutationOutcome:
        if content is not None:
            assert request.invocation is not None
            (request.invocation.scratch_path / "unfixable.txt").write_text(content, encoding="utf-8")
        return inner(request)

    return _run


class UnfixableClassificationTests:
    @pytest.mark.parametrize(
        "denials",
        [
            pytest.param([], id="no-denials"),
            pytest.param([AgentDenial(tool="bash", message="denied")], id="with-denials"),
        ],
    )
    def test_unfixable_file_without_edits_returns_unfixable_with_stripped_reason(
        self, git_repo: Path, tmp_path: Path, denials: list[AgentDenial]
    ) -> None:
        response = UnitTestAdapter(run_fn=_reporting_run("\n  no SSH key \n", denials=denials)).invoke(
            _unfixable_request(git_repo, tmp_path, None)
        )

        assert response.status == AgentResponseStatus.UNFIXABLE
        assert response.unfixable_reason == "no SSH key"
        assert response.errors == []
        assert response.rejected_patch_path is None
        assert _porcelain(git_repo) == ""

    def test_unfixable_file_with_edits_saves_patch_and_restores_baseline(self, git_repo: Path, tmp_path: Path) -> None:
        destination = tmp_path / "rejected" / "code_attempt_1.patch"

        response = UnitTestAdapter(run_fn=_reporting_run("no SSH key", edits={"a.txt": "edit\n"})).invoke(
            _unfixable_request(git_repo, tmp_path, destination)
        )

        assert response.status == AgentResponseStatus.UNFIXABLE
        assert response.unfixable_reason == "no SSH key"
        assert response.errors == [f"Discarded edit saved to {destination}."]
        assert response.rejected_patch_path == str(destination)
        assert "a.txt" in destination.read_text(encoding="utf-8")
        assert _porcelain(git_repo) == ""

    def test_unfixable_save_failure_keeps_edits_and_stays_unfixable(self, git_repo: Path, tmp_path: Path) -> None:
        response = UnitTestAdapter(run_fn=_reporting_run("no SSH key", edits={"a.txt": "edit\n"})).invoke(
            _unfixable_request(git_repo, tmp_path, None)
        )

        assert response.status == AgentResponseStatus.UNFIXABLE
        assert response.unfixable_reason == "no SSH key"
        assert response.errors == [
            "Could not save the rejected edit (no session storage directory is available); "
            "the worktree was left unchanged."
        ]
        assert response.rejected_patch_path is None
        assert (git_repo / "a.txt").read_text(encoding="utf-8") == "edit\n"

    def test_unfixable_discard_failure_reports_rejected_line_and_keeps_path(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:

        def _fail_discard(*args: object, **kwargs: object) -> None:
            raise MutationGitError("git reset failed: index locked")

        monkeypatch.setattr("dovo.core.agents.cli_mutation.discard_since", _fail_discard)
        destination = tmp_path / "rejected" / "code_attempt_1.patch"

        response = UnitTestAdapter(run_fn=_reporting_run("no SSH key", edits={"a.txt": "edit\n"})).invoke(
            _unfixable_request(git_repo, tmp_path, destination)
        )

        assert response.status == AgentResponseStatus.UNFIXABLE
        assert response.errors == [
            "Failed to discard rejected worktree edit: git reset failed: index locked",
            f"Rejected edit saved to {destination}.",
        ]
        assert response.rejected_patch_path == str(destination)

    @pytest.mark.parametrize(
        ("content", "edits", "denials", "expected"),
        [
            pytest.param(None, None, [], AgentResponseStatus.NO_OP, id="missing-no-op"),
            pytest.param(None, {"a.txt": "edit\n"}, [], AgentResponseStatus.PROPOSED_PATCH, id="missing-proposed"),
            pytest.param(
                None,
                None,
                [AgentDenial(tool="bash", message="denied")],
                AgentResponseStatus.BLOCKED,
                id="missing-blocked",
            ),
            pytest.param("  \n\t", None, [], AgentResponseStatus.NO_OP, id="blank-no-op"),
            pytest.param("  \n\t", {"a.txt": "edit\n"}, [], AgentResponseStatus.PROPOSED_PATCH, id="blank-proposed"),
            pytest.param(
                "  \n\t",
                None,
                [AgentDenial(tool="bash", message="denied")],
                AgentResponseStatus.BLOCKED,
                id="blank-blocked",
            ),
        ],
    )
    def test_missing_or_blank_unfixable_file_keeps_the_original_status(
        self,
        git_repo: Path,
        tmp_path: Path,
        content: str | None,
        edits: dict[str, str] | None,
        denials: list[AgentDenial],
        expected: AgentResponseStatus,
    ) -> None:
        response = UnitTestAdapter(run_fn=_reporting_run(content, edits=edits, denials=denials)).invoke(
            _unfixable_request(git_repo, tmp_path, None)
        )

        assert response.status == expected
        assert response.unfixable_reason is None

    @pytest.mark.parametrize(
        ("kind", "reason"),
        [
            pytest.param("directory", "Is a directory", id="directory"),
            pytest.param("symlink", "it is a symlink", id="symlink"),
            pytest.param("dangling-symlink", "it is a symlink", id="dangling-symlink"),
            pytest.param("fifo", "it is not a regular file", id="fifo"),
        ],
    )
    def test_unreadable_unfixable_file_returns_provider_error_naming_the_file(
        self, git_repo: Path, tmp_path: Path, kind: str, reason: str
    ) -> None:
        secret = tmp_path / "secret.txt"
        secret.write_text("TOP-SECRET-VALUE\n", encoding="utf-8")

        def _write(request: CliMutationRunRequest) -> CliMutationOutcome:
            assert request.invocation is not None
            report = request.invocation.scratch_path / "unfixable.txt"
            if kind == "directory":
                report.mkdir()
            elif kind == "fifo":
                os.mkfifo(report)
            else:
                report.symlink_to(secret if kind == "symlink" else tmp_path / "missing.txt")
            return CliMutationOutcome(status="finished", result_text="done")

        request = _unfixable_request(git_repo, tmp_path, None)

        response = UnitTestAdapter(run_fn=_write).invoke(request)

        assert response.status == AgentResponseStatus.PROVIDER_ERROR
        assert response.errors == [
            f"Agent provider error (AGENT_PROVIDER_ERROR): Failed to read {request.agent_scratch_path}/unfixable.txt: "
            f"{reason}"
        ]
        assert response.unfixable_reason is None
        assert "TOP-SECRET-VALUE" not in response.model_dump_json()

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            pytest.param("timeout", AgentResponseStatus.TIMEOUT, id="timeout"),
            pytest.param("error", AgentResponseStatus.PROVIDER_ERROR, id="error"),
        ],
    )
    def test_timeout_or_runner_error_ignores_the_unfixable_file(
        self, git_repo: Path, tmp_path: Path, status: CliMutationRunStatus, expected: AgentResponseStatus
    ) -> None:
        response = UnitTestAdapter(run_fn=_reporting_run("no SSH key", status=status)).invoke(
            _unfixable_request(git_repo, tmp_path, None)
        )

        assert response.status == expected
        assert response.unfixable_reason is None


def _request(
    worktree_path: Path,
    *,
    on_line: Callable[[str, str], None] | None = None,
    on_progress: Callable[[AgentProgress], None] | None = None,
    progress_mapper: ProgressMapper | None = None,
) -> CliMutationRunRequest:
    return CliMutationRunRequest(
        worktree_path=worktree_path,
        prompt="hi",
        timeout_seconds=3,
        tools=default_tool_policy(),
        on_line=on_line,
        on_progress=on_progress,
        progress_mapper=progress_mapper,
    )


def _mapped(*targets: str) -> ProgressMapper:
    def mapper(line: str, *, worktree_path: Path) -> tuple[AgentProgress, ...]:
        return tuple(AgentProgress(kind=AgentProgressKind.READ, target=target) for target in targets)

    return mapper


class ForwardLineTests:
    @pytest.mark.parametrize("stream_name", [pytest.param("stdout", id="stdout"), pytest.param("stderr", id="stderr")])
    def test_callback_tags_each_line_with_stream_name(self, stream_name: str) -> None:
        received: list[tuple[str, str]] = []
        callback = forward_line(stream_name, lambda name, line: received.append((name, line)))
        assert callback is not None

        callback("a")
        callback("b")

        assert received == [(stream_name, "a"), (stream_name, "b")]

    def test_none_on_line_returns_none(self) -> None:
        assert forward_line("stdout", None) is None


class ForwardStdoutTests:
    def test_neither_callback_set_returns_none(self, tmp_path: Path) -> None:
        request = _request(tmp_path)

        assert forward_stdout(request) is None

    def test_on_line_only_tags_stdout_and_never_calls_mapper(self, tmp_path: Path) -> None:
        received: list[tuple[str, str]] = []
        mapper_calls: list[str] = []

        def mapper(line: str, *, worktree_path: Path) -> tuple[AgentProgress, ...]:
            mapper_calls.append(line)
            return ()

        request = _request(
            tmp_path,
            on_line=lambda name, line: received.append((name, line)),
            progress_mapper=mapper,
        )
        callback = forward_stdout(request)
        assert callback is not None

        callback("o1")

        assert received == [("stdout", "o1")]
        assert mapper_calls == []

    def test_on_progress_only_delivers_mapped_items_in_order(self, tmp_path: Path) -> None:
        progress: list[AgentProgress] = []
        mapper_calls: list[tuple[str, Path]] = []

        def mapper(line: str, *, worktree_path: Path) -> tuple[AgentProgress, ...]:
            mapper_calls.append((line, worktree_path))
            return _mapped("a", "b")(line, worktree_path=worktree_path)

        request = _request(tmp_path, on_progress=progress.append, progress_mapper=mapper)
        callback = forward_stdout(request)
        assert callback is not None

        callback("line")

        assert progress == [
            AgentProgress(kind=AgentProgressKind.READ, target="a"),
            AgentProgress(kind=AgentProgressKind.READ, target="b"),
        ]
        assert mapper_calls == [("line", tmp_path)]

    def test_none_progress_mapper_delivers_lines_without_progress(self, tmp_path: Path) -> None:
        received: list[tuple[str, str]] = []
        progress: list[AgentProgress] = []
        request = _request(
            tmp_path,
            on_line=lambda name, line: received.append((name, line)),
            on_progress=progress.append,
        )
        callback = forward_stdout(request)
        assert callback is not None

        callback("line")

        assert received == [("stdout", "line")]
        assert progress == []

    def test_both_callbacks_deliver_raw_line_and_progress(self, tmp_path: Path) -> None:
        received: list[tuple[str, str]] = []
        progress: list[AgentProgress] = []
        request = _request(
            tmp_path,
            on_line=lambda name, line: received.append((name, line)),
            on_progress=progress.append,
            progress_mapper=_mapped("a"),
        )
        callback = forward_stdout(request)
        assert callback is not None

        callback("line")

        assert received == [("stdout", "line")]
        assert progress == [AgentProgress(kind=AgentProgressKind.READ, target="a")]

    @pytest.mark.parametrize(
        "failing",
        [pytest.param("mapper", id="mapper-raises"), pytest.param("on_progress", id="on-progress-raises")],
    )
    def test_callback_failure_is_dropped_and_raw_line_still_delivered(self, tmp_path: Path, failing: str) -> None:
        received: list[tuple[str, str]] = []

        def raising_mapper(line: str, *, worktree_path: Path) -> tuple[AgentProgress, ...]:
            raise RuntimeError("bad line")

        def raising_progress(progress: AgentProgress) -> None:
            raise RuntimeError("terminal gone")

        request = _request(
            tmp_path,
            on_line=lambda name, line: received.append((name, line)),
            on_progress=raising_progress,
            progress_mapper=raising_mapper if failing == "mapper" else _mapped("a"),
        )
        callback = forward_stdout(request)
        assert callback is not None

        callback("line")

        assert received == [("stdout", "line")]


class RunCliProcessTests:
    def test_completed_process_is_returned_with_request_derived_spawn_arguments(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeAgentRunner().returning(stdout=b"out")
        monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)
        request = _request(tmp_path)

        completed = run_cli_process(
            ["tool", "--x"], request, env={"K": "V"}, binary="tool", install_hint="install tool"
        )

        assert isinstance(completed, subprocess.CompletedProcess)
        assert completed.stdout == b"out"
        assert runner.last_call == FakeAgentRunnerCall(
            cmd=["tool", "--x"], cwd=tmp_path, env={"K": "V"}, input_data=b"hi", timeout_seconds=3
        )

    def test_stream_callbacks_reach_the_runner(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        runner = StreamingAgentRunner(stdout_lines=["o1"], stderr_lines=["e1"], completed_stdout=b"")
        monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)
        received: list[tuple[str, str]] = []
        progress: list[AgentProgress] = []
        request = _request(
            tmp_path,
            on_line=lambda name, line: received.append((name, line)),
            on_progress=progress.append,
            progress_mapper=_mapped("a"),
        )

        run_cli_process(["tool"], request, env={}, binary="tool", install_hint="x")

        assert received == [("stdout", "o1"), ("stderr", "e1")]
        assert progress == [AgentProgress(kind=AgentProgressKind.READ, target="a")]

    def test_request_without_callbacks_passes_no_stream_callbacks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = StreamingAgentRunner(stdout_lines=[], stderr_lines=[], completed_stdout=b"")
        monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)

        run_cli_process(["tool"], _request(tmp_path), env={}, binary="tool", install_hint="x")

        assert (runner.received_stdout_callback, runner.received_stderr_callback) == (None, None)

    @pytest.mark.parametrize(
        ("raised", "expected"),
        [
            pytest.param(
                subprocess.TimeoutExpired(cmd="tool", timeout=3),
                CliMutationOutcome(status="timeout"),
                id="timeout-expired",
            ),
            pytest.param(
                FileNotFoundError("tool"),
                CliMutationOutcome(
                    status="error", error_detail="tool is not installed or not on PATH: tool. Fix: install tool"
                ),
                id="missing-binary",
            ),
            pytest.param(
                OSError("exec failed"), CliMutationOutcome(status="error", error_detail="exec failed"), id="os-error"
            ),
        ],
    )
    def test_spawn_failure_returns_terminal_outcome(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raised: Exception, expected: CliMutationOutcome
    ) -> None:
        monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", FakeAgentRunner().raising(raised))
        request = _request(tmp_path)

        outcome = run_cli_process(["tool"], request, env={}, binary="tool", install_hint="install tool")

        assert outcome == expected


class DecodeOutputTests:
    def test_valid_bytes_decode_to_text(self) -> None:
        completed = subprocess.CompletedProcess(args=["x"], returncode=0, stdout=b"out", stderr=b"err")

        assert decode_output(completed) == ("out", "err")

    @pytest.mark.parametrize(
        ("stdout", "stderr", "expected"),
        [
            pytest.param(b"a\xffb", b"", ("a\ufffdb", ""), id="invalid-utf8-replaced"),
            pytest.param(None, None, ("", ""), id="none-streams-empty"),
        ],
    )
    def test_undecodable_or_missing_streams_never_raise(
        self, stdout: bytes | None, stderr: bytes | None, expected: tuple[str, str]
    ) -> None:
        completed = subprocess.CompletedProcess(args=["x"], returncode=0, stdout=stdout, stderr=stderr)

        assert decode_output(completed) == expected


class UnknownOptionDetailTests:
    @pytest.mark.parametrize(
        "stderr_text",
        [
            pytest.param("error: unknown option '--x'", id="lowercase"),
            pytest.param("Error: Unknown Option --x", id="mixed-case"),
        ],
    )
    def test_unknown_option_stderr_returns_adapter_detail(self, stderr_text: str) -> None:
        assert unknown_option_detail(stderr_text, "update it") == "update it"

    def test_unrelated_stderr_returns_none(self) -> None:
        assert unknown_option_detail("some other failure", "update it") is None
