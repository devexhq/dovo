"""Session domain: Session and SessionCollection entrypoints, result models, and stale-run reconciliation."""

from dovo.core.sessions.models import (
    DiffResult,
    DiffStatus,
    HistoryListResult,
    HistoryListStatus,
    HistoryShowResult,
    HistoryShowStatus,
    LogsShowResult,
    LogsShowStatus,
    LogStreamFilter,
    ReconciliationResult,
    SessionLogEvent,
    SessionLogEventType,
    StepRunSummary,
)
from dovo.core.sessions.services.reconcile import current_process_started_at, reconcile_stale_sessions
from dovo.core.sessions.sessions import Session, SessionCollection, history_redactor

__all__ = [
    "DiffResult",
    "DiffStatus",
    "HistoryListResult",
    "HistoryListStatus",
    "HistoryShowResult",
    "HistoryShowStatus",
    "LogStreamFilter",
    "LogsShowResult",
    "LogsShowStatus",
    "ReconciliationResult",
    "Session",
    "SessionCollection",
    "SessionLogEvent",
    "SessionLogEventType",
    "StepRunSummary",
    "current_process_started_at",
    "history_redactor",
    "reconcile_stale_sessions",
]
