"""Session record and session summary view builders for the session formatter tests."""

from __future__ import annotations

from dovo.cli.ui.formatters.session.session_views import SessionSummaryView
from dovo.core.db import SessionRecord, SessionStatus


def session_record(**overrides: object) -> SessionRecord:
    """Build a completed 10-second 'sess-12345678' SessionRecord, with any field replaced by overrides."""
    fields: dict[str, object] = {
        "id": 1,
        "project_id": "test-project",
        "session_id": "sess-12345678",
        "blueprint_key": "deploy-blueprint",
        "blueprint_name": "deploy-blueprint",
        "status": SessionStatus.COMPLETED,
        "branch_name": "feature/test",
        "started_at": "2026-08-19 01:00:00",
        "completed_at": "2026-08-19 01:00:10",
        "error_message": None,
    }
    return SessionRecord.model_validate(fields | overrides)


def session_summary_view(**overrides: object) -> SessionSummaryView:
    """Build the SessionSummaryView that session_record() transforms into, with any field replaced by overrides."""
    fields: dict[str, object] = {
        "session_id": "sess-12345678",
        "blueprint_name": "deploy-blueprint",
        "status": "completed",
        "branch_name": "feature/test",
        "started_at": "2026-08-19 01:00:00",
        "completed_at": "2026-08-19 01:00:10",
        "duration_seconds": 10.0,
        "error_message": None,
    }
    return SessionSummaryView.model_validate(fields | overrides)
