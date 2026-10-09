"""Shared BlueprintRunService/BlueprintResumeService result-building helpers."""

from __future__ import annotations

from dovo.core.agents.environment import env_passthrough_flag_error
from dovo.core.agents.models import AgentEnvMode, AgentEnvOverrides
from dovo.core.db import SessionRecord, SessionsRepository
from dovo.engine.models import BlueprintRunResult, RunOutcome


def fail(warnings: list[str], message: str) -> BlueprintRunResult:
    """Construct a failed BlueprintRunResult carrying the caller's accumulated warnings."""
    return BlueprintRunResult(
        session_record=None,
        errors=[message],
        warnings=warnings,
    )


def load_record(sessions_db: SessionsRepository, warnings: list[str], session_id: str) -> SessionRecord | None:
    """Load a SessionRecord by session id, appending a warning and returning None on failure."""
    try:
        return sessions_db.get(session_id)
    except Exception as exc:
        warnings.append(f"Failed to load session record for '{session_id}': {exc}")
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
