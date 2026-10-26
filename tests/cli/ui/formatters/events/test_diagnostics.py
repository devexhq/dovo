"""Tier 2 presentation contract tests for DiagnosticsFormatter."""

from __future__ import annotations

from pathlib import Path

from dovo.cli.ui.events import DiagnosticsEvent
from dovo.cli.ui.formatters.events.diagnostics import DiagnosticsFormatter
from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord
from tests.harness.formatter import render_rich


class DiagnosticsFormatterTests:
    def test_renders_details_line_per_record(self) -> None:
        """Render one 'Details logged: <log_path> (event <event_id>)' line per record, in order (terminal only, OQ-2)."""
        event = DiagnosticsEvent(
            diagnostics=[
                DiagnosticRecord(
                    event_id=event_id,
                    log_path=Path(f"/home/me/.dovo/logs/2026-10-23/{event_id}.json"),
                    level=DiagnosticLevel.ERROR,
                )
                for event_id in ("exc_aaaaaaaaaaaa", "exc_bbbbbbbbbbbb")
            ]
        )

        rendered = render_rich(DiagnosticsFormatter().to_rich(event))

        assert rendered.splitlines() == [
            "Details logged: /home/me/.dovo/logs/2026-10-23/exc_aaaaaaaaaaaa.json (event exc_aaaaaaaaaaaa)",
            "Details logged: /home/me/.dovo/logs/2026-10-23/exc_bbbbbbbbbbbb.json (event exc_bbbbbbbbbbbb)",
        ]
