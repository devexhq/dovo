"""Parity: the Step keys table in `docs/cli/session.md` matches the keys of a `dovo session show` `steps[]` row."""

from __future__ import annotations

import re
from typing import Final

from dovo.cli.ui.formatters.session.session_show import SessionShowFormatter
from dovo.core.sessions import HistoryShowResult, StepRunSummary
from tests.lint.astlib import REPO_ROOT

SESSION_DOC: Final = REPO_ROOT / "docs" / "cli" / "session.md"
_HEADING: Final = "### Step keys"
_ROW_RE: Final = re.compile(r"^\|\s*`(?P<key>[a-z_]+)`\s*\|", re.MULTILINE)


def _documented_keys() -> set[str]:
    """Return the keys in the first column of the Step keys table, up to the next heading."""
    section = SESSION_DOC.read_text(encoding="utf-8").split(_HEADING, 1)[1].split("\n#", 1)[0]
    return {match["key"] for match in _ROW_RE.finditer(section)}


def _emitted_keys() -> set[str]:
    """Return the keys of one `steps[]` row as `dovo session show --format json` serializes it."""
    result = HistoryShowResult(session_id="s", steps=[StepRunSummary(kind="step", step_id="s", state="completed")])
    steps = SessionShowFormatter().to_json_serializable(result)["steps"]
    assert isinstance(steps, list)
    row = steps[0]
    assert isinstance(row, dict)
    return set(row)


class SessionShowStepDocParityTests:
    """The documented `steps[]` keys must agree with the emitted ones, by set."""

    def test_documented_step_keys_match_emitted_step_keys(self) -> None:
        documented = _documented_keys()
        emitted = _emitted_keys()

        assert documented == emitted, (
            f"{SESSION_DOC.relative_to(REPO_ROOT)} Step keys: "
            f"documented but not emitted: {sorted(documented - emitted)}; "
            f"emitted but not documented: {sorted(emitted - documented)}"
        )
