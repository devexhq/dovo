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
)
from dovo.core.sessions.services.reconcile import reconcile_stale_sessions
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
    "Session",
    "SessionCollection",
    "SessionLogEvent",
    "SessionLogEventType",
    "reconcile_stale_sessions",
]
