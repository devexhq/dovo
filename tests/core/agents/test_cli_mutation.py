"""Tests for the shared direct-mutation agent adapter base."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Literal

import pytest

from dovo.core.agents import (
    AgentInvocationContext,
    AgentProgress,
    AgentRequest,
    AgentResponseStatus,
    default_tool_policy,
)
from dovo.core.agents.base import ProviderSpec
from dovo.core.agents.cli_mutation import (
    CliDirectMutationAdapter,
    CliMutationOutcome,
    CliMutationRunFn,
    CliMutationRunRequest,
    CliMutationRunStatus,
    build_mutation_prompt,
    validate_request_patch,
)
from dovo.core.agents.environment import ENV_FILTERED_PROMPT_LINE
from dovo.core.agents.models import AgentDenial
from dovo.core.agents.mutation_git import (
    MutationGitError,
    capture_diff_since,
    discard_since,
    resolve_pre_agent_baseline,
)
from dovo.core.git import PatchApplyResult, PatchApplyStatus
from tests.harness import AgentRequestBuilder, new_file_diff


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
    """Adapter double declaring credential_envs ("TEST_CREDENTIAL_PRIMARY","TEST_CREDENTIAL_FALLBACK") with an additive provider-specific preflight."""

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
        """[tier-1/unit] build_mutation_prompt: fix_failure/review_remediation returns the repair header plus an exact JSON body with mode, worktree_path, instruction, payload."""
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
        """[tier-1/unit] build_mutation_prompt: a direct request returns the direct header plus a JSON body with exactly mode, worktree_path, instruction; no 'Fix the failure' text and no payload key."""
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
        """[tier-1/unit] build_mutation_prompt: a direct request with agent_scratch_path P returns the direct header + the scratch lines + a JSON body with keys mode, worktree_path, agent_scratch_path (str(P)), instruction, in that order."""
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
            "- Do not read or write logs, artifacts, or session directories.\n\n"
        ) + json.dumps(expected_body, indent=2, ensure_ascii=False)

        prompt = build_mutation_prompt(_request_with_invocation(git_repo, invocation))

        assert prompt == expected
        assert list(json.loads(prompt[prompt.index("{") :])) == list(expected_body)

    def test_prompt_never_contains_control_path_or_invocation_id(self, git_repo: Path, tmp_path: Path) -> None:
        """[tier-1/unit] build_mutation_prompt: with a full AgentInvocationContext on the request, the prompt text contains neither str(control_path) nor invocation_id."""
        invocation = _invocation(tmp_path)

        prompt = build_mutation_prompt(_request_with_invocation(git_repo, invocation))

        assert str(invocation.control_path) not in prompt
        assert invocation.invocation_id not in prompt


class CliMutationInvocationPropagationTests:
    def test_invoke_forwards_invocation_to_the_runner_request(self, git_repo: Path, tmp_path: Path) -> None:
        """[tier-1/unit] CliDirectMutationAdapter._invoke: the CliMutationRunRequest passed to _default_run has invocation == the AgentRequest's invocation."""
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
        """[tier-1/unit] CliDirectMutationAdapter._invoke: the CliMutationRunRequest passed to _default_run has on_line equal to the AgentRequest's on_line, and None when the request has none."""

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
        """[tier-1/unit] CliDirectMutationAdapter._invoke: the CliMutationRunRequest passed to _default_run has on_progress equal to the AgentRequest's on_progress, and None when the request has none."""

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


class ScratchDiffIsolationTests:
    def test_scratch_file_never_appears_in_the_captured_diff(self, git_repo: Path, tmp_path: Path) -> None:
        """[tier-1/integration] CliDirectMutationAdapter.invoke: a real git_repo adapter writing 'notes.py' into scratch_path and 'src/a.py' into the worktree returns a PROPOSED_PATCH whose unified_diff names only src/a.py and whose capture_diff_since(baseline) paths == ['src/a.py']."""
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
        """[tier-1/integration] CliDirectMutationAdapter.invoke: a file written under control_path leaves 'git status --porcelain' in the worktree empty."""
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
        """[tier-1/integration] discard_since: after discarding worktree edits to the baseline, files under scratch_path and control_path still exist with unchanged contents."""
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
        """[tier-1/unit] validate_request_patch: a request with max_files/max_patch_kb/reject_binary_changes all None and a diff of 31 files, over 1 MiB, with a binary marker returns CHECKED_OK with all 32 files touched."""
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
        """[tier-1/unit] validate_request_patch: an explicit max_patch_kb=1 with a 2 KiB diff returns TOO_LARGE, and reject_binary_changes=True with a binary marker returns BINARY_REJECTED."""
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
        """[tier-1/unit] validate_request_patch: max_files=1 and a two-file diff returns TOO_MANY_FILES with fixes == ['Set max_files on the agent step.']."""
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
        """A finished run whose diff clears the patch gate returns PROPOSED_PATCH."""
        adapter = UnitTestAdapter(run_fn=_fake_run(edits={"a.txt": "fixed\n"}))

        resp = adapter.invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert resp.status == AgentResponseStatus.PROPOSED_PATCH
        assert resp.unified_diff is not None and "fixed" in resp.unified_diff
        assert resp.raw_text == "done"
        assert resp.mutation_baseline_ref is not None
        assert (git_repo / "a.txt").read_text(encoding="utf-8") == "fixed\n"

    def test_no_op_when_no_edits(self, git_repo: Path) -> None:
        """A finished run with an empty diff (no edits) returns NO_OP."""
        adapter = UnitTestAdapter(run_fn=_fake_run())

        resp = adapter.invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert resp.status == AgentResponseStatus.NO_OP
        assert resp.unified_diff is None
        assert resp.raw_text == "done"
        assert resp.mutation_baseline_ref is not None
        assert resp.errors == []

    def test_timeout_labels_provider_name(self, git_repo: Path) -> None:
        """A timed-out run returns TIMEOUT with the concrete provider name in the fix hint."""
        adapter = UnitTestAdapter(run_fn=_fake_run(status="timeout"))

        resp = adapter.invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert resp.status == AgentResponseStatus.TIMEOUT
        assert resp.raw_text == "done"
        assert resp.mutation_baseline_ref is not None
        assert resp.errors == ["Agent timed out after 10s (provider=unit-test)."]
        assert resp.fixes == ["Raise timeout_seconds on the agent step"]

    def test_provider_error(self, git_repo: Path) -> None:
        """A run that errors returns PROVIDER_ERROR carrying the runner's error detail."""
        adapter = UnitTestAdapter(run_fn=_fake_run(status="error", error_detail="boom"))

        resp = adapter.invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert resp.raw_text == "done"
        assert resp.mutation_baseline_ref is not None
        assert resp.errors == ["Agent provider error (AGENT_PROVIDER_ERROR): boom"]

    def test_gate_violation_discards_edits(self, git_repo: Path, tmp_path: Path) -> None:
        """Edits touching more files than max_files are discarded back to baseline."""
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
        """A discard() failure after a gate violation leads the errors, followed by the gate errors."""
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
        """[tier-1/integration] CliDirectMutationAdapter.invoke: MutationGitError("git broke") raised by the patched dovo.core.agents.cli_mutation function returns PROVIDER_ERROR with errors == [expected_error]."""

        def _fail_git(*a: object, **k: object) -> None:
            raise MutationGitError("git broke")

        monkeypatch.setattr(f"dovo.core.agents.cli_mutation.{patched_name}", _fail_git)
        adapter = UnitTestAdapter(run_fn=_fake_run())

        resp = adapter.invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert resp.errors == [expected_error]

    def test_gate_violation_preserves_wip(self, git_repo: Path, tmp_path: Path) -> None:
        """Discard restores pre-existing uncommitted WIP, not the last committed tip."""
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
        """A failing _preflight short-circuits before baseline resolution or the runner call."""
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
        """[tier-1/unit] CliDirectMutationAdapter.invoke: a runner outcome with denials yields BLOCKED only for an empty diff; otherwise PROPOSED_PATCH or NO_OP as before."""
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
        """[tier-1/unit] CliDirectMutationAdapter.invoke: a declared-credential double whose _preflight returns "extra" never replaces the credential error, and surfaces only when a credential is usable; the run function is never called in either case."""
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
        """[tier-1/unit] CliDirectMutationAdapter.invoke: with credentials missing and resolve_pre_agent_baseline patched, the baseline function is never called."""
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
        """[tier-1/integration] CliDirectMutationAdapter.invoke: host-only names make the runner prompt start with ENV_FILTERED_PROMPT_LINE + '\\n' and response.env_withheld equal the sorted names."""
        monkeypatch.setattr(os, "environ", {"PATH": os.environ["PATH"], "ZED_HOST": "1", "FOO_HOST": "2"})
        received: list[CliMutationRunRequest] = []

        def _record(request: CliMutationRunRequest) -> CliMutationOutcome:
            received.append(request)
            return CliMutationOutcome(status="finished")

        response = EnvironmentAdapter(run_fn=_record).invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert response.env_withheld == ["FOO_HOST", "ZED_HOST"]
        assert received[0].prompt.startswith(ENV_FILTERED_PROMPT_LINE + "\n")
        assert received[0].env["DOVO_ENV_WITHHELD"] == "FOO_HOST,ZED_HOST"

    def test_nothing_withheld_leaves_prompt_and_response_unchanged(self, git_repo: Path) -> None:
        """[tier-1/integration] CliDirectMutationAdapter.invoke: an env with no withheld names yields a prompt starting with the normal header and response.env_withheld == []."""
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
        """[tier-1/integration] CliDirectMutationAdapter.invoke: a double with no descriptor gets env == {} and env_withheld == []."""
        received: list[CliMutationRunRequest] = []

        def _record(request: CliMutationRunRequest) -> CliMutationOutcome:
            received.append(request)
            return CliMutationOutcome(status="finished")

        response = UnitTestAdapter(run_fn=_record).invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert (received[0].env, response.env_withheld) == ({}, [])

    def test_run_request_dump_excludes_env(self, tmp_path: Path) -> None:
        """[tier-1/unit] CliMutationRunRequest.model_dump: key 'env' is absent."""
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
        """[tier-1/integration] CliDirectMutationAdapter.invoke: max_files=1 with a tracked edit, an untracked file and a binary file saves destination, resets the worktree to baseline, and 'git apply' of the patch recreates all three."""
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
        """[tier-1/unit] CliDirectMutationAdapter.invoke: validate_request_patch patched to return each non-CHECKED_OK status yields PROVIDER_ERROR, response.rejected_patch_path == str(destination), destination exists, and the agent's file is gone from the worktree."""
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
        """[tier-1/integration] CliDirectMutationAdapter.invoke: a 2-file edit with max_files=1 returns errors == ['Patch touches 2 files; max_files is 1.', f'Discarded edit saved to {destination}.']."""
        destination = tmp_path / "rejected" / "code_attempt_1.patch"

        response = UnitTestAdapter(run_fn=_fake_run(edits={"a.txt": "1\n", "b.txt": "2\n"})).invoke(
            _gated_request(git_repo, destination)
        )

        assert response.errors == ["Patch touches 2 files; max_files is 1.", f"Discarded edit saved to {destination}."]


class RejectedPatchCaptureFailureTests:
    def test_unwritable_destination_keeps_edits_and_reports_could_not_save(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        """[tier-1/integration] CliDirectMutationAdapter.invoke: a destination under a regular file with max_files=1 and a 2-file edit returns PROVIDER_ERROR, both files still contain the agent's text, rejected_patch_path is None, and errors == ['Patch touches 2 files; max_files is 1.', 'Could not save the rejected edit (<reason>); the worktree was left unchanged.']."""
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
        """[tier-1/integration] CliDirectMutationAdapter.invoke: an AgentRequest with rejected_patch_destination None and a gate rejection returns errors ending 'Could not save the rejected edit (no session storage directory is available); the worktree was left unchanged.' and the edited files remain."""
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
        """[tier-1/integration] CliDirectMutationAdapter.invoke: an edit writing 'API_KEY=sk-test-123' rejected by max_files=1 leaves that exact text in the saved patch, and the response's errors, raw_text and rejected_patch_path do not contain it."""
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
        """[tier-1/unit] CliDirectMutationAdapter.invoke: save_rejected_patch patched to return None yields errors == ['Patch touches 2 files; max_files is 1.'], rejected_patch_path None, and the worktree reset to baseline."""
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
        """[tier-1/integration] CliDirectMutationAdapter.invoke: discard_since patched to raise MutationGitError('git reset failed: index locked') returns errors == ['Agent provider error (AGENT_PROVIDER_ERROR): Failed to discard rejected worktree edit: git reset failed: index locked', 'Patch touches 2 files; max_files is 1.', f'Rejected edit saved to {destination}.'] and rejected_patch_path == str(destination)."""

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
