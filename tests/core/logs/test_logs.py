"""Tier 1 domain tests for the Logs entrypoint coordinator."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from worktree.common.filesystem.models import RepositoryPaths, WorkspacePaths
from worktree.common.filesystem.services.global_root import resolve_global_paths
from worktree.core.db import RunStatus
from worktree.core.logs import Logs, LogsShowStatus, LogStreamFilter, RunLogEvent, RunLogEventType, append_run_log_event
from worktree.core.project.services.storage import resolve_workspace_paths


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(None))


def _seed_session(logs: Logs, workspace: Path, session_id: str) -> Path:
    """Persist a run record and create its session log directory."""
    logs.db.create(session_id=session_id, blueprint_name="bp", blueprint_key="bp", status=RunStatus.COMPLETED)
    session_log_dir = (
        resolve_workspace_paths(RepositoryPaths.from_root(workspace), resolve_global_paths(None)).logs_dir / session_id
    )
    session_log_dir.mkdir(parents=True)
    return session_log_dir


def _write_run_log(session_log_dir: Path, step_ids: list[str]) -> list[RunLogEvent]:
    """Append one STEP_START event per step id via the real writer and return them as parsed from disk."""
    for step_id in step_ids:
        append_run_log_event(session_log_dir, RunLogEvent(event=RunLogEventType.STEP_START, step_id=step_id))
    lines = (session_log_dir / "run.log").read_text(encoding="utf-8").splitlines()
    return [RunLogEvent.model_validate_json(line) for line in lines]


class LogsShowTests:
    """[tier-1/integration] Logs.show: session lookup, run.log parsing, and step log filtering."""

    def test_show_returns_session_not_found_when_no_run_record_exists(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] Logs.show: an unknown session_id returns SESSION_NOT_FOUND with no events or lines."""
        result = Logs(_paths_for(isolated_workspace)).show("ghost")

        assert (result.status, result.session_id, result.events, result.lines) == (
            LogsShowStatus.SESSION_NOT_FOUND,
            "ghost",
            [],
            [],
        )
        assert result.ok is False

    def test_show_default_returns_parsed_run_log_events_in_written_order(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] Logs.show: without filters, events equal every run.log line parsed in file order."""
        logs = Logs(_paths_for(isolated_workspace))
        session_log_dir = _seed_session(logs, isolated_workspace, "sess")
        written = _write_run_log(session_log_dir, ["s1", "s2", "s3"])

        result = logs.show("sess")

        assert result.status == LogsShowStatus.OK
        assert result.events == written
        assert result.lines == []

    def test_show_skips_unparseable_trailing_run_log_line(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] Logs.show: a run.log truncated mid-JSON on its last line yields every well-formed prior event."""
        logs = Logs(_paths_for(isolated_workspace))
        session_log_dir = _seed_session(logs, isolated_workspace, "sess")
        written = _write_run_log(session_log_dir, ["s1", "s2"])
        with (session_log_dir / "run.log").open("a", encoding="utf-8") as run_log:
            run_log.write('{"ts": "2026-01-01T00:00:00+00:00", "event": "step_st')

        result = logs.show("sess")

        assert result.status == LogsShowStatus.OK
        assert result.events == written

    def test_show_step_filter_returns_latest_attempt_stdout_and_stderr_interleaved_by_stream_option(
        self, isolated_workspace: Path
    ) -> None:
        """[tier-1/integration] Logs.show: step='build', stream=BOTH returns only the latest attempt's stdout then stderr lines."""
        logs = Logs(_paths_for(isolated_workspace))
        session_log_dir = _seed_session(logs, isolated_workspace, "sess")
        (session_log_dir / "01_build_attempt_1.stdout.log").write_text("old out\n", encoding="utf-8")
        (session_log_dir / "01_build_attempt_1.stderr.log").write_text("old err\n", encoding="utf-8")
        (session_log_dir / "01_build_attempt_2.stdout.log").write_text("new out\n", encoding="utf-8")
        (session_log_dir / "01_build_attempt_2.stderr.log").write_text("new err\n", encoding="utf-8")

        result = logs.show("sess", step="build", stream=LogStreamFilter.BOTH)

        assert result.status == LogsShowStatus.OK
        assert result.lines == ["new out", "new err"]
        assert result.events == []

    def test_show_unknown_step_returns_step_not_found_with_available_steps(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] Logs.show: step='missing' against logged steps ['build'] returns STEP_NOT_FOUND listing 'build'."""
        logs = Logs(_paths_for(isolated_workspace))
        session_log_dir = _seed_session(logs, isolated_workspace, "sess")
        (session_log_dir / "01_build_attempt_1.stdout.log").write_text("out\n", encoding="utf-8")

        result = logs.show("sess", step="missing")

        assert (result.status, result.available_steps) == (LogsShowStatus.STEP_NOT_FOUND, ["build"])

    def test_show_unknown_attempt_returns_attempt_not_found_with_available_attempts(
        self, isolated_workspace: Path
    ) -> None:
        """[tier-1/integration] Logs.show: step='build', attempt=2 with only attempt 1 on disk returns ATTEMPT_NOT_FOUND listing [1]."""
        logs = Logs(_paths_for(isolated_workspace))
        session_log_dir = _seed_session(logs, isolated_workspace, "sess")
        (session_log_dir / "01_build_attempt_1.stdout.log").write_text("out\n", encoding="utf-8")

        result = logs.show("sess", step="build", attempt=2)

        assert (result.status, result.available_attempts) == (LogsShowStatus.ATTEMPT_NOT_FOUND, [1])

    def test_show_step_tail_returns_only_last_n_lines(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] Logs.show: step='build', tail=1 keeps only the last line of a 3-line stdout log."""
        logs = Logs(_paths_for(isolated_workspace))
        session_log_dir = _seed_session(logs, isolated_workspace, "sess")
        (session_log_dir / "01_build_attempt_1.stdout.log").write_text("a\nb\nc\n", encoding="utf-8")

        result = logs.show("sess", step="build", stream=LogStreamFilter.STDOUT, tail=1)

        assert result.lines == ["c"]

    def test_show_run_log_tail_returns_only_last_n_events(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] Logs.show: tail=1 keeps only the last parsed event of a 3-event run.log."""
        logs = Logs(_paths_for(isolated_workspace))
        session_log_dir = _seed_session(logs, isolated_workspace, "sess")
        written = _write_run_log(session_log_dir, ["s1", "s2", "s3"])

        result = logs.show("sess", tail=1)

        assert result.events == [written[-1]]

    def test_show_step_log_cut_mid_character_returns_replacement_char(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] Logs.show: a capture ending in a truncated UTF-8 sequence reads with U+FFFD instead of raising."""
        logs = Logs(_paths_for(isolated_workspace))
        session_log_dir = _seed_session(logs, isolated_workspace, "sess")
        (session_log_dir / "01_build_attempt_1.stdout.log").write_bytes("ok\n✓".encode()[:-1])

        result = logs.show("sess", step="build", stream=LogStreamFilter.STDOUT)

        assert (result.status, result.lines) == (LogsShowStatus.OK, ["ok", "�"])

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions")
    def test_show_unreadable_step_log_returns_error_result(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] Logs.show: an OSError reading a capture is returned in errors, making the result not ok."""
        logs = Logs(_paths_for(isolated_workspace))
        session_log_dir = _seed_session(logs, isolated_workspace, "sess")
        capture = session_log_dir / "01_build_attempt_1.stdout.log"
        capture.write_text("out\n", encoding="utf-8")
        capture.chmod(0)

        result = logs.show("sess", step="build")

        assert result.ok is False
        assert len(result.errors) == 1
        assert "Permission denied" in result.errors[0]
