"""Direct-mode agent attempt: provider invocation, patch validation, and worktree patch application."""

from __future__ import annotations

from pathlib import Path

from dovo.core.agents.cli_mutation import validate_request_patch
from dovo.core.agents.factory import get_agent_adapter
from dovo.core.agents.models import (
    AgentAttempt,
    AgentRequest,
    AgentResponse,
    AgentResponseStatus,
    ProviderKind,
    ResolvedAgentSettings,
)
from dovo.core.agents.registry import PROVIDERS
from dovo.core.git import GitError, GitRunner
from dovo.core.patch import GitDiffParser, PatchApplyStatus


def run_direct_attempt(
    *,
    instruction: str,
    settings: ResolvedAgentSettings,
    worktree_path: Path,
    timeout_seconds: int,
) -> AgentAttempt:
    """Run one direct-mode agent attempt through the resolved provider and return the classified attempt."""
    request = _build_request(instruction, settings, worktree_path, timeout_seconds)

    return _run_provider(request, settings.provider)


def _build_request(
    instruction: str, settings: ResolvedAgentSettings, worktree_path: Path, timeout_seconds: int
) -> AgentRequest:
    """Build the direct-mode AgentRequest from the instruction and the resolved settings."""
    return AgentRequest(
        mode="direct",
        instruction=instruction,
        payload=None,
        worktree_path=worktree_path,
        timeout_seconds=timeout_seconds,
        model=settings.model,
        endpoint=settings.endpoint,
        temperature=settings.temperature,
        max_tokens=settings.max_tokens,
    )


def _run_provider(request: AgentRequest, provider: str) -> AgentAttempt:
    """Resolve the adapter, call propose_fix once, and settle the response; any exception becomes provider_error."""
    try:
        adapter = get_agent_adapter(provider)
        response = adapter.propose_fix(request)

        return _settle(PROVIDERS[provider].kind, request, response)
    except Exception as exc:
        return AgentAttempt(status=AgentResponseStatus.PROVIDER_ERROR, diagnostics=[f"Agent provider error: {exc}"])


def _response_text(response: AgentResponse) -> str | None:
    """Return the provider summary, falling back to its raw result text."""
    return response.summary or response.raw_text


def _settle(kind: ProviderKind, request: AgentRequest, response: AgentResponse) -> AgentAttempt:
    """Classify a provider response: patches settle by provider kind, every other status maps straight through."""
    if response.status == AgentResponseStatus.PROPOSED_PATCH:
        if kind == ProviderKind.DIRECT_MUTATION:
            return _accept_direct_mutation(response)
        return _settle_diff_returning(request, response)

    return AgentAttempt(
        status=response.status,
        summary=_response_text(response),
        unfixable_reason=response.unfixable_reason,
        diagnostics=list(response.errors),
    )


def _settle_diff_returning(request: AgentRequest, response: AgentResponse) -> AgentAttempt:
    """Require a non-empty diff, validate it, apply it in the worktree, and report PROPOSED_PATCH only after apply succeeds."""
    summary = _response_text(response)
    diff = response.unified_diff or ""

    if not diff.strip():
        diagnostics = ["Agent returned a proposed patch without a diff."]
    else:
        gate = validate_request_patch(request, diff)
        diagnostics = (
            _apply_in_worktree(request.worktree_path, diff)
            if gate.status == PatchApplyStatus.CHECKED_OK
            else list(gate.errors)
        )
        if not diagnostics:
            return AgentAttempt(
                status=AgentResponseStatus.PROPOSED_PATCH,
                summary=summary,
                touched_files=sorted(set(gate.touched_files)),
            )

    return AgentAttempt(status=AgentResponseStatus.PROVIDER_ERROR, summary=summary, diagnostics=diagnostics)


def _apply_in_worktree(worktree_path: Path, diff: str) -> list[str]:
    """Run apply_check then apply in the worktree and return failure diagnostics (empty when the patch applied)."""
    try:
        returncode, _, stderr = GitRunner.apply_check(worktree_path, diff)
        if returncode != 0:
            return [f"Patch does not apply cleanly: {stderr.strip()}"]

        returncode, _, stderr = GitRunner.apply(worktree_path, diff)
        if returncode != 0:
            return [f"Patch application failed: {stderr.strip()}"]
    except GitError as exc:
        return [f"Patch application failed: {exc}"]

    return []


def _accept_direct_mutation(response: AgentResponse) -> AgentAttempt:
    """Record an accepted direct-mutation response and the paths its diff touched, without re-applying it."""
    paths, _, _ = GitDiffParser(response.unified_diff or "").parse()

    return AgentAttempt(
        status=AgentResponseStatus.PROPOSED_PATCH,
        summary=_response_text(response),
        touched_files=sorted(set(paths)),
    )
