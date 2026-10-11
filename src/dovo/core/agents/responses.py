"""Shared constructors for the timeout, provider-error, blocked, and no-op agent responses."""

from __future__ import annotations

from collections.abc import Sequence

from dovo.core.agents.models import AgentDenial, AgentResponse, AgentResponseStatus


def timeout_response(
    *,
    provider: str,
    timeout_seconds: int,
    duration_ms: int,
    raw_text: str | None = None,
    mutation_baseline_ref: str | None = None,
) -> AgentResponse:
    """Build the TIMEOUT response carrying the canonical timeout error and fix."""
    return AgentResponse(
        status=AgentResponseStatus.TIMEOUT,
        duration_ms=duration_ms,
        raw_text=raw_text,
        mutation_baseline_ref=mutation_baseline_ref,
        errors=[f"Agent timed out after {timeout_seconds}s (provider={provider})."],
        fixes=["Raise timeout_seconds on the agent step"],
    )


def provider_error_response(
    *,
    duration_ms: int,
    detail: str | None = None,
    errors: Sequence[str] = (),
    raw_text: str | None = None,
    mutation_baseline_ref: str | None = None,
) -> AgentResponse:
    """Build the PROVIDER_ERROR response, prefixing ``detail`` once and placing it before ``errors``.

    ``errors`` are already-classified diagnostics and are copied through unprefixed; the input is never mutated.
    """
    diagnostics = [*errors]
    if detail is not None:
        diagnostics.insert(0, f"Agent provider error (AGENT_PROVIDER_ERROR): {detail}")

    return AgentResponse(
        status=AgentResponseStatus.PROVIDER_ERROR,
        duration_ms=duration_ms,
        raw_text=raw_text,
        mutation_baseline_ref=mutation_baseline_ref,
        errors=diagnostics,
    )


def no_op_response(
    *,
    duration_ms: int,
    raw_text: str | None = None,
    mutation_baseline_ref: str | None = None,
) -> AgentResponse:
    """Build the NO_OP response."""
    return AgentResponse(
        status=AgentResponseStatus.NO_OP,
        duration_ms=duration_ms,
        raw_text=raw_text,
        mutation_baseline_ref=mutation_baseline_ref,
    )


def blocked_response(
    *,
    duration_ms: int,
    denials: Sequence[AgentDenial],
    raw_text: str | None = None,
    mutation_baseline_ref: str | None = None,
) -> AgentResponse:
    """Build the BLOCKED response carrying the permission denials and the grant-or-remove-deny fix.

    ``denials`` must be non-empty; the caller checks before building the response.
    """
    first, *rest = denials
    errors = [
        f"Agent run was blocked by tool permissions (AGENT_PERMISSION_BLOCKED): {first.tool}: {first.message}",
        *(f"{denial.tool}: {denial.message}" for denial in rest),
    ]
    fixes: list[str] = []
    for denial in denials:
        if denial.by_rule:
            fix = f"Remove or narrow the deny rule that blocked '{denial.tool}': {denial.message}"
        elif denial.capability is not None:
            fix = (
                f"Grant it in the step's tools policy, e.g. tools: {{allow: [{{capability: {denial.capability.value}}}]}}, "
                "or set it once under agent.tools in .dovo/config.json "
                "(see docs/guides/agent-providers.md#tool-policy-profiles)"
            )
        else:
            continue

        if fix not in fixes:
            fixes.append(fix)

    return AgentResponse(
        status=AgentResponseStatus.BLOCKED,
        duration_ms=duration_ms,
        raw_text=raw_text,
        mutation_baseline_ref=mutation_baseline_ref,
        errors=errors,
        fixes=fixes,
    )


def diagnostics_with_fixes(errors: Sequence[str], fixes: Sequence[str]) -> list[str]:
    """Return the errors followed by one trailing ``Fix:`` block when fixes are present."""
    diagnostics = list(errors)
    if fixes:
        diagnostics.append("Fix:\n" + "\n".join(f"- {fix}" for fix in fixes))

    return diagnostics
