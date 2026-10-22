"""Direct-mode agent attempt: provider invocation and classification of the provider response."""

from __future__ import annotations

from pathlib import Path

from dovo.common.diagnostic_log import DiagnosticLevel, diagnostic_hint_line, record_exception
from dovo.common.tool_policy import ToolPolicy
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
    tools: ToolPolicy,
    max_files: int | None = None,
    context: AgentAttemptContext | None = None,
) -> AgentAttempt:
    """Run one direct-mode agent attempt through the resolved provider, carrying the step's final tool policy, invocation paths, and environment layers to the request."""
    request = _build_request(
        instruction, settings, worktree_path, timeout_seconds, tools, max_files, context or AgentAttemptContext()
    )

    return _run_provider(request, settings.provider)


def _build_request(
    instruction: str,
    settings: ResolvedAgentSettings,
    worktree_path: Path,
    timeout_seconds: int,
    tools: ToolPolicy,
    max_files: int | None,
    context: AgentAttemptContext,
) -> AgentRequest:
    """Build the direct-mode AgentRequest from the instruction, the resolved settings, the final tool policy, the step's file-limit override, and the attempt context."""
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
        max_files=max_files,
        agent_scratch_path=invocation.scratch_path if invocation is not None else None,
        rejected_patch_destination=context.rejected_patch_destination,
        invocation=invocation,
        env_passthrough=settings.env_passthrough,
        env_mode=settings.env_mode,
        mcp_servers=settings.mcp_servers,
        tools=tools,
        env=dict(context.env),
        metadata_env=dict(context.metadata_env),
        on_line=context.on_line,
        on_progress=context.on_progress,
        on_env_withheld=context.on_env_withheld,
    )


def _run_provider(request: AgentRequest, provider: str) -> AgentAttempt:
    """Resolve the adapter, call invoke once, and settle the response; any exception becomes provider_error."""
    try:
        adapter = get_agent_adapter(provider)
    except ValueError as exc:
        # No record: an unregistered provider is a configuration error the message names.
        return AgentAttempt(status=AgentResponseStatus.PROVIDER_ERROR, diagnostics=[f"Agent provider error: {exc}"])
    except Exception as exc:
        return _recorded_provider_error(exc, request, provider)

    try:
        return _settle(adapter.invoke(request))
    except Exception as exc:
        return _recorded_provider_error(exc, request, provider)


def _recorded_provider_error(exc: Exception, request: AgentRequest, provider: str) -> AgentAttempt:
    """Record exc at error level and return the provider_error attempt carrying the hint line as a warning."""
    record = record_exception(
        exc,
        level=DiagnosticLevel.ERROR,
        operation="agent.run",
        context={"provider": provider, "path": str(request.worktree_path)},
    )
    line = diagnostic_hint_line(record)
    return AgentAttempt(
        status=AgentResponseStatus.PROVIDER_ERROR,
        diagnostics=[f"Agent provider error: {exc}"],
        warnings=[line] if line is not None else [],
    )


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
        rejected_patch_path=response.rejected_patch_path,
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
