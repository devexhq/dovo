"""Tests for the diagnostic log record's published dump shape."""

from __future__ import annotations

from pathlib import Path

from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord


class DiagnosticRecordDumpTests:
    def test_dump_omits_level(self) -> None:
        """Dump a record in JSON mode to exactly {"event_id": "exc_3f9a1c07b2de", "log_path": "/home/me/.dovo/logs/2026-10-23/exc_3f9a1c07b2de.json"}."""
        record = DiagnosticRecord(
            event_id="exc_3f9a1c07b2de",
            log_path=Path("/home/me/.dovo/logs/2026-10-23/exc_3f9a1c07b2de.json"),
            level=DiagnosticLevel.ERROR,
        )

        assert record.model_dump(mode="json") == {
            "event_id": "exc_3f9a1c07b2de",
            "log_path": "/home/me/.dovo/logs/2026-10-23/exc_3f9a1c07b2de.json",
        }
