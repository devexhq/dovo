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
    RunLogEvent,
    RunLogEventType,
)
from dovo.core.sessions.services.reconcile import reconcile_stale_runs
from dovo.core.sessions.sessions import Session, SessionCollection

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
    "RunLogEvent",
    "RunLogEventType",
    "Session",
    "SessionCollection",
    "reconcile_stale_runs",
]
