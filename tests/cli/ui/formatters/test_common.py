"""Tier 2 tests for the shared formatter helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from rich.text import Text

from dovo.cli.ui.formatters.common import with_diagnostics
from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord
from tests.harness.formatter import render_rich

RECORD_A: Final[DiagnosticRecord] = DiagnosticRecord(
    event_id="exc_aaaaaaaaaaaa",
    log_path=Path("/home/me/.dovo/logs/2026-10-23/exc_aaaaaaaaaaaa.json"),
    level=DiagnosticLevel.ERROR,
)
RECORD_B: Final[DiagnosticRecord] = DiagnosticRecord(
    event_id="exc_bbbbbbbbbbbb",
    log_path=Path("/home/me/.dovo/logs/2026-10-23/exc_bbbbbbbbbbbb.json"),
    level=DiagnosticLevel.ERROR,
)


class WithDiagnosticsTests:
    def test_appends_one_line_per_record_or_returns_renderable(self) -> None:
        """Return the renderable itself for [] and render 'Details logged: <path> (event <id>)' once per record, in order, after it."""
        body = Text("Catalog creation failed.")

        rendered = render_rich(with_diagnostics(body, [RECORD_A, RECORD_B]))

        assert with_diagnostics(body, []) is body
        assert rendered.splitlines() == [
            "Catalog creation failed.",
            "Details logged: /home/me/.dovo/logs/2026-10-23/exc_aaaaaaaaaaaa.json (event exc_aaaaaaaaaaaa)",
            "Details logged: /home/me/.dovo/logs/2026-10-23/exc_bbbbbbbbbbbb.json (event exc_bbbbbbbbbbbb)",
        ]
