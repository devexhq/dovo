"""Shared BlueprintRunService/BlueprintResumeService result-building helpers."""

from __future__ import annotations

from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord, error_diagnostics, record_exception
from dovo.common.exceptions import Failure
from dovo.core.agents.environment import check_env_passthrough_flag
from dovo.core.agents.models import AgentEnvMode, AgentEnvOverrides
from dovo.core.catalog.exceptions import CatalogValidateFindings
from dovo.core.db import SessionRecord, SessionsRepository
from dovo.engine.models import BlueprintRunResult, RunObserver, RunOutcome
from dovo.engine.notify import safe_notify


def caught_result(failure: Failure, warnings: list[str], diagnostics: list[DiagnosticRecord]) -> BlueprintRunResult:
    """Build the failed result for a failure caught before any session ran, after the caller's warnings and records."""
    return BlueprintRunResult(
        session_record=None, warnings=warnings, **failure.result_fields(prior_diagnostics=diagnostics)
    )


def failed_result(
    failure: Failure,
    warnings: list[str],
    diagnostics: list[DiagnosticRecord],
    *,
    blueprint_name: str,
    observer: RunObserver | None,
) -> BlueprintRunResult:
    """Build the failed result for a failure caught before any step ran.

    A validation failure, whose data is ``CatalogValidateFindings``, names blueprint_name as the invalid blueprint,
    lists every validation error, and hands its warnings to the observer as validation warnings; any other failure
    carries its own message alone.
    """
    fields = failure.result_fields(prior_diagnostics=diagnostics)
    findings = failure.data
    if not isinstance(findings, CatalogValidateFindings):
        return BlueprintRunResult(session_record=None, warnings=warnings, **fields)

    safe_notify(observer, "on_validation_warnings", findings.warnings)
    fields["errors"] = list(findings.errors)
    return BlueprintRunResult(session_record=None, warnings=warnings, invalid_blueprint=blueprint_name, **fields)


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

    ``session_record`` is None whenever the record can't be found — no synthetic
    fallback record is ever substituted, for either caller.
    """
    warnings.extend(run_outcome.warnings)
    diagnostics.extend(run_outcome.diagnostics)
    record = load_record(sessions_db, warnings, diagnostics, session_id) if session_id else None

    return BlueprintRunResult(
        session_record=record,
        error_code=run_outcome.error_code,
        errors=list(run_outcome.errors),
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
