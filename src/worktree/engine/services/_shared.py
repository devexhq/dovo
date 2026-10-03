"""Shared BlueprintRunService/BlueprintResumeService result-building helpers."""

from __future__ import annotations

from worktree.core.db import RunRecord, RunsRepository
from worktree.engine.models import BlueprintRunResult, RunOutcome


def fail(warnings: list[str], message: str) -> BlueprintRunResult:
    """Construct a failed BlueprintRunResult carrying the caller's accumulated warnings."""
    return BlueprintRunResult(
        run_record=None,
        errors=[message],
        warnings=warnings,
    )


def load_record(runs_db: RunsRepository, warnings: list[str], session_id: str) -> RunRecord | None:
    """Load a RunRecord by session id, appending a warning and returning None on failure."""
    try:
        return runs_db.get(session_id)
    except Exception as exc:
        warnings.append(f"Failed to load run record for '{session_id}': {exc}")
        return None


def finalize(
    runs_db: RunsRepository,
    warnings: list[str],
    run_outcome: RunOutcome,
    session_id: str,
) -> BlueprintRunResult:
    """Merge outcome warnings, load the run record, and build the terminal BlueprintRunResult.

    ``run_record`` is None whenever the record can't be found — no synthetic
    fallback record is ever substituted, for either caller.
    """
    warnings.extend(run_outcome.warnings)
    record = load_record(runs_db, warnings, session_id) if session_id else None

    return BlueprintRunResult(
        run_record=record,
        errors=list(run_outcome.errors),
        warnings=warnings,
    )
