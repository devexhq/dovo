"""Contract tests for the run.log JSON Lines writer."""

from __future__ import annotations

import json
from pathlib import Path

from worktree.core.runtime import RunLogEvent, RunLogEventType
from worktree.core.runtime.log_writer import append_run_log_event


class AppendRunLogEventTests:
    """[tier-1/domain] append_run_log_event: stamped, best-effort JSON line appends to run.log."""

    def test_append_run_log_event_writes_one_json_line_with_stamped_timestamp(self, tmp_path: Path) -> None:
        """[tier-1/domain] append_run_log_event: creates run.log with one JSON line carrying the event and a writer-stamped ts."""
        append_run_log_event(tmp_path, RunLogEvent(event=RunLogEventType.STEP_START, step_id="s1"))

        lines = (tmp_path / "run.log").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert (record["event"], record["step_id"]) == ("step_start", "s1")
        assert record["ts"] != ""

    def test_append_run_log_event_is_noop_when_session_log_dir_is_none(self, tmp_path: Path) -> None:
        """[tier-1/domain] append_run_log_event: session_log_dir=None writes nothing."""
        append_run_log_event(None, RunLogEvent(event=RunLogEventType.RUN_STARTED))

        assert list(tmp_path.iterdir()) == []

    def test_append_run_log_event_swallows_os_error(self, tmp_path: Path) -> None:
        """[tier-1/domain] append_run_log_event: an OSError opening run.log (here, a missing directory) is silently dropped."""
        missing_dir = tmp_path / "missing"

        append_run_log_event(missing_dir, RunLogEvent(event=RunLogEventType.RUN_STARTED))

        assert not missing_dir.exists()
