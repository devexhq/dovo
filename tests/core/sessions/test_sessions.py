"""Tier 1 domain tests for the Session and SessionCollection entrypoints."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlmodel import select

from dovo.common.filesystem.models import RepositoryPaths, WorkspacePaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.db import SessionRecord, SessionsRepository, SessionStatus
from dovo.core.project.services.storage import resolve_workspace_paths
from dovo.core.sessions import (
    DiffStatus,
    HistoryListStatus,
    HistoryShowStatus,
    LogsShowStatus,
    LogStreamFilter,
    ReconciliationResult,
    Session,
    SessionCollection,
    SessionLogEvent,
    SessionLogEventType,
)


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(None))


def _write_patch(paths: WorkspacePaths, session_id: str, diff_text: str) -> Path:
    """Persist diff_text as the session's diff.patch artifact and return its path."""
    session_dir = paths.session_dir(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    patch_path = session_dir / "diff.patch"
    patch_path.write_text(diff_text, encoding="utf-8")
    return patch_path


def _seed_session(session: Session, workspace: Path) -> Path:
    """Persist a session record and create its session log directory."""
    session.db.create(
        session_id=session.session_id, blueprint_name="bp", blueprint_key="bp", status=SessionStatus.COMPLETED
    )
    session_log_dir = (
        resolve_workspace_paths(RepositoryPaths.from_root(workspace), resolve_global_paths(None)).logs_dir
        / session.session_id
    )
    session_log_dir.mkdir(parents=True)
    return session_log_dir


def _write_session_log(session_log_dir: Path, step_ids: list[str]) -> list[SessionLogEvent]:
    """Write one timestamped STEP_START event per step id to session.log and return them as parsed from disk."""
    events = [
        SessionLogEvent(ts=f"2026-09-26T10:00:{i:02d}+00:00", event=SessionLogEventType.STEP_START, step_id=step_id)
        for i, step_id in enumerate(step_ids)
    ]
    (session_log_dir / "session.log").write_text("".join(e.model_dump_json() + "\n" for e in events), encoding="utf-8")
    lines = (session_log_dir / "session.log").read_text(encoding="utf-8").splitlines()
    return [SessionLogEvent.model_validate_json(line) for line in lines]


def _as_expected_record(record: SessionRecord) -> SessionRecord:
    """Construct an explicit expected SessionRecord instance with all model fields set."""
    return SessionRecord.model_construct(
        id=record.id,
        project_id=record.project_id,
        session_id=record.session_id,
        blueprint_key=record.blueprint_key,
        blueprint_name=record.blueprint_name,
        branch_name=record.branch_name,
        status=record.status,
        pid=record.pid,
        started_at=record.started_at,
        completed_at=record.completed_at,
        error_message=record.error_message,
    )


def _seed_run(db: SessionsRepository, session_id: str, started_at: str) -> SessionRecord:
    """Create a COMPLETED session record and pin its started_at."""
    db.create(session_id=session_id, blueprint_name="bp", blueprint_key="bp", status=SessionStatus.COMPLETED)
    with db.session() as sql_session:
        record = sql_session.exec(select(SessionRecord).where(SessionRecord.session_id == session_id)).one()
        record.started_at = started_at
        sql_session.add(record)
        sql_session.commit()
        sql_session.refresh(record)
        return record


class SessionDiffTests:
    def test_diff_with_run_record_and_patch_returns_ok_with_exact_path_and_text(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] Session.diff: a session record plus a written diff.patch -> status OK, session_id, diff_text equal to the patch, artifact_path equal to paths.session_dir(id) / 'diff.patch'."""
        paths = _paths_for(isolated_workspace)
        session = Session(paths, "session-626")
        session.db.create(
            session_id="session-626", blueprint_name="bp", blueprint_key="bp", status=SessionStatus.COMPLETED
        )
        patch_text = "diff --git a/file.txt b/file.txt\n-old\n+new\n"
        _write_patch(paths, "session-626", patch_text)

        result = session.diff()

        assert result.status == DiffStatus.OK
        assert result.session_id == "session-626"
        assert result.diff_text == patch_text
        assert result.artifact_path == paths.session_dir("session-626") / "diff.patch"

    def test_diff_with_session_directory_but_no_run_record_returns_session_not_found(
        self, isolated_workspace: Path
    ) -> None:
        """[tier-1/integration] Session.diff: a session directory holding diff.patch but no session record -> SESSION_NOT_FOUND with errors == ["Session '<id>' not found under .dovo/sessions/."] and artifact_path None."""
        paths = _paths_for(isolated_workspace)
        _write_patch(paths, "orphan", "diff --git a/f.txt b/f.txt\n")

        result = Session(paths, "orphan").diff()

        assert result.status == DiffStatus.SESSION_NOT_FOUND
        assert result.errors == ["Session 'orphan' not found under .dovo/sessions/."]
        assert result.artifact_path is None

    def test_diff_with_run_record_but_no_patch_returns_diff_not_found(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] Session.diff: a session record with no diff.patch -> DIFF_NOT_FOUND naming the session (existence passes, artifact read then classifies)."""
        session = Session(_paths_for(isolated_workspace), "session-3")
        session.db.create(
            session_id="session-3", blueprint_name="bp", blueprint_key="bp", status=SessionStatus.COMPLETED
        )

        result = session.diff()

        assert result.status == DiffStatus.DIFF_NOT_FOUND
        assert result.session_id == "session-3"


class SessionCollectionLatestDiffTests:
    def test_latest_diff_selects_greatest_started_at_not_newest_directory(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] SessionCollection.latest_diff: two session records where the earlier started_at has the newer session-directory mtime -> returns the later started_at session's diff."""
        paths = _paths_for(isolated_workspace)
        sessions = SessionCollection(paths)
        _seed_run(sessions.db, "later-run", "2026-01-02 00:00:00")
        _seed_run(sessions.db, "earlier-run", "2026-01-01 00:00:00")
        _write_patch(paths, "later-run", "diff --git a/later.txt b/later.txt\n")
        _write_patch(paths, "earlier-run", "diff --git a/earlier.txt b/earlier.txt\n")

        result = sessions.latest_diff()

        assert result.status == DiffStatus.OK
        assert result.session_id == "later-run"

    def test_latest_diff_with_equal_started_at_selects_greatest_id(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] SessionCollection.latest_diff: two session records with identical started_at -> returns the session whose session record has the greater id."""
        paths = _paths_for(isolated_workspace)
        sessions = SessionCollection(paths)
        first = _seed_run(sessions.db, "zzz-first-created", "2026-01-01 00:00:00")
        second = _seed_run(sessions.db, "aaa-second-created", "2026-01-01 00:00:00")
        _write_patch(paths, "zzz-first-created", "diff --git a/first.txt b/first.txt\n")
        _write_patch(paths, "aaa-second-created", "diff --git a/second.txt b/second.txt\n")

        result = sessions.latest_diff()

        assert second.id is not None
        assert first.id is not None
        assert second.id > first.id
        assert result.session_id == "aaa-second-created"

    def test_latest_diff_with_no_run_records_returns_session_not_found_without_session_id(
        self, isolated_workspace: Path
    ) -> None:
        """[tier-1/integration] SessionCollection.latest_diff: empty sessions table -> SESSION_NOT_FOUND, session_id None, errors == ["No sessions found under .dovo/sessions/."]."""
        result = SessionCollection(_paths_for(isolated_workspace)).latest_diff()

        assert result.status == DiffStatus.SESSION_NOT_FOUND
        assert result.session_id is None
        assert result.errors == ["No sessions found under .dovo/sessions/."]


class SessionCollectionGetTests:
    def test_get_returns_session_bound_to_id_sharing_the_collection_repository(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] SessionCollection.get: a run created through the injected SessionsRepository -> get(id).details() is OK with that run; an unknown id -> details() is NOT_FOUND."""
        sessions = SessionCollection(_paths_for(isolated_workspace))
        seeded = sessions.db.create(
            session_id="known", blueprint_name="bp", blueprint_key="bp", status=SessionStatus.COMPLETED
        )

        known = sessions.get("known").details()
        unknown = sessions.get("ghost").details()

        assert known.status == HistoryShowStatus.OK
        assert known.session == _as_expected_record(seeded)
        assert unknown.status == HistoryShowStatus.NOT_FOUND


class SessionLogsTests:
    """[tier-1/integration] Session.logs: session lookup, session.log parsing, and step log filtering."""

    def test_logs_returns_session_not_found_when_no_run_record_exists(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] Session.logs: an unknown session_id returns SESSION_NOT_FOUND with no events or lines."""
        result = Session(_paths_for(isolated_workspace), "ghost").logs()

        assert (result.status, result.session_id, result.events, result.lines) == (
            LogsShowStatus.SESSION_NOT_FOUND,
            "ghost",
            [],
            [],
        )
        assert result.ok is False

    def test_logs_default_returns_parsed_run_log_events_in_written_order(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] Session.logs: without filters, events equal every session.log line parsed in file order."""
        session = Session(_paths_for(isolated_workspace), "sess")
        session_log_dir = _seed_session(session, isolated_workspace)
        written = _write_session_log(session_log_dir, ["s1", "s2", "s3"])

        result = session.logs()

        assert result.status == LogsShowStatus.OK
        assert result.events == written
        assert result.lines == []

    def test_logs_skips_unparseable_trailing_run_log_line(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] Session.logs: a session.log truncated mid-JSON on its last line yields every well-formed prior event."""
        session = Session(_paths_for(isolated_workspace), "sess")
        session_log_dir = _seed_session(session, isolated_workspace)
        written = _write_session_log(session_log_dir, ["s1", "s2"])
        with (session_log_dir / "session.log").open("a", encoding="utf-8") as session_log:
            session_log.write('{"ts": "2026-01-01T00:00:00+00:00", "event": "step_st')

        result = session.logs()

        assert result.status == LogsShowStatus.OK
        assert result.events == written

    def test_logs_step_filter_returns_latest_attempt_stdout_and_stderr_interleaved_by_stream_option(
        self, isolated_workspace: Path
    ) -> None:
        """[tier-1/integration] Session.logs: step='build', stream=BOTH returns only the latest attempt's stdout then stderr lines."""
        session = Session(_paths_for(isolated_workspace), "sess")
        session_log_dir = _seed_session(session, isolated_workspace)
        (session_log_dir / "01_build_attempt_1.stdout.log").write_text("old out\n", encoding="utf-8")
        (session_log_dir / "01_build_attempt_1.stderr.log").write_text("old err\n", encoding="utf-8")
        (session_log_dir / "01_build_attempt_2.stdout.log").write_text("new out\n", encoding="utf-8")
        (session_log_dir / "01_build_attempt_2.stderr.log").write_text("new err\n", encoding="utf-8")

        result = session.logs(step="build", stream=LogStreamFilter.BOTH)

        assert result.status == LogsShowStatus.OK
        assert result.lines == ["new out", "new err"]
        assert result.events == []

    def test_logs_unknown_step_returns_step_not_found_with_available_steps(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] Session.logs: step='missing' against logged steps ['build'] returns STEP_NOT_FOUND listing 'build'."""
        session = Session(_paths_for(isolated_workspace), "sess")
        session_log_dir = _seed_session(session, isolated_workspace)
        (session_log_dir / "01_build_attempt_1.stdout.log").write_text("out\n", encoding="utf-8")

        result = session.logs(step="missing")

        assert (result.status, result.available_steps) == (LogsShowStatus.STEP_NOT_FOUND, ["build"])

    def test_logs_unknown_attempt_returns_attempt_not_found_with_available_attempts(
        self, isolated_workspace: Path
    ) -> None:
        """[tier-1/integration] Session.logs: step='build', attempt=2 with only attempt 1 on disk returns ATTEMPT_NOT_FOUND listing [1]."""
        session = Session(_paths_for(isolated_workspace), "sess")
        session_log_dir = _seed_session(session, isolated_workspace)
        (session_log_dir / "01_build_attempt_1.stdout.log").write_text("out\n", encoding="utf-8")

        result = session.logs(step="build", attempt=2)

        assert (result.status, result.available_attempts) == (LogsShowStatus.ATTEMPT_NOT_FOUND, [1])

    def test_logs_step_tail_returns_only_last_n_lines(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] Session.logs: step='build', tail=1 keeps only the last line of a 3-line stdout log."""
        session = Session(_paths_for(isolated_workspace), "sess")
        session_log_dir = _seed_session(session, isolated_workspace)
        (session_log_dir / "01_build_attempt_1.stdout.log").write_text("a\nb\nc\n", encoding="utf-8")

        result = session.logs(step="build", stream=LogStreamFilter.STDOUT, tail=1)

        assert result.lines == ["c"]

    def test_logs_run_log_tail_returns_only_last_n_events(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] Session.logs: tail=1 keeps only the last parsed event of a 3-event session.log."""
        session = Session(_paths_for(isolated_workspace), "sess")
        session_log_dir = _seed_session(session, isolated_workspace)
        written = _write_session_log(session_log_dir, ["s1", "s2", "s3"])

        result = session.logs(tail=1)

        assert result.events == [written[-1]]

    def test_logs_step_log_cut_mid_character_returns_replacement_char(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] Session.logs: a capture ending in a truncated UTF-8 sequence reads with U+FFFD instead of raising."""
        session = Session(_paths_for(isolated_workspace), "sess")
        session_log_dir = _seed_session(session, isolated_workspace)
        (session_log_dir / "01_build_attempt_1.stdout.log").write_bytes("ok\n✓".encode()[:-1])

        result = session.logs(step="build", stream=LogStreamFilter.STDOUT)

        assert (result.status, result.lines) == (LogsShowStatus.OK, ["ok", "�"])

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions")
    def test_logs_unreadable_step_log_returns_error_result(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] Session.logs: an OSError reading a capture is returned in errors, making the result not ok."""
        session = Session(_paths_for(isolated_workspace), "sess")
        session_log_dir = _seed_session(session, isolated_workspace)
        capture = session_log_dir / "01_build_attempt_1.stdout.log"
        capture.write_text("out\n", encoding="utf-8")
        capture.chmod(0)

        result = session.logs(step="build")

        assert result.ok is False
        assert len(result.errors) == 1
        assert "Permission denied" in result.errors[0]


class SessionCollectionListTests:
    """Integration tests for SessionCollection.list querying and reconciliation."""

    def test_empty_database_returns_ok_status_with_empty_runs(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] SessionCollection.list: empty sessions table returns HistoryListResult with status=OK, sessions=[], warnings=[]."""
        sessions = SessionCollection(_paths_for(isolated_workspace))

        result = sessions.list()

        assert result.status == HistoryListStatus.OK
        assert result.sessions == []

    def test_unfiltered_list_returns_runs_ordered_with_ok_status(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] SessionCollection.list: returns all sessions up to default limit with status=OK in descending order."""
        sessions = SessionCollection(_paths_for(isolated_workspace))
        session_first = sessions.db.create(
            session_id="session-001",
            blueprint_name="task-alpha",
            blueprint_key="task-alpha",
            status=SessionStatus.COMPLETED,
        )
        session_second = sessions.db.create(
            session_id="session-002",
            blueprint_name="task-beta",
            blueprint_key="task-beta",
            status=SessionStatus.FAILED,
        )

        result = sessions.list()

        assert result.status == HistoryListStatus.OK
        assert result.sessions == [_as_expected_record(session_second), _as_expected_record(session_first)]

    def test_limit_parameter_restricts_number_of_returned_runs(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] SessionCollection.list: limit=N restricts returned sessions to N records."""
        sessions = SessionCollection(_paths_for(isolated_workspace))
        sessions.db.create(
            session_id="session-001",
            blueprint_name="task-alpha",
            blueprint_key="task-alpha",
            status=SessionStatus.COMPLETED,
        )
        sessions.db.create(
            session_id="session-002",
            blueprint_name="task-beta",
            blueprint_key="task-beta",
            status=SessionStatus.COMPLETED,
        )
        session_third = sessions.db.create(
            session_id="session-003",
            blueprint_name="task-gamma",
            blueprint_key="task-gamma",
            status=SessionStatus.COMPLETED,
        )

        result = sessions.list(limit=1)

        assert result.status == HistoryListStatus.OK
        assert result.sessions == [_as_expected_record(session_third)]

    @pytest.mark.parametrize(
        ("status_arg", "expected_session_id"),
        [
            pytest.param("completed", "session-comp", id="lowercase-completed"),
            pytest.param("COMPLETED", "session-comp", id="uppercase-completed"),
            pytest.param("failed", "session-fail", id="lowercase-failed"),
            pytest.param("FAILED", "session-fail", id="uppercase-failed"),
            pytest.param("running", "session-run", id="lowercase-running"),
        ],
    )
    def test_status_filter_filters_matching_runs(
        self, isolated_workspace: Path, status_arg: str, expected_session_id: str
    ) -> None:
        """[tier-1/integration] SessionCollection.list: status filter correctly filters sessions by status enum and case-insensitively."""
        sessions = SessionCollection(_paths_for(isolated_workspace))
        run_comp = sessions.db.create(
            session_id="session-comp",
            blueprint_name="task-comp",
            blueprint_key="task-comp",
            status=SessionStatus.COMPLETED,
        )
        run_fail = sessions.db.create(
            session_id="session-fail",
            blueprint_name="task-fail",
            blueprint_key="task-fail",
            status=SessionStatus.FAILED,
        )
        # Using current process PID ensures is_run_stale is False so reconcile_stale_sessions does not flip status to FAILED.
        run_active = sessions.db.create(
            session_id="session-run",
            blueprint_name="task-run",
            blueprint_key="task-run",
            status=SessionStatus.RUNNING,
            pid=os.getpid(),
        )
        lookup: dict[str, SessionRecord] = {
            "session-comp": run_comp,
            "session-fail": run_fail,
            "session-run": run_active,
        }

        result = sessions.list(status=status_arg)

        assert result.status == HistoryListStatus.OK
        assert result.sessions == [_as_expected_record(lookup[expected_session_id])]

    def test_unknown_status_filter_fallback_passes_raw_string_to_query(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] SessionCollection.list: invalid status string falls back to raw string filter without raising ValueError."""
        sessions = SessionCollection(_paths_for(isolated_workspace))
        sessions.db.create(
            session_id="session-comp",
            blueprint_name="task-comp",
            blueprint_key="task-comp",
            status=SessionStatus.COMPLETED,
        )

        result = sessions.list(status="nonexistent_status_value")

        assert result.status == HistoryListStatus.OK
        assert result.sessions == []

    def test_stale_run_reconciliation_warning_is_captured_in_result(
        self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] SessionCollection.list: reconciliation warning from reconcile_stale_sessions is appended to warnings."""
        sessions = SessionCollection(_paths_for(isolated_workspace))

        def _mock_reconcile(*_args: object, **_kwargs: object) -> ReconciliationResult:
            return ReconciliationResult(reconciled=[], warning="Session was terminated abnormally")

        monkeypatch.setattr("dovo.core.sessions.sessions.reconcile_stale_sessions", _mock_reconcile)

        result = sessions.list()

        assert result.status == HistoryListStatus.OK
        assert result.sessions == []
        assert result.warnings == ["Session was terminated abnormally"]


class SessionDetailsTests:
    """Integration tests for Session.details session lookup."""

    def test_missing_session_id_returns_not_found_status(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] Session.details: missing session_id returns HistoryShowResult with status=NOT_FOUND and session=None."""
        session = Session(_paths_for(isolated_workspace), "nonexistent-session")

        result = session.details()

        assert result.status == HistoryShowStatus.NOT_FOUND
        assert result.session_id == "nonexistent-session"
        assert result.session is None
        assert result.ok is False

    def test_existing_session_id_returns_ok_status_with_run_record(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] Session.details: existing session_id returns HistoryShowResult with status=OK and session record."""
        session = Session(_paths_for(isolated_workspace), "session-alpha")
        seeded_session = session.db.create(
            session_id="session-alpha",
            blueprint_name="deploy-flow",
            blueprint_key="deploy-flow",
            status=SessionStatus.COMPLETED,
        )

        result = session.details()

        assert result.status == HistoryShowStatus.OK
        assert result.session_id == "session-alpha"
        assert result.session == _as_expected_record(seeded_session)


class SessionDetailsIncludeLogsTests:
    """[tier-1/integration] Session.details(include_logs=...): log file listing and session.log snippet."""

    def _seed_logged_session(self, session: Session, workspace: Path) -> Path:
        """Persist a session record and a log directory with twelve session.log events and one stdout capture."""
        session.db.create(session_id="sess", blueprint_name="bp", blueprint_key="bp", status=SessionStatus.COMPLETED)
        session_log_dir = (
            resolve_workspace_paths(RepositoryPaths.from_root(workspace), resolve_global_paths(None)).logs_dir / "sess"
        )
        session_log_dir.mkdir(parents=True)
        events = [
            SessionLogEvent(ts=f"2026-09-26T10:00:{i:02d}+00:00", event=SessionLogEventType.STEP_START, step_id=f"s{i}")
            for i in range(1, 13)
        ]
        (session_log_dir / "session.log").write_text(
            "".join(e.model_dump_json() + "\n" for e in events), encoding="utf-8"
        )
        (session_log_dir / "01_s1_attempt_1.stdout.log").write_text("out\n", encoding="utf-8")
        return session_log_dir

    def test_details_include_logs_true_populates_log_files_and_snippet(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] Session.details: include_logs=True lists every file under logs_dir/<session_id>/ and renders the last 10 session.log events."""
        session = Session(_paths_for(isolated_workspace), "sess")
        session_log_dir = self._seed_logged_session(session, isolated_workspace)

        result = session.details(include_logs=True)

        assert result.log_files == [
            str(session_log_dir / "01_s1_attempt_1.stdout.log"),
            str(session_log_dir / "session.log"),
        ]
        assert result.log_snippet == [f"[2026-09-26T10:00:{i:02d}+00:00] step_start step_id=s{i}" for i in range(3, 13)]

    def test_details_include_logs_false_default_leaves_log_fields_empty(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] Session.details: the default call leaves log_files and log_snippet empty even when logs exist."""
        session = Session(_paths_for(isolated_workspace), "sess")
        self._seed_logged_session(session, isolated_workspace)

        result = session.details()

        assert (result.log_files, result.log_snippet) == ([], [])

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions")
    def test_details_include_logs_unreadable_run_log_returns_error_result(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] Session.details: an OSError reading session.log is returned in errors with empty log fields."""
        session = Session(_paths_for(isolated_workspace), "sess")
        session_log_dir = self._seed_logged_session(session, isolated_workspace)
        (session_log_dir / "session.log").chmod(0)

        result = session.details(include_logs=True)

        assert result.ok is False
        assert (result.log_files, result.log_snippet) == ([], [])
        assert len(result.errors) == 1
        assert "Permission denied" in result.errors[0]
