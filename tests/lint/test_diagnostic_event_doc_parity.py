"""Parity: the event-file field table in `docs/cli/doctor.md` matches `DiagnosticEvent`."""

from __future__ import annotations

import re
from typing import Final

from dovo.common.diagnostic_log.models import DiagnosticEvent
from tests.lint.astlib import REPO_ROOT

DOCTOR_DOC: Final = REPO_ROOT / "docs" / "cli" / "doctor.md"
_HEADING: Final = "## Diagnostic logs"
_ROW_RE: Final = re.compile(r"^\|\s*`(?P<field>[a-z_]+)`\s*\|", re.MULTILINE)


def _documented_fields(section: str) -> list[str]:
    """Return the field names in the first column of the section's table, in order."""
    return [match["field"] for match in _ROW_RE.finditer(section)]


def _diagnostic_logs_section() -> str:
    """Return the text of the Diagnostic logs section up to the next `##` heading."""
    return DOCTOR_DOC.read_text(encoding="utf-8").split(_HEADING, 1)[1].split("\n## ", 1)[0]


class DiagnosticEventDocParityTests:
    """The documented event-file fields must agree with `DiagnosticEvent` field for field."""

    def test_documented_fields_match_diagnostic_event_fields(self) -> None:
        documented = _documented_fields(_diagnostic_logs_section())

        assert "event_id" in documented
        assert documented == list(DiagnosticEvent.model_fields)
