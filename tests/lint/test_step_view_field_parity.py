"""Parity: a step's fields agree across the live `run.step_done` event, the saved record and `dovo session show`.

Each view carries the shared step fields plus an allow-list of fields it alone carries; `docs/cli/run.md` and
`docs/cli/session.md` document each allow-list. A field added to one view fails here until it is added to the others
or to that view's allow-list.
"""

from __future__ import annotations

import re
from typing import Final

from pydantic import BaseModel

from dovo.cli.ui.events import StepDoneEvent
from dovo.cli.ui.formatters.events.step_done import StepDoneFormatter
from dovo.core.sessions import StepRunSummary
from dovo.engine.executors.models import StepRecord
from tests.lint.astlib import REPO_ROOT

SHARED: Final = frozenset(
    {
        "step_id",
        "status",
        "attempts",
        "exit_code",
        "duration_seconds",
        "error_message",
        "error_code",
        "remediations",
        "rejected_patch_path",
    }
)
EVENT_ONLY: Final = frozenset({"idx", "total"})
RECORD_ONLY: Final = frozenset({"stdout", "stderr", "outputs", "warnings"})
SHOW_ONLY: Final = frozenset(
    {"kind", "name", "state", "loop_id", "iteration", "iterations", "skip_cause", "branch", "if_id"}
)

RUN_DOC: Final = REPO_ROOT / "docs" / "cli" / "run.md"
_HEADING: Final = "### Step done keys"
_ROW_RE: Final = re.compile(r"^\|\s*`(?P<key>[a-z_]+)`\s*\|", re.MULTILINE)


def _unexpected(model: type[BaseModel], view_only: frozenset[str]) -> str:
    """Describe how model's fields differ from the shared fields plus view_only."""
    fields = set(model.model_fields)
    expected = SHARED | view_only
    return (
        f"{model.__name__}: in this view only: {sorted(fields - expected)}; "
        f"missing from this view: {sorted(expected - fields)}"
    )


def _documented_step_done_keys() -> set[str]:
    """Return the keys in the first column of run.md's Step done keys table, up to the next heading."""
    section = RUN_DOC.read_text(encoding="utf-8").split(_HEADING, 1)[1].split("\n#", 1)[0]
    return {match["key"] for match in _ROW_RE.finditer(section)}


def _emitted_step_done_keys() -> set[str]:
    """Return the keys of a `run.step_done` payload as `--format json` serializes it."""
    event = StepDoneEvent(idx=1, total=1, step_id="s", status="completed", attempts=1, exit_code=0)
    return set(StepDoneFormatter().to_json_serializable(event))


class StepViewFieldParityTests:
    """Every view carries the shared step fields and, beyond them, only its documented allow-list."""

    def test_live_event_carries_the_shared_fields_and_its_allow_list(self) -> None:
        assert set(StepDoneEvent.model_fields) == SHARED | EVENT_ONLY, _unexpected(StepDoneEvent, EVENT_ONLY)

    def test_saved_record_carries_the_shared_fields_and_its_allow_list(self) -> None:
        assert set(StepRecord.model_fields) == SHARED | RECORD_ONLY, _unexpected(StepRecord, RECORD_ONLY)

    def test_session_show_row_carries_the_shared_fields_and_its_allow_list(self) -> None:
        assert set(StepRunSummary.model_fields) == SHARED | SHOW_ONLY, _unexpected(StepRunSummary, SHOW_ONLY)

    def test_documented_step_done_keys_match_emitted_keys(self) -> None:
        documented = _documented_step_done_keys()
        emitted = _emitted_step_done_keys()

        assert documented == emitted, (
            f"{RUN_DOC.relative_to(REPO_ROOT)} Step done keys: "
            f"documented but not emitted: {sorted(documented - emitted)}; "
            f"emitted but not documented: {sorted(emitted - documented)}"
        )
