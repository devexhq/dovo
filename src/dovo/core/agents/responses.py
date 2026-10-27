"""Shared constructors for the timeout, provider-error, blocked, unfixable, and no-op agent responses."""

from __future__ import annotations

from collections.abc import Sequence

from dovo.common.formatters import format_remediations
from dovo.common.models import Remediation
from dovo.core.agents.models import AgentDenial, AgentResponse, AgentResponseStatus


def timeout_response(
    *,
    provider: str,
    timeout_seconds: int,
    duration_seconds: float,
    raw_text: str | None = None,
    mutation_baseline_ref: str | None = None,
) -> AgentResponse:
    """Build the TIMEOUT response carrying the canonical timeout error and remediation."""
    return AgentResponse(
        status=AgentResponseStatus.TIMEOUT,
        duration_seconds=duration_seconds,
        raw_text=raw_text,
        mutation_baseline_ref=mutation_baseline_ref,
        errors=[f"Agent timed out after {timeout_seconds}s (provider={provider})."],
        remediations=[Remediation(message="Raise 'timeout_seconds' on the agent step")],
    )


def provider_error_response(
    *,
    duration_seconds: float,
    detail: str | None = None,
    errors: Sequence[str] = (),
    raw_text: str | None = None,
    mutation_baseline_ref: str | None = None,
) -> AgentResponse:
    """Build the PROVIDER_ERROR response, prefixing ``detail`` once and placing it before ``errors``.

    ``errors`` are already-classified error strings and are copied through unprefixed; the input is never mutated.
    """
    errors_out = [*errors]
    if detail is not None:
        errors_out.insert(0, f"Agent provider error: {detail}")

    return AgentResponse(
        status=AgentResponseStatus.PROVIDER_ERROR,
        duration_seconds=duration_seconds,
        raw_text=raw_text,
        mutation_baseline_ref=mutation_baseline_ref,
        errors=errors_out,
    )


def no_op_response(
    *,
    duration_seconds: float,
    raw_text: str | None = None,
    mutation_baseline_ref: str | None = None,
) -> AgentResponse:
    """Build the NO_OP response."""
    return AgentResponse(
        status=AgentResponseStatus.NO_OP,
        duration_seconds=duration_seconds,
        raw_text=raw_text,
        mutation_baseline_ref=mutation_baseline_ref,
    )


def unfixable_response(
    *,
    reason: str,
    duration_seconds: float,
    errors: Sequence[str] = (),
    raw_text: str | None = None,
    mutation_baseline_ref: str | None = None,
) -> AgentResponse:
    """Build the UNFIXABLE response carrying the agent's reason and any errors from discarding its edit."""
    return AgentResponse(
        status=AgentResponseStatus.UNFIXABLE,
        duration_seconds=duration_seconds,
        unfixable_reason=reason,
        raw_text=raw_text,
        mutation_baseline_ref=mutation_baseline_ref,
        errors=[*errors],
    )


def blocked_response(
    *,
    provider: str,
    duration_seconds: float,
    denials: Sequence[AgentDenial],
    raw_text: str | None = None,
    mutation_baseline_ref: str | None = None,
) -> AgentResponse:
    """Build the BLOCKED response whose grant remediation names agents.<provider>.tools.

    ``denials`` must be non-empty.
    """
    first, *rest = denials
    errors = [
        f"Agent run was blocked by tool permissions: {first.tool}: {first.message}",
        *(f"{denial.tool}: {denial.message}" for denial in rest),
    ]
    remediations: list[Remediation] = []
    for denial in denials:
        if denial.by_rule:
            remediation = Remediation(
                message=f"Remove or narrow the deny rule that blocked '{denial.tool}': {denial.message}"
            )
        elif denial.capability is not None:
            remediation = Remediation(
                message=(
                    "Grant it in the step's tools policy, e.g. "
                    f"'tools: {{allow: [{{capability: {denial.capability.value}}}]}}', "
                    f"or set it once under 'agents.{provider}.tools' in '.dovo/config.json' "
                    "(see 'docs/guides/agent-providers.md#tool-policy-profiles')"
                )
            )
        else:
            continue

        if remediation not in remediations:
            remediations.append(remediation)

    return AgentResponse(
        status=AgentResponseStatus.BLOCKED,
        duration_seconds=duration_seconds,
        raw_text=raw_text,
        mutation_baseline_ref=mutation_baseline_ref,
        errors=errors,
        remediations=remediations,
    )


def errors_with_fix_block(errors: Sequence[str], remediations: Sequence[Remediation]) -> list[str]:
    """Return the errors followed by one trailing Fix: block when remediations are present."""
    errors_out = list(errors)
    if remediations:
        errors_out.append(format_remediations(remediations))

    return errors_out
