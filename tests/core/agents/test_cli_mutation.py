"""Tests for the shared direct-mutation agent adapter base."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import pytest

from tests.harness import AgentRequestBuilder, new_file_diff
from worktree.core.agents import AgentRequest, AgentResponseStatus
from worktree.core.agents.cli_mutation import (
    CliDirectMutationAdapter,
    CliMutationOutcome,
    CliMutationRunFn,
    CliMutationRunRequest,
    CliMutationRunStatus,
    build_mutation_prompt,
    validate_request_patch,
)
from worktree.core.agents.mutation_git import MutationGitError
from worktree.core.patch import PatchApplyStatus


def _fake_run(
    *,
    edits: dict[str, str] | None = None,
    status: CliMutationRunStatus = "finished",
    error_detail: str | None = None,
    result_text: str | None = "done",
) -> CliMutationRunFn:
    def _run(request: CliMutationRunRequest) -> CliMutationOutcome:
        if edits:
            for rel, content in edits.items():
                path = request.sandbox_path / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
        return CliMutationOutcome(status=status, result_text=result_text, error_detail=error_detail)

    return _run


class UnitTestAdapter(CliDirectMutationAdapter):
    """Minimal concrete adapter exercising only the shared base's propose_fix flow."""

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
        """[tier-1/unit] build_mutation_prompt: fix_failure/review_remediation returns the repair header plus an exact JSON body with mode, sandbox_path, instruction, payload."""
        built = AgentRequestBuilder().with_sandbox_path(git_repo).build()
        request = built.model_copy(update={"mode": mode})
        assert request.payload is not None
        expected_body = {
            "mode": mode,
            "sandbox_path": str(git_repo),
            "instruction": "Fix the failing test.",
            "payload": request.payload.model_dump(mode="json"),
        }
        expected = (
            "You are a coding agent running directly in this sandbox checkout. "
            "Fix the failure described below.\n"
            "- Make the smallest change that fixes the failure.\n"
            "- Stay inside this working directory; do not push, open a PR, or "
            "touch remotes.\n"
            "- Prefer leaving tests green.\n"
            "- Do not modify files under .worktree/.\n"
            "- When finished, leave the working tree containing only the fix.\n\n"
        ) + json.dumps(expected_body, indent=2, ensure_ascii=False)

        assert build_mutation_prompt(request) == expected

    def test_direct_mode_prompt_carries_instruction_without_repair_directive_or_payload(self, git_repo: Path) -> None:
        """[tier-1/unit] build_mutation_prompt: a direct request returns the direct header plus a JSON body with exactly mode, sandbox_path, instruction; no 'Fix the failure' text and no payload key."""
        request = AgentRequest(mode="direct", instruction="Plan the change", sandbox_path=git_repo, timeout_seconds=10)
        expected_body = {"mode": "direct", "sandbox_path": str(git_repo), "instruction": "Plan the change"}
        expected = (
            "You are a coding agent running directly in this sandbox checkout.\n"
            "- Carry out the instruction below.\n"
            "- If it asks for planning or review, report your findings in your final message and leave the working tree unchanged.\n"
            "- Stay inside this working directory; do not push, open a PR, or touch remotes.\n"
            "- Do not modify files under .worktree/.\n\n"
        ) + json.dumps(expected_body, indent=2, ensure_ascii=False)

        prompt = build_mutation_prompt(request)

        assert prompt == expected
        assert "Fix the failure" not in prompt


class ValidateRequestPatchTests:
    def test_absent_bounds_use_defaults_and_accept_single_file_diff(self, git_repo: Path) -> None:
        """[tier-1/unit] validate_request_patch: a request with max_files/max_patch_kb/reject_binary_changes all None and a one-file diff returns PatchApplyStatus.CHECKED_OK with that file in touched_files."""
        request = AgentRequest(mode="direct", instruction="go", sandbox_path=git_repo, timeout_seconds=10)

        result = validate_request_patch(request, new_file_diff("a.txt"))

        assert result.status == PatchApplyStatus.CHECKED_OK
        assert result.touched_files == ["a.txt"]

    def test_explicit_bound_rejects_diff_over_limit(self, git_repo: Path) -> None:
        """[tier-1/unit] validate_request_patch: max_files=1 and a two-file diff returns PatchApplyStatus.TOO_MANY_FILES."""
        request = AgentRequest(mode="direct", instruction="go", sandbox_path=git_repo, timeout_seconds=10, max_files=1)
        diff = new_file_diff("a.txt") + new_file_diff("b.txt")

        result = validate_request_patch(request, diff)

        assert result.status == PatchApplyStatus.TOO_MANY_FILES


class SharedMutationAdapterTests:
    def test_proposed_patch(self, git_repo: Path) -> None:
        """A finished run whose diff clears the patch gate returns PROPOSED_PATCH."""
        adapter = UnitTestAdapter(run_fn=_fake_run(edits={"a.txt": "fixed\n"}))

        resp = adapter.propose_fix(AgentRequestBuilder().with_sandbox_path(git_repo).build())

        assert resp.status == AgentResponseStatus.PROPOSED_PATCH
        assert resp.unified_diff is not None and "fixed" in resp.unified_diff
        assert resp.raw_text == "done"
        assert resp.mutation_baseline_ref is not None
        assert (git_repo / "a.txt").read_text(encoding="utf-8") == "fixed\n"

    def test_no_op_when_no_edits(self, git_repo: Path) -> None:
        """A finished run with an empty diff (no edits) returns NO_OP."""
        adapter = UnitTestAdapter(run_fn=_fake_run())

        resp = adapter.propose_fix(AgentRequestBuilder().with_sandbox_path(git_repo).build())

        assert resp.status == AgentResponseStatus.NO_OP
        assert resp.raw_text == "done"
        assert resp.mutation_baseline_ref is not None
        assert resp.errors == []

    def test_timeout_labels_provider_name(self, git_repo: Path) -> None:
        """A timed-out run returns TIMEOUT with the concrete provider name in the fix hint."""
        adapter = UnitTestAdapter(run_fn=_fake_run(status="timeout"))

        resp = adapter.propose_fix(AgentRequestBuilder().with_sandbox_path(git_repo).build())

        assert resp.status == AgentResponseStatus.TIMEOUT
        assert resp.raw_text == "done"
        assert resp.mutation_baseline_ref is not None
        assert resp.errors == [
            "Agent timed out after 10s (provider=unit-test).\nFix:\n- raise agent.timeout_seconds on the blueprint"
        ]

    def test_provider_error(self, git_repo: Path) -> None:
        """A run that errors returns PROVIDER_ERROR carrying the runner's error detail."""
        adapter = UnitTestAdapter(run_fn=_fake_run(status="error", error_detail="boom"))

        resp = adapter.propose_fix(AgentRequestBuilder().with_sandbox_path(git_repo).build())

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert resp.raw_text == "done"
        assert resp.mutation_baseline_ref is not None
        assert resp.errors == ["Agent provider error (AGENT_PROVIDER_ERROR): boom"]

    def test_gate_violation_discards_edits(self, git_repo: Path) -> None:
        """Edits touching more files than max_files are discarded back to baseline."""
        adapter = UnitTestAdapter(run_fn=_fake_run(edits={"README.md": "edit one\n", "b.txt": "edit two\n"}))

        resp = adapter.propose_fix(AgentRequestBuilder().with_sandbox_path(git_repo).with_max_files(1).build())

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert resp.raw_text == "done"
        assert resp.mutation_baseline_ref is not None
        assert resp.errors == ["Patch touches 2 files; max_files is 1."]
        assert (git_repo / "README.md").read_text(encoding="utf-8") == "# Test Repo\n"
        assert not (git_repo / "b.txt").exists()

    def test_gate_violation_discard_git_error_appends_to_errors(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A discard() failure after a gate violation appends its detail onto gate errors."""
        adapter = UnitTestAdapter(run_fn=_fake_run(edits={"a.txt": "1\n", "b.txt": "2\n"}))

        def _fail_discard(*a: object, **k: object) -> None:
            raise MutationGitError("git reset failed: index locked")

        monkeypatch.setattr("worktree.core.agents.cli_mutation.discard_since", _fail_discard)

        resp = adapter.propose_fix(AgentRequestBuilder().with_sandbox_path(git_repo).with_max_files(1).build())

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert resp.raw_text == "done"
        assert resp.mutation_baseline_ref is not None
        assert resp.errors == [
            "Patch touches 2 files; max_files is 1.",
            "Agent provider error (AGENT_PROVIDER_ERROR): "
            "failed to discard rejected sandbox edit: git reset failed: index locked",
        ]

    def test_gate_violation_preserves_wip(self, git_repo: Path) -> None:
        """Discard restores pre-existing uncommitted WIP, not the last committed tip."""
        (git_repo / "a.txt").write_text("wip content\n", encoding="utf-8")
        adapter = UnitTestAdapter(run_fn=_fake_run(edits={"a.txt": "edit 1\n", "b.txt": "edit 2\n"}))

        resp = adapter.propose_fix(AgentRequestBuilder().with_sandbox_path(git_repo).with_max_files(1).build())

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert resp.raw_text == "done"
        assert resp.mutation_baseline_ref is not None
        assert resp.errors == ["Patch touches 2 files; max_files is 1."]
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

        resp = adapter.propose_fix(AgentRequestBuilder().with_sandbox_path(git_repo).build())

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert resp.errors == ["Agent provider error (AGENT_PROVIDER_ERROR): preflight failed"]
        assert not run_function_called
