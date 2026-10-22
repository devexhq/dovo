"""Tests for recording unexpected engine failures in the diagnostic log."""

from __future__ import annotations

from pathlib import Path

from dovo.common.filesystem.services import resolve_global_paths
from dovo.engine.executors.failure_log import logged_failure, logged_hint, record_fallback
from tests.harness.diagnostic_log import diagnostic_hint, read_single_event

_CONTEXT = {"session_id": "sess-1", "step_id": "build"}


class FailureLogTests:
    def test_logged_failure_appends_hint_and_writes_error_record(self, frozen_diagnostic_clock: Path) -> None:
        """Return 'msg\\nDetails logged: <file> (event exc_3f9a1c07b2de)' and write an error record with the given operation and context."""
        message = logged_failure("msg", RuntimeError("boom"), operation="engine.persist", context=_CONTEXT)

        assert message == f"msg{diagnostic_hint(frozen_diagnostic_clock)}"
        event = read_single_event(frozen_diagnostic_clock)
        assert (event["level"], event["operation"], event["context"]) == ("error", "engine.persist", _CONTEXT)

    def test_logged_hint_returns_bare_line_and_writes_error_record(self, frozen_diagnostic_clock: Path) -> None:
        """Return 'Details logged: <file> (event exc_3f9a1c07b2de)' and write an error record with the given operation and context."""
        line = logged_hint(RuntimeError("boom"), operation="engine.step", context=_CONTEXT)

        assert line == diagnostic_hint(frozen_diagnostic_clock).lstrip("\n")
        event = read_single_event(frozen_diagnostic_clock)
        assert (event["level"], event["operation"], event["context"]) == ("error", "engine.step", _CONTEXT)

    def test_record_fallback_writes_debug_record(self, frozen_diagnostic_clock: Path) -> None:
        """Write one debug record with the given operation and context."""
        record_fallback(RuntimeError("boom"), operation="engine.notify", context={"method": "on_step_start"})

        event = read_single_event(frozen_diagnostic_clock)
        assert (event["level"], event["operation"], event["context"]) == (
            "debug",
            "engine.notify",
            {"method": "on_step_start"},
        )

    def test_helpers_leave_message_unchanged_when_recorder_cannot_write(self) -> None:
        """Return 'msg' from logged_failure and None from logged_hint when <DOVO_HOME>/logs is a regular file."""
        logs_dir = resolve_global_paths().logs_dir
        logs_dir.parent.mkdir(parents=True, exist_ok=True)
        logs_dir.write_text("not a directory", encoding="utf-8")

        assert logged_failure("msg", RuntimeError("boom"), operation="engine.persist", context=_CONTEXT) == "msg"
        assert logged_hint(RuntimeError("boom"), operation="engine.step", context=_CONTEXT) is None
