"""Tests for the diagnostic log recorder and its day-directory retention."""

from __future__ import annotations

import json
import os
import stat
import sys
import traceback
from pathlib import Path

import pytest

from dovo.common.diagnostic_log import (
    DiagnosticLevel,
    DiagnosticRecord,
    append_diagnostic_hint,
    bind_cli_command,
    diagnostic_hint_line,
    record_exception,
    reset_cli_command,
)
from dovo.common.diagnostic_log.services import prune_log_days
from tests.harness.diagnostic_log import FROZEN_EVENT_ID, FROZEN_TIMESTAMP


def _raised(exc: Exception) -> Exception:
    """Raise and catch exc so it carries a traceback, and return it."""
    try:
        raise exc
    except Exception as caught:
        return caught


def _read_event(path: Path) -> dict[str, object]:
    """Return the parsed JSON object stored in one event file."""
    return json.loads(path.read_text(encoding="utf-8"))


class RecordExceptionTests:
    def test_writes_one_event_file_with_literal_fields(self, frozen_diagnostic_clock: Path) -> None:
        """Write <DOVO_HOME>/logs/2026-10-19/exc_3f9a1c07b2de.json with the literal event dict and return the matching DiagnosticRecord."""
        exc = _raised(RuntimeError("disk on fire"))

        record = record_exception(
            exc,
            level=DiagnosticLevel.ERROR,
            operation="catalog.create",
            context={"item_type": "blueprint", "name": "demo"},
        )

        assert record == DiagnosticRecord(
            event_id=FROZEN_EVENT_ID, log_path=frozen_diagnostic_clock, level=DiagnosticLevel.ERROR
        )
        assert sorted(frozen_diagnostic_clock.parent.iterdir()) == [frozen_diagnostic_clock]
        assert _read_event(frozen_diagnostic_clock) == {
            "event_id": "exc_3f9a1c07b2de",
            "timestamp": FROZEN_TIMESTAMP,
            "level": "error",
            "operation": "catalog.create",
            "command": None,
            "context": {"item_type": "blueprint", "name": "demo"},
            "exception_type": "RuntimeError",
            "exception_message": "disk on fire",
            "formatted_traceback": "".join(traceback.format_exception(exc)),
        }

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
    def test_event_file_mode_is_0600(self, frozen_diagnostic_clock: Path) -> None:
        """Create the event file with permission bits 0o600."""
        record_exception(
            _raised(RuntimeError("boom")), level=DiagnosticLevel.DEBUG, operation="catalog.template_fallback"
        )

        assert stat.S_IMODE(frozen_diagnostic_clock.stat().st_mode) == 0o600

    @pytest.mark.parametrize("chain", ["cause", "context"])
    def test_traceback_contains_every_chained_exception(self, frozen_diagnostic_clock: Path, chain: str) -> None:
        """Include the inner and outer exception types and messages in formatted_traceback for raise-from and implicit chaining."""
        try:
            try:
                raise KeyError("inner-key")
            except KeyError as inner:
                if chain == "cause":
                    raise RuntimeError("outer failure") from inner
                raise RuntimeError("outer failure")  # noqa: B904 - implicit chaining under test
        except RuntimeError as outer:
            record_exception(outer, level=DiagnosticLevel.ERROR, operation="catalog.create")

        formatted = _read_event(frozen_diagnostic_clock)["formatted_traceback"]
        assert isinstance(formatted, str)
        assert "KeyError: 'inner-key'" in formatted
        assert "RuntimeError: outer failure" in formatted

    def test_redacts_token_value_in_context_message_and_traceback(
        self, frozen_diagnostic_clock: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Replace a GH_TOKEN value with [REDACTED:GH_TOKEN] in context strings, exception_message and formatted_traceback, keep non-string context values, and never write the value."""
        monkeypatch.setenv("GH_TOKEN", "s3cr3t-value")

        record_exception(
            _raised(RuntimeError("push failed with s3cr3t-value")),
            level=DiagnosticLevel.ERROR,
            operation="catalog.create",
            context={"name": "s3cr3t-value", "attempt": 2, "dry_run": None},
        )

        event = _read_event(frozen_diagnostic_clock)
        assert event["context"] == {"name": "[REDACTED:GH_TOKEN]", "attempt": 2, "dry_run": None}
        assert event["exception_message"] == "push failed with [REDACTED:GH_TOKEN]"
        assert "RuntimeError: push failed with [REDACTED:GH_TOKEN]" in str(event["formatted_traceback"])
        assert "s3cr3t-value" not in frozen_diagnostic_clock.read_text(encoding="utf-8")

    def test_unwritable_logs_dir_returns_none(self, frozen_diagnostic_clock: Path) -> None:
        """Return None without raising when <DOVO_HOME>/logs is a regular file."""
        logs_dir = frozen_diagnostic_clock.parent.parent
        logs_dir.parent.mkdir(parents=True, exist_ok=True)
        logs_dir.write_text("not a directory", encoding="utf-8")

        record = record_exception(
            _raised(RuntimeError("boom")), level=DiagnosticLevel.ERROR, operation="catalog.create"
        )

        assert record is None
        assert logs_dir.read_text(encoding="utf-8") == "not a directory"

    def test_colliding_event_id_returns_none_and_keeps_first_file(self, frozen_diagnostic_clock: Path) -> None:
        """Return None for a second event with the same id and leave the first file's content unchanged."""
        record_exception(_raised(RuntimeError("first")), level=DiagnosticLevel.ERROR, operation="catalog.create")
        first_content = frozen_diagnostic_clock.read_text(encoding="utf-8")

        second = record_exception(
            _raised(RuntimeError("second")), level=DiagnosticLevel.ERROR, operation="catalog.delete"
        )

        assert second is None
        assert frozen_diagnostic_clock.read_text(encoding="utf-8") == first_content

    @pytest.mark.parametrize("command", [None, "blueprint"])
    def test_command_is_bound_value_or_null(self, frozen_diagnostic_clock: Path, command: str | None) -> None:
        """Store command null when unbound and "blueprint" while bind_cli_command("blueprint") is active."""
        token = bind_cli_command(command) if command is not None else None
        try:
            record_exception(_raised(RuntimeError("boom")), level=DiagnosticLevel.ERROR, operation="catalog.create")
        finally:
            if token is not None:
                reset_cli_command(token)

        assert _read_event(frozen_diagnostic_clock)["command"] == command


class PruneLogDaysTests:
    def test_keeps_fourteen_newest_day_directories(self, frozen_diagnostic_clock: Path) -> None:
        """Delete the oldest dated directory when a record makes 15, keeping non-date entries and files."""
        logs_dir = frozen_diagnostic_clock.parent.parent
        older_days = [f"2026-10-{day:02d}" for day in range(5, 19)]
        for name in older_days:
            (logs_dir / name).mkdir(parents=True)
        (logs_dir / "notes").mkdir()
        (logs_dir / "README.txt").write_text("keep", encoding="utf-8")

        record_exception(_raised(RuntimeError("boom")), level=DiagnosticLevel.ERROR, operation="catalog.create")

        assert sorted(entry.name for entry in logs_dir.iterdir()) == sorted(
            [*older_days[1:], "2026-10-19", "notes", "README.txt"]
        )

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permission bits")
    def test_undeletable_day_directory_is_skipped(self, tmp_path: Path) -> None:
        """Skip a dated directory that cannot be removed, still delete the other excess days, and not raise."""
        days = [f"2026-10-{day:02d}" for day in range(1, 17)]
        for name in days:
            (tmp_path / name).mkdir()
        locked = tmp_path / days[0]
        (locked / "event.json").write_text("{}", encoding="utf-8")
        locked.chmod(0o500)
        try:
            prune_log_days(tmp_path)
            remaining = sorted(entry.name for entry in tmp_path.iterdir())
        finally:
            locked.chmod(0o700)

        assert remaining == [days[0], *days[2:]]


class AppendDiagnosticHintTests:
    @pytest.mark.parametrize(
        ("level", "has_hint"),
        [
            pytest.param(DiagnosticLevel.ERROR, True, id="error"),
            pytest.param(DiagnosticLevel.DEBUG, False, id="debug"),
            pytest.param(None, False, id="no_record"),
        ],
    )
    def test_appends_hint_only_for_error_records(
        self, tmp_path: Path, level: DiagnosticLevel | None, has_hint: bool
    ) -> None:
        """Append '\nDetails logged: <path> (event <id>)' for an error record and return the message unchanged for debug or None."""
        log_path = tmp_path / "logs" / "2026-10-19" / "exc_3f9a1c07b2de.json"
        record = (
            None if level is None else DiagnosticRecord(event_id="exc_3f9a1c07b2de", log_path=log_path, level=level)
        )
        hint = f"\nDetails logged: {log_path} (event exc_3f9a1c07b2de)" if has_hint else ""

        assert append_diagnostic_hint("disk on fire", record) == f"disk on fire{hint}"


class DiagnosticHintLineTests:
    @pytest.mark.parametrize("level", [DiagnosticLevel.DEBUG, None], ids=["debug", "none"])
    def test_hint_line_names_error_record_and_skips_debug_or_none(
        self, tmp_path: Path, level: DiagnosticLevel | None
    ) -> None:
        """Return 'Details logged: <log_path> (event exc_3f9a1c07b2de)' for an error record and None for a debug record or None."""
        log_path = tmp_path / "logs" / "2026-10-19" / "exc_3f9a1c07b2de.json"
        error_record = DiagnosticRecord(event_id="exc_3f9a1c07b2de", log_path=log_path, level=DiagnosticLevel.ERROR)
        other = None if level is None else DiagnosticRecord(event_id="exc_3f9a1c07b2de", log_path=log_path, level=level)

        assert diagnostic_hint_line(error_record) == f"Details logged: {log_path} (event exc_3f9a1c07b2de)"
        assert diagnostic_hint_line(other) is None
