"""Shared BlueprintRunService/BlueprintResumeService result-building helpers."""

from __future__ import annotations

from dovo.common.diagnostic_log import append_diagnostic_hint
from dovo.common.exceptions import Failure
from dovo.core.agents.environment import env_passthrough_flag_error
from dovo.core.agents.models import AgentEnvMode, AgentEnvOverrides
from dovo.core.db import SessionRecord, SessionsRepository
from dovo.engine.executors.failure_log import logged_failure
from dovo.engine.models import BlueprintRunResult, RunOutcome


def fail(warnings: list[str], message: str) -> BlueprintRunResult:
    """Construct a failed BlueprintRunResult carrying the caller's accumulated warnings."""
    return BlueprintRunResult(
        session_record=None,
        errors=[message],
        warnings=warnings,
    )


def fail_with(warnings: list[str], failure: Failure) -> BlueprintRunResult:
    """Construct a failed BlueprintRunResult from a caught failure: its code, message with any Details logged line, and remediations."""
    return BlueprintRunResult(
        session_record=None,
        error_code=failure.error_code,
        errors=[append_diagnostic_hint(failure.message, failure.diagnostic)],
        remediations=list(failure.remediations),
        warnings=warnings,
    )


def load_record(sessions_db: SessionsRepository, warnings: list[str], session_id: str) -> SessionRecord | None:
    """Load a SessionRecord by session id, appending a warning and returning None on failure."""
    try:
        return sessions_db.get(session_id)
    except Exception as exc:
        warnings.append(
            logged_failure(
                f"Failed to load session record for '{session_id}': {exc}",
                exc,
                operation="engine.read_run",
                context={"session_id": session_id},
            )
        )
        return None


def finalize(
    sessions_db: SessionsRepository,
    warnings: list[str],
    run_outcome: RunOutcome,
    session_id: str,
) -> BlueprintRunResult:
    """Merge outcome warnings, load the session record, and build the terminal BlueprintRunResult.

    ``session_record`` is None whenever the record can't be found — no synthetic
    fallback record is ever substituted, for either caller.
    """
    warnings.extend(run_outcome.warnings)
    record = load_record(sessions_db, warnings, session_id) if session_id else None

    return BlueprintRunResult(
        session_record=record,
        errors=list(run_outcome.errors),
        warnings=warnings,
        paused_prompt_id=run_outcome.paused_prompt_id,
    )


def resolve_env_overrides(
    env_mode: AgentEnvMode | None, env_passthrough: list[str]
) -> tuple[AgentEnvOverrides | None, str | None]:
    """Validate the --env-passthrough entries and build the per-invocation overrides, or return the fixed invalid-value message."""
    message = env_passthrough_flag_error(env_passthrough)
    if message is not None:
        return None, message

    if env_mode is None and not env_passthrough:
        return None, None

    return AgentEnvOverrides(env_mode=env_mode, env_passthrough=env_passthrough), None
