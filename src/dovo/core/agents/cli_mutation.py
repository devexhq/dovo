"""Shared direct-mutation agent adapter base and DTOs."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from dovo.core.agents.base import BaseAgentProvider, elapsed_ms
from dovo.core.agents.models import AgentRequest, AgentResponse, AgentResponseStatus
from dovo.core.agents.mutation_git import (
    MutationGitError,
    capture_diff_since,
    discard_since,
    resolve_pre_agent_baseline,
)
from dovo.core.agents.responses import no_op_response, provider_error_response, timeout_response
from dovo.core.git import PatchApplyResult, PatchApplyStatus, validate_patch_text

CliMutationRunStatus = Literal["finished", "timeout", "error"]

DEFAULT_MAX_FILES = 30
DEFAULT_MAX_PATCH_KB = 1024
DEFAULT_REJECT_BINARY_CHANGES = True


class CliMutationRunRequest(BaseModel):
    """Normalized inputs for invoking a direct-mutation CLI/SDK runner."""

    model_config = {"extra": "forbid", "strict": True}

    worktree_path: Path
    prompt: str
    model: str | None = None
    timeout_seconds: float


class CliMutationOutcome(BaseModel):
    """Normalized result from a direct-mutation CLI/SDK runner."""

    model_config = {"extra": "forbid", "strict": True}

    status: CliMutationRunStatus
    result_text: str | None = None
    error_detail: str | None = None


CliMutationRunFn = Callable[[CliMutationRunRequest], CliMutationOutcome]


_DIRECT_PROMPT_HEADER = (
    "You are a coding agent running directly in this worktree checkout.\n"
    "- Carry out the instruction below.\n"
    "- If it asks for planning or review, report your findings in your final message and leave the working tree unchanged.\n"
    "- Stay inside this working directory; do not push, open a PR, or touch remotes.\n"
    "- Do not modify files under .dovo/.\n\n"
)
_REMEDIATION_PROMPT_HEADER = (
    "You are a coding agent running directly in this worktree checkout. "
    "Fix the failure described below.\n"
    "- Make the smallest change that fixes the failure.\n"
    "- Stay inside this working directory; do not push, open a PR, or "
    "touch remotes.\n"
    "- Prefer leaving tests green.\n"
    "- Do not modify files under .dovo/.\n"
    "- When finished, leave the working tree containing only the fix.\n\n"
)


def build_mutation_prompt(request: AgentRequest) -> str:
    """Build the agent prompt: direct mode carries the authored instruction only; remediation modes add the failure payload."""
    header = _DIRECT_PROMPT_HEADER if request.mode == "direct" else _REMEDIATION_PROMPT_HEADER
    return header + json.dumps(_prompt_body(request), indent=2, ensure_ascii=False)


def _prompt_body(request: AgentRequest) -> dict[str, object]:
    """Return the JSON body: mode, worktree_path, instruction, plus payload only when the request carries one."""
    body: dict[str, object] = {
        "mode": request.mode,
        "worktree_path": str(request.worktree_path),
        "instruction": request.instruction,
    }
    if request.payload is not None:
        body["payload"] = request.payload.model_dump(mode="json")
    return body


def validate_request_patch(request: AgentRequest, diff: str) -> PatchApplyResult:
    """Run the shared patch gate on ``diff`` with the request's bounds, falling back to the DEFAULT_* limits."""
    return validate_patch_text(
        diff,
        max_files=request.max_files or DEFAULT_MAX_FILES,
        max_patch_kb=request.max_patch_kb or DEFAULT_MAX_PATCH_KB,
        reject_binary_changes=(
            request.reject_binary_changes
            if request.reject_binary_changes is not None
            else DEFAULT_REJECT_BINARY_CHANGES
        ),
        worktree_path=request.worktree_path,
    )


class CliDirectMutationAdapter(BaseAgentProvider):
    """Base for providers that edit the worktree directly instead of returning a diff.

    Edits that fail patch validation are discarded from the worktree; a failed discard is reported in the response errors.
    """

    def _default_run(self, request: CliMutationRunRequest) -> CliMutationOutcome:
        """Execute the provider tool against the mutation request."""
        raise NotImplementedError

    def _preflight(self, request: AgentRequest) -> str | None:
        """Perform provider-specific preflight checks before running; they run after the base credential check."""
        return None

    def _provider_name(self) -> str:
        """Return the display name of the mutation adapter provider."""
        return "direct-mutation"

    def _invoke(self, request: AgentRequest) -> AgentResponse:
        """Run the provider in the worktree; never raises for classified outcomes."""
        started = time.monotonic()

        preflight_error = self._preflight(request)
        if preflight_error is not None:
            return provider_error_response(duration_ms=elapsed_ms(started), detail=preflight_error)

        try:
            baseline = resolve_pre_agent_baseline(request.worktree_path)
        except MutationGitError as exc:
            return provider_error_response(
                duration_ms=elapsed_ms(started), detail=f"Failed to resolve worktree baseline: {exc}"
            )

        prompt = build_mutation_prompt(request)
        outcome = self._default_run(
            CliMutationRunRequest(
                worktree_path=request.worktree_path,
                prompt=prompt,
                model=request.model,
                timeout_seconds=float(request.timeout_seconds),
            )
        )
        duration_ms = elapsed_ms(started)

        return self._respond_to_outcome(request, outcome, baseline, duration_ms)

    def _respond_to_outcome(
        self,
        request: AgentRequest,
        outcome: CliMutationOutcome,
        baseline: str,
        duration_ms: int,
    ) -> AgentResponse:
        """Classify a finished runner outcome into the timeout, provider-error, no-op, or proposal response."""
        if outcome.status == "timeout":
            return timeout_response(
                provider=self._provider_name(),
                timeout_seconds=request.timeout_seconds,
                duration_ms=duration_ms,
                raw_text=outcome.result_text,
                mutation_baseline_ref=baseline,
            )

        if outcome.status == "error":
            return provider_error_response(
                duration_ms=duration_ms,
                detail=outcome.error_detail or "Direct-mutation runner returned error",
                raw_text=outcome.result_text,
                mutation_baseline_ref=baseline,
            )

        try:
            diff, _ = capture_diff_since(request.worktree_path, baseline)
        except MutationGitError as exc:
            return provider_error_response(
                duration_ms=duration_ms,
                detail=f"Failed to capture worktree diff: {exc}",
                raw_text=outcome.result_text,
                mutation_baseline_ref=baseline,
            )

        if not diff.strip():
            return no_op_response(duration_ms=duration_ms, raw_text=outcome.result_text, mutation_baseline_ref=baseline)

        gate = validate_request_patch(request, diff)
        if gate.status != PatchApplyStatus.CHECKED_OK:
            return self._reject_gated_edit(request, gate, outcome, baseline, duration_ms)

        return AgentResponse(
            status=AgentResponseStatus.PROPOSED_PATCH,
            unified_diff=diff,
            duration_ms=duration_ms,
            mutation_baseline_ref=baseline,
            raw_text=outcome.result_text,
        )

    def _reject_gated_edit(
        self,
        request: AgentRequest,
        gate: PatchApplyResult,
        outcome: CliMutationOutcome,
        baseline: str,
        duration_ms: int,
    ) -> AgentResponse:
        """Discard the rejected edit and return PROVIDER_ERROR carrying the gate's errors, led by any discard failure."""
        discard_detail: str | None = None
        try:
            discard_since(request.worktree_path, baseline)
        except MutationGitError as exc:
            discard_detail = f"Failed to discard rejected worktree edit: {exc}"

        return provider_error_response(
            duration_ms=duration_ms,
            detail=discard_detail,
            errors=gate.errors,
            raw_text=outcome.result_text,
            mutation_baseline_ref=baseline,
        )
