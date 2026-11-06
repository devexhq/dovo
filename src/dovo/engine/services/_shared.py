"""Shared BlueprintRunService/BlueprintResumeService result-building helpers."""

from __future__ import annotations

from typing import TypedDict

from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord, error_diagnostics, record_exception
from dovo.common.exceptions import Failure
from dovo.core.agents.environment import check_env_passthrough_flag
from dovo.core.agents.models import AgentEnvMode, AgentEnvOverrides
from dovo.core.catalog.exceptions import CatalogValidateFindings
from dovo.core.db import SessionRecord, SessionsRepository, SessionStatus
from dovo.engine.models import BlueprintRunResult, RunOutcome


class SessionFields(TypedDict):
    """The run result's session fields, spread into its constructor."""

    session_id: str | None
    blueprint_name: str | None
    session_status: SessionStatus | None


def session_fields(record: SessionRecord | None) -> SessionFields:
    """Return the run result's session_id, blueprint_name and session_status from record, each None without one."""
    if record is None:
        return SessionFields(session_id=None, blueprint_name=None, session_status=None)
    return SessionFields(
        session_id=record.session_id, blueprint_name=record.blueprint_name, session_status=record.status
    )


def failed_result(
    failure: Failure,
    warnings: list[str],
    diagnostics: list[DiagnosticRecord],
    *,
    record: SessionRecord | None = None,
) -> BlueprintRunResult:
    """Build the failed result for a failure caught before any step ran, after the caller's warnings and records.

    The session fields come from record, the session the failure stopped, or are None without one. A validation
    failure, whose data is ``CatalogValidateFindings``, lists every validation error and appends its warnings to
    warnings; any other failure carries its own message alone.
    """
    fields = failure.result_fields(prior_diagnostics=diagnostics)
    findings = failure.data
    if isinstance(findings, CatalogValidateFindings):
        warnings.extend(findings.warnings)
        fields["errors"] = list(findings.errors)
    return BlueprintRunResult(warnings=warnings, **fields, **session_fields(record))


def load_record(
    sessions_db: SessionsRepository, warnings: list[str], diagnostics: list[DiagnosticRecord], session_id: str
) -> SessionRecord | None:
    """Load a SessionRecord by session id, appending a warning and its record to diagnostics and returning None on failure."""
    try:
        return sessions_db.get(session_id)
    except Exception as exc:
        warnings.append(f"Failed to load session record for '{session_id}': {exc}")
        diagnostics.extend(
            error_diagnostics(
                record_exception(
                    exc, level=DiagnosticLevel.ERROR, operation="engine.read_run", context={"session_id": session_id}
                )
            )
        )
        return None


def finalize(
    sessions_db: SessionsRepository,
    warnings: list[str],
    diagnostics: list[DiagnosticRecord],
    run_outcome: RunOutcome,
    session_id: str,
) -> BlueprintRunResult:
    """Merge outcome warnings and diagnostics, load the session record, and build the terminal BlueprintRunResult.

    The session fields are read back from the session record and are None whenever it can't be loaded, with no
    synthetic fallback; error_code, and so ok, is the outcome's whether or not the record loads.
    """
    warnings.extend(run_outcome.warnings)
    diagnostics.extend(run_outcome.diagnostics)
    record = load_record(sessions_db, warnings, diagnostics, session_id) if session_id else None

    return BlueprintRunResult(
        **session_fields(record),
        error_code=run_outcome.error_code,
        errors=list(run_outcome.errors),
        remediations=list(run_outcome.remediations),
        warnings=warnings,
        diagnostics=diagnostics,
        paused_prompt_id=run_outcome.paused_prompt_id,
    )


def resolve_env_overrides(env_mode: AgentEnvMode | None, env_passthrough: list[str]) -> AgentEnvOverrides | None:
    """Validate the --env-passthrough entries and build the per-invocation overrides, or None when no flag is set.

    Raises:
        AgentError: AGENT_ENV_OVERRIDE_INVALID for an invalid --env-passthrough entry.
    """
    check_env_passthrough_flag(env_passthrough)
    if env_mode is None and not env_passthrough:
        return None

    return AgentEnvOverrides(env_mode=env_mode, env_passthrough=env_passthrough)
