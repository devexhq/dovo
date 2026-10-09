"""Direct-mode agent attempt: provider invocation and classification of the provider response."""

from __future__ import annotations

from pathlib import Path

from dovo.core.agents.factory import get_agent_adapter
from dovo.core.agents.models import (
    AgentAttempt,
    AgentAttemptContext,
    AgentRequest,
    AgentResponse,
    AgentResponseStatus,
    ResolvedAgentSettings,
)
from dovo.core.agents.responses import diagnostics_with_fixes
from dovo.core.git import GitDiffParser


def run_direct_attempt(
    *,
    instruction: str,
    settings: ResolvedAgentSettings,
    worktree_path: Path,
    timeout_seconds: int,
    context: AgentAttemptContext | None = None,
) -> AgentAttempt:
    """Run one direct-mode agent attempt through the resolved provider, carrying the step boundary's invocation paths and environment layers to the request."""
    request = _build_request(instruction, settings, worktree_path, timeout_seconds, context or AgentAttemptContext())

    return _run_provider(request, settings.provider)


def _build_request(
    instruction: str,
    settings: ResolvedAgentSettings,
    worktree_path: Path,
    timeout_seconds: int,
    context: AgentAttemptContext,
) -> AgentRequest:
    """Build the direct-mode AgentRequest from the instruction, the resolved settings, and the attempt context."""
    invocation = context.invocation
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
        agent_scratch_path=invocation.scratch_path if invocation is not None else None,
        invocation=invocation,
        env_passthrough=settings.env_passthrough,
        env_mode=settings.env_mode,
        env=dict(context.env),
        metadata_env=dict(context.metadata_env),
    )


def _run_provider(request: AgentRequest, provider: str) -> AgentAttempt:
    """Resolve the adapter, call invoke once, and settle the response; any exception becomes provider_error."""
    try:
        adapter = get_agent_adapter(provider)
        response = adapter.invoke(request)

        return _settle(response)
    except Exception as exc:
        return AgentAttempt(status=AgentResponseStatus.PROVIDER_ERROR, diagnostics=[f"Agent provider error: {exc}"])


def _response_text(response: AgentResponse) -> str | None:
    """Return the provider summary, falling back to its raw result text."""
    return response.summary or response.raw_text


def _settle(response: AgentResponse) -> AgentAttempt:
    """Accept a PROPOSED_PATCH as a direct mutation; every other status maps straight through."""
    if response.status == AgentResponseStatus.PROPOSED_PATCH:
        return _accept_direct_mutation(response)

    diagnostics = diagnostics_with_fixes(response.errors, response.fixes)
    return AgentAttempt(
        status=response.status,
        summary=_response_text(response),
        unfixable_reason=response.unfixable_reason,
        diagnostics=diagnostics,
        env_withheld=response.env_withheld,
    )


def _accept_direct_mutation(response: AgentResponse) -> AgentAttempt:
    """Record an accepted direct-mutation response and the paths its diff touched, without re-applying it."""
    paths, _, _ = GitDiffParser(response.unified_diff or "").parse()

    return AgentAttempt(
        status=AgentResponseStatus.PROPOSED_PATCH,
        summary=_response_text(response),
        touched_files=sorted(set(paths)),
        env_withheld=response.env_withheld,
    )
