"""Tier 1 domain tests for the Session and SessionCollection entrypoints."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlmodel import select

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem.models import WorkspacePaths
from dovo.common.lock import LockTimeoutError
from dovo.common.models import Remediation
from dovo.core.db import SessionRecord, SessionsRepository, SessionStatus
from dovo.core.sessions import (
    DiffResult,
    HistoryShowResult,
    LogsShowResult,
    LogStreamFilter,
    Reconciliation,
    Session,
    SessionCollection,
    SessionLogEvent,
    SessionLogEventType,
)
from tests.harness.diagnostic_log import diagnostic_record, read_single_event
from tests.harness.fakes import RecordingStepStateCloser
from tests.harness.lock import time_out_first_lock
from tests.harness.sessions import fail_reconciliation_list
from tests.harness.workspace_paths import initialized_workspace_paths

_NO_SESSIONS_REMEDIATION = Remediation(
    message="Run 'dovo run <blueprint>' to create a session", command="dovo run <blueprint>"
)
_SESSION_LIST_REMEDIATION = Remediation(
    message="Run 'dovo session list' to view past sessions", command="dovo session list"
)
_SESSION_IDS_REMEDIATION = Remediation(
    message="Run 'dovo session list' to list valid session IDs", command="dovo session list"
)


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
    session_log_dir = initialized_workspace_paths(workspace).logs_dir / session.session_id
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


def _deny_reads(monkeypatch: pytest.MonkeyPatch, suffix: str) -> None:
    """Make every Path.read_text of a file with suffix raise PermissionError("denied"), leaving other reads alone."""
    original = Path.read_text

    def _read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self.suffix == suffix:
            raise PermissionError("denied")
        return original(self, *args, **kwargs)  # ty: ignore[invalid-argument-type]  # pass-through of the real call

    monkeypatch.setattr(Path, "read_text", _read_text)


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


def _create(
    db: SessionsRepository, session_id: str, status: SessionStatus = SessionStatus.COMPLETED, pid: int | None = None
) -> SessionRecord:
    """Create a session record for blueprint 'bp' with the given status and pid."""
    return db.create(session_id=session_id, blueprint_name="bp", blueprint_key="bp", status=status, pid=pid)


def _seed_run(db: SessionsRepository, session_id: str, started_at: str) -> SessionRecord:
    """Create a COMPLETED session record and pin its started_at."""
    _create(db, session_id)
    with db.session() as sql_session:
        record = sql_session.exec(select(SessionRecord).where(SessionRecord.session_id == session_id)).one()
        record.started_at = started_at
        sql_session.add(record)
        sql_session.commit()
        sql_session.refresh(record)
        return record


class SessionDiffTests:
    def test_diff_with_run_record_and_patch_returns_success_with_exact_path_and_text(
        self, isolated_workspace: Path
    ) -> None:
        paths = initialized_workspace_paths(isolated_workspace)
        session = Session(paths, "session-626")
        _create(session.db, "session-626")
        patch_text = "diff --git a/file.txt b/file.txt\n-old\n+new\n"
        _write_patch(paths, "session-626", patch_text)

        result = session.diff()

        assert result == DiffResult(
            session_id="session-626",
            artifact_path=paths.session_dir("session-626") / "diff.patch",
            diff_text=patch_text,
        )
        assert result.ok is True

    @pytest.mark.parametrize("patch_text", [pytest.param("", id="empty"), pytest.param("  \n\n", id="whitespace")])
    def test_diff_with_blank_patch_is_a_success_with_empty_text(
        self, isolated_workspace: Path, patch_text: str
    ) -> None:
        paths = initialized_workspace_paths(isolated_workspace)
        session = Session(paths, "session-blank")
        _create(session.db, "session-blank")
        patch_path = _write_patch(paths, "session-blank", patch_text)

        result = session.diff()

        assert result == DiffResult(session_id="session-blank", artifact_path=patch_path, diff_text="")
        assert result.ok is True

    def test_diff_with_session_directory_but_no_run_record_returns_session_not_found(
        self, isolated_workspace: Path
    ) -> None:
        paths = initialized_workspace_paths(isolated_workspace)
        _write_patch(paths, "orphan", "diff --git a/f.txt b/f.txt\n")

        result = Session(paths, "orphan").diff()

        assert result == DiffResult(
            session_id="orphan",
            error_code=ErrorCode.SESSION_NOT_FOUND,
            errors=["Session 'orphan' not found."],
            remediations=[_SESSION_IDS_REMEDIATION],
        )

    def test_diff_without_artifact_returns_diff_not_found_naming_the_artifact_path(
        self, isolated_workspace: Path
    ) -> None:
        paths = initialized_workspace_paths(isolated_workspace)
        session = Session(paths, "session-no-diff")
        _create(session.db, "session-no-diff")
        patch_path = paths.session_dir("session-no-diff") / "diff.patch"

        result = session.diff()

        assert result == DiffResult(
            session_id="session-no-diff",
            error_code=ErrorCode.SESSION_DIFF_NOT_FOUND,
            errors=["Session 'session-no-diff' has no diff artifact."],
            remediations=[Remediation(message=f"Verify the session generated a diff artifact at '{patch_path}'")],
        )

    def test_diff_with_unreadable_artifact_returns_diff_unreadable_and_records_the_os_error(
        self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch, frozen_diagnostic_clock: Path
    ) -> None:
        paths = initialized_workspace_paths(isolated_workspace)
        session = Session(paths, "session-locked")
        _create(session.db, "session-locked")
        patch_path = _write_patch(paths, "session-locked", "diff --git a/f.txt b/f.txt\n")
        _deny_reads(monkeypatch, ".patch")

        result = session.diff()

        assert result == DiffResult(
            session_id="session-locked",
            error_code=ErrorCode.SESSION_DIFF_UNREADABLE,
            errors=[f"Failed to read diff artifact at '{patch_path}': denied"],
            remediations=[Remediation(message="Check file permissions and that the artifact is readable")],
            diagnostics=[diagnostic_record(frozen_diagnostic_clock)],
        )
        assert read_single_event(frozen_diagnostic_clock)["operation"] == "sessions.diff"


class SessionCollectionLatestDiffTests:
    def test_latest_diff_selects_greatest_started_at_not_newest_directory(self, isolated_workspace: Path) -> None:
        paths = initialized_workspace_paths(isolated_workspace)
        sessions = SessionCollection(paths)
        _seed_run(sessions.db, "later-run", "2026-01-02 00:00:00")
        _seed_run(sessions.db, "earlier-run", "2026-01-01 00:00:00")
        _write_patch(paths, "later-run", "diff --git a/later.txt b/later.txt\n")
        _write_patch(paths, "earlier-run", "diff --git a/earlier.txt b/earlier.txt\n")

        result = sessions.latest_diff()

        assert result.error_code is None
        assert result.session_id == "later-run"

    def test_latest_diff_with_equal_started_at_selects_greatest_id(self, isolated_workspace: Path) -> None:
        paths = initialized_workspace_paths(isolated_workspace)
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
        result = SessionCollection(initialized_workspace_paths(isolated_workspace)).latest_diff()

        assert result == DiffResult(
            error_code=ErrorCode.SESSION_NOT_FOUND,
            errors=["No sessions found."],
            remediations=[_NO_SESSIONS_REMEDIATION],
        )


class SessionCollectionLatestTests:
    def test_latest_details_and_logs_resolve_latest_session(self, isolated_workspace: Path) -> None:
        paths = initialized_workspace_paths(isolated_workspace)
        sessions = SessionCollection(paths)
        _seed_run(sessions.db, "later-run", "2026-01-02 00:00:00")
        _seed_run(sessions.db, "earlier-run", "2026-01-01 00:00:00")
        (paths.logs_dir / "later-run").mkdir(parents=True)

        details = sessions.latest_details()
        logs = sessions.latest_logs()

        assert details.error_code is None
        assert details.session_id == "later-run"
        assert logs.error_code is None
        assert logs.session_id == "later-run"

    @pytest.mark.parametrize(
        "resolve",
        [
            pytest.param(lambda sessions: sessions.latest_details(), id="details"),
            pytest.param(lambda sessions: sessions.latest_logs(), id="logs"),
        ],
    )
    def test_latest_methods_without_sessions_return_not_found_with_create_session_fix(
        self,
        isolated_workspace: Path,
        resolve: Callable[[SessionCollection], HistoryShowResult | LogsShowResult],
    ) -> None:
        result = resolve(SessionCollection(initialized_workspace_paths(isolated_workspace)))

        assert result.error_code == ErrorCode.SESSION_NOT_FOUND
        assert result.session_id is None
        assert result.errors == ["No sessions found."]
        assert result.remediations == [_NO_SESSIONS_REMEDIATION]


class SessionLogsTests:
    def test_logs_returns_session_not_found_when_no_run_record_exists(self, isolated_workspace: Path) -> None:
        result = Session(initialized_workspace_paths(isolated_workspace), "ghost").logs()

        assert result == LogsShowResult(
            session_id="ghost",
            error_code=ErrorCode.SESSION_NOT_FOUND,
            errors=["Session 'ghost' not found."],
            remediations=[_SESSION_LIST_REMEDIATION],
        )
        assert result.ok is False

    def test_logs_returns_session_not_found_when_the_log_directory_was_never_created(
        self, isolated_workspace: Path
    ) -> None:
        session = Session(initialized_workspace_paths(isolated_workspace), "no-logs")
        _create(session.db, "no-logs")

        result = session.logs()

        assert result == LogsShowResult(
            session_id="no-logs",
            error_code=ErrorCode.SESSION_NOT_FOUND,
            errors=["No logs found for session 'no-logs'."],
            remediations=[_SESSION_LIST_REMEDIATION],
        )

    def test_logs_default_returns_parsed_run_log_events_in_written_order(self, isolated_workspace: Path) -> None:
        session = Session(initialized_workspace_paths(isolated_workspace), "sess")
        session_log_dir = _seed_session(session, isolated_workspace)
        written = _write_session_log(session_log_dir, ["s1", "s2", "s3"])

        result = session.logs()

        assert result == LogsShowResult(session_id="sess", events=written)

    def test_logs_skips_unparseable_trailing_run_log_line(self, isolated_workspace: Path) -> None:
        session = Session(initialized_workspace_paths(isolated_workspace), "sess")
        session_log_dir = _seed_session(session, isolated_workspace)
        written = _write_session_log(session_log_dir, ["s1", "s2"])
        with (session_log_dir / "session.log").open("a", encoding="utf-8") as session_log:
            session_log.write('{"ts": "2026-01-01T00:00:00+00:00", "event": "step_st')

        result = session.logs()

        assert result.error_code is None
        assert result.events == written

    @pytest.mark.parametrize(
        ("captured", "expected_message"),
        [
            pytest.param(
                ["01_build_attempt_1.stdout.log", "02_lint_attempt_1.stdout.log"],
                "Step 'missing' not found in session 'sess'. Available steps: build, lint.",
                id="captured-steps",
            ),
            pytest.param([], "Step 'missing' not found in session 'sess'. Available steps: none.", id="no-captures"),
        ],
    )
    def test_logs_unknown_step_returns_step_not_found_with_available_steps(
        self, isolated_workspace: Path, captured: list[str], expected_message: str
    ) -> None:
        session = Session(initialized_workspace_paths(isolated_workspace), "sess")
        session_log_dir = _seed_session(session, isolated_workspace)
        for name in captured:
            (session_log_dir / name).write_text("out\n", encoding="utf-8")

        result = session.logs(step="missing")

        assert result == LogsShowResult(
            session_id="sess",
            error_code=ErrorCode.SESSION_STEP_NOT_FOUND,
            errors=[expected_message],
            available_steps=[name.split("_")[1] for name in captured],
        )

    def test_logs_unknown_attempt_returns_attempt_not_found_with_available_attempts(
        self, isolated_workspace: Path
    ) -> None:
        session = Session(initialized_workspace_paths(isolated_workspace), "sess")
        session_log_dir = _seed_session(session, isolated_workspace)
        (session_log_dir / "01_build_attempt_1.stdout.log").write_text("out\n", encoding="utf-8")
        (session_log_dir / "01_build_attempt_3.stdout.log").write_text("out\n", encoding="utf-8")

        result = session.logs(step="build", attempt=2)

        assert result == LogsShowResult(
            session_id="sess",
            error_code=ErrorCode.SESSION_ATTEMPT_NOT_FOUND,
            errors=["Attempt 2 not found for step 'build' in session 'sess'. Available attempts: 1, 3."],
            available_attempts=[1, 3],
        )

    def test_logs_step_tail_returns_only_last_n_lines(self, isolated_workspace: Path) -> None:
        session = Session(initialized_workspace_paths(isolated_workspace), "sess")
        session_log_dir = _seed_session(session, isolated_workspace)
        (session_log_dir / "01_build_attempt_1.stdout.log").write_text("a\nb\nc\n", encoding="utf-8")

        result = session.logs(step="build", stream=LogStreamFilter.STDOUT, tail=1)

        assert result.lines == ["c"]

    def test_logs_run_log_tail_returns_only_last_n_events(self, isolated_workspace: Path) -> None:
        session = Session(initialized_workspace_paths(isolated_workspace), "sess")
        session_log_dir = _seed_session(session, isolated_workspace)
        written = _write_session_log(session_log_dir, ["s1", "s2", "s3"])

        result = session.logs(tail=1)

        assert result.events == [written[-1]]

    def test_logs_step_log_cut_mid_character_returns_replacement_char(self, isolated_workspace: Path) -> None:
        session = Session(initialized_workspace_paths(isolated_workspace), "sess")
        session_log_dir = _seed_session(session, isolated_workspace)
        (session_log_dir / "01_build_attempt_1.stdout.log").write_bytes("ok\n✓".encode()[:-1])

        result = session.logs(step="build", stream=LogStreamFilter.STDOUT)

        assert (result.error_code, result.lines) == (None, ["ok", "�"])

    @pytest.mark.parametrize(
        ("step", "seed_name"),
        [
            pytest.param("build", "01_build_attempt_1.stdout.log", id="step-capture"),
            pytest.param(None, "session.log", id="session-log"),
        ],
    )
    def test_logs_unreadable_log_file_returns_logs_unreadable_and_records_the_os_error(
        self,
        isolated_workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        frozen_diagnostic_clock: Path,
        step: str | None,
        seed_name: str,
    ) -> None:
        session = Session(initialized_workspace_paths(isolated_workspace), "sess")
        session_log_dir = _seed_session(session, isolated_workspace)
        (session_log_dir / seed_name).write_text("out\n", encoding="utf-8")
        _deny_reads(monkeypatch, ".log")

        result = session.logs(step=step)

        assert result == LogsShowResult(
            session_id="sess",
            error_code=ErrorCode.SESSION_LOGS_UNREADABLE,
            errors=[f"Failed reading session logs in '{session_log_dir}': denied"],
            diagnostics=[diagnostic_record(frozen_diagnostic_clock)],
        )
        assert read_single_event(frozen_diagnostic_clock)["operation"] == "sessions.logs"


class SessionCollectionListTests:
    """Integration tests for SessionCollection.list querying and reconciliation."""

    def test_empty_database_returns_success_with_empty_runs(self, isolated_workspace: Path) -> None:
        sessions = SessionCollection(initialized_workspace_paths(isolated_workspace))

        result = sessions.list(closer=RecordingStepStateCloser())

        assert result.error_code is None
        assert result.sessions == []

    def test_unfiltered_list_returns_runs_ordered_newest_first(self, isolated_workspace: Path) -> None:
        sessions = SessionCollection(initialized_workspace_paths(isolated_workspace))
        session_first = _create(sessions.db, "session-001")
        session_second = _create(sessions.db, "session-002", SessionStatus.FAILED)

        result = sessions.list(closer=RecordingStepStateCloser())

        assert result.error_code is None
        assert result.sessions == [_as_expected_record(session_second), _as_expected_record(session_first)]

    def test_limit_parameter_restricts_number_of_returned_runs(self, isolated_workspace: Path) -> None:
        sessions = SessionCollection(initialized_workspace_paths(isolated_workspace))
        _create(sessions.db, "session-001")
        _create(sessions.db, "session-002")
        session_third = _create(sessions.db, "session-003")

        result = sessions.list(closer=RecordingStepStateCloser(), limit=1)

        assert result.error_code is None
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
        sessions = SessionCollection(initialized_workspace_paths(isolated_workspace))
        run_comp = _create(sessions.db, "session-comp")
        run_fail = _create(sessions.db, "session-fail", SessionStatus.FAILED)
        # Using current process PID ensures is_run_stale is False so reconcile_stale_sessions does not flip status to FAILED.
        run_active = _create(sessions.db, "session-run", SessionStatus.RUNNING, pid=os.getpid())
        lookup: dict[str, SessionRecord] = {
            "session-comp": run_comp,
            "session-fail": run_fail,
            "session-run": run_active,
        }

        result = sessions.list(closer=RecordingStepStateCloser(), status=status_arg)

        assert result.error_code is None
        assert result.sessions == [_as_expected_record(lookup[expected_session_id])]

    def test_unknown_status_filter_fallback_passes_raw_string_to_query(self, isolated_workspace: Path) -> None:
        sessions = SessionCollection(initialized_workspace_paths(isolated_workspace))
        _create(sessions.db, "session-comp")

        result = sessions.list(closer=RecordingStepStateCloser(), status="nonexistent_status_value")

        assert result.error_code is None
        assert result.sessions == []

    def test_stale_run_reconciliation_warning_is_captured_in_result(
        self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sessions = SessionCollection(initialized_workspace_paths(isolated_workspace))

        def _mock_reconcile(*_args: object, **_kwargs: object) -> Reconciliation:
            return Reconciliation(warning="Session was terminated abnormally")

        monkeypatch.setattr("dovo.core.sessions.sessions.reconcile_stale_sessions", _mock_reconcile)

        result = sessions.list(closer=RecordingStepStateCloser())

        assert result.error_code is None
        assert result.sessions == []
        assert result.warnings == ["Session was terminated abnormally"]

    def test_list_copies_reconciliation_record(
        self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch, frozen_diagnostic_clock: Path
    ) -> None:
        """With SessionsRepository.list raising only under the reconcile lock, return the sessions, no warning for it and diagnostics [the sessions.reconcile record]."""
        sessions = SessionCollection(initialized_workspace_paths(isolated_workspace))
        session_first = _create(sessions.db, "session-001")
        fail_reconciliation_list(monkeypatch)

        result = sessions.list(closer=RecordingStepStateCloser())

        assert result.error_code is None
        assert result.sessions == [_as_expected_record(session_first)]
        assert result.warnings == []
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        assert read_single_event(frozen_diagnostic_clock)["operation"] == "sessions.reconcile"

    def test_list_reports_an_unexpected_query_failure_as_internal_error(
        self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch, frozen_diagnostic_clock: Path
    ) -> None:
        """With SessionsRepository.list raising RuntimeError("boom") after reconciliation, return INTERNAL_ERROR and its record."""
        sessions = SessionCollection(initialized_workspace_paths(isolated_workspace))

        def _boom(*_args: object, **_kwargs: object) -> list[SessionRecord]:
            raise RuntimeError("boom")

        monkeypatch.setattr(SessionsRepository, "list", _boom)
        monkeypatch.setattr("dovo.core.sessions.sessions.reconcile_stale_sessions", lambda *a, **k: Reconciliation())

        result = sessions.list(closer=RecordingStepStateCloser())

        assert result.error_code == ErrorCode.INTERNAL_ERROR
        assert result.errors == ["Unexpected error during sessions.list: boom"]
        assert result.sessions == []
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        assert read_single_event(frozen_diagnostic_clock)["operation"] == "sessions.list"

    def test_list_propagates_reconciliation_lock_timeout(
        self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Raise LockTimeoutError("lock held") from list()."""
        sessions = SessionCollection(initialized_workspace_paths(isolated_workspace))
        time_out_first_lock(monkeypatch)

        with pytest.raises(LockTimeoutError, match="lock held"):
            sessions.list(closer=RecordingStepStateCloser())


class SessionDetailsTests:
    """Integration tests for Session.details session lookup."""

    def test_collection_get_details_returns_shared_repository_record_or_not_found(
        self, isolated_workspace: Path
    ) -> None:
        sessions = SessionCollection(initialized_workspace_paths(isolated_workspace))
        seeded = _create(sessions.db, "known")

        known = sessions.get("known").details()
        unknown = sessions.get("ghost").details()

        assert known == HistoryShowResult(session_id="known", session=_as_expected_record(seeded))
        assert unknown == HistoryShowResult(
            session_id="ghost",
            error_code=ErrorCode.SESSION_NOT_FOUND,
            errors=["Session 'ghost' not found."],
            remediations=[_SESSION_LIST_REMEDIATION],
        )
        assert (known.ok, unknown.ok) == (True, False)


class SessionDetailsWorktreeOutcomeTests:
    @pytest.mark.parametrize(
        ("use_worktree", "status", "worktree_kept", "expected_kept", "expects_path", "worktree_id"),
        [
            pytest.param(True, SessionStatus.FAILED, True, True, True, "wt-session", id="failed-kept"),
            pytest.param(True, SessionStatus.COMPLETED, False, False, False, "wt-session", id="completed-removed"),
            pytest.param(False, SessionStatus.COMPLETED, False, None, False, "wt-session", id="in-place"),
            pytest.param(True, SessionStatus.RUNNING, False, None, False, "wt-session", id="running"),
            pytest.param(True, SessionStatus.FAILED, False, None, False, None, id="setup-failed-no-worktree"),
        ],
    )
    def test_details_reports_worktree_outcome(
        self,
        isolated_workspace: Path,
        use_worktree: bool,
        status: SessionStatus,
        worktree_kept: bool,
        expected_kept: bool | None,
        expects_path: bool,
        worktree_id: str | None,
    ) -> None:
        paths = initialized_workspace_paths(isolated_workspace)
        session = Session(paths, "wt-session")
        session.db.create(
            session_id="wt-session",
            blueprint_name="bp",
            blueprint_key="bp",
            use_worktree=use_worktree,
        )
        if status != SessionStatus.RUNNING:
            session.db.update_status("wt-session", status, worktree_id=worktree_id, worktree_kept=worktree_kept)

        result = session.details()

        assert result.worktree_kept is expected_kept
        assert result.worktree_path == (paths.worktree_dir("wt-session") if expects_path else None)


class SessionDetailsIncludeLogsTests:
    def _seed_logged_session(self, session: Session, workspace: Path) -> Path:
        """Persist a session record and a log directory with twelve session.log events and one stdout capture."""
        session.db.create(session_id="sess", blueprint_name="bp", blueprint_key="bp", status=SessionStatus.COMPLETED)
        session_log_dir = initialized_workspace_paths(workspace).logs_dir / "sess"
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
        session = Session(initialized_workspace_paths(isolated_workspace), "sess")
        session_log_dir = self._seed_logged_session(session, isolated_workspace)

        result = session.details(include_logs=True)

        assert result.log_files == [
            str(session_log_dir / "01_s1_attempt_1.stdout.log"),
            str(session_log_dir / "session.log"),
        ]
        assert result.log_snippet == [f"[2026-09-26T10:00:{i:02d}+00:00] step_start step_id=s{i}" for i in range(3, 13)]

    def test_details_include_logs_false_default_leaves_log_fields_empty(self, isolated_workspace: Path) -> None:
        session = Session(initialized_workspace_paths(isolated_workspace), "sess")
        self._seed_logged_session(session, isolated_workspace)

        result = session.details()

        assert (result.log_files, result.log_snippet) == ([], [])

    def test_details_include_logs_unreadable_run_log_returns_logs_unreadable_with_the_record_and_its_diagnostic(
        self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch, frozen_diagnostic_clock: Path
    ) -> None:
        session = Session(initialized_workspace_paths(isolated_workspace), "sess")
        session_log_dir = self._seed_logged_session(session, isolated_workspace)
        seeded = session.db.get("sess")
        _deny_reads(monkeypatch, ".log")

        result = session.details(include_logs=True)

        assert seeded is not None
        assert result == HistoryShowResult(
            session_id="sess",
            session=_as_expected_record(seeded),
            error_code=ErrorCode.SESSION_LOGS_UNREADABLE,
            errors=[f"Failed reading session logs in '{session_log_dir}': denied"],
            diagnostics=[diagnostic_record(frozen_diagnostic_clock)],
        )
        assert read_single_event(frozen_diagnostic_clock)["operation"] == "sessions.details"


class SessionHistoryRedactionTests:
    def test_details_and_list_return_masked_error_message(
        self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        collection = SessionCollection(initialized_workspace_paths(isolated_workspace))
        collection.db.create(session_id="old", blueprint_name="bp", blueprint_key="bp", status=SessionStatus.COMPLETED)
        collection.db.update_status("old", SessionStatus.FAILED, error_message="s3cr3t-value")
        monkeypatch.setenv("SVC_TOKEN", "s3cr3t-value")

        details = collection.get("old").details()
        listed = collection.list(closer=RecordingStepStateCloser())

        assert details.session is not None
        assert details.session.error_message == "[REDACTED:SVC_TOKEN]"
        assert [row.error_message for row in listed.sessions] == ["[REDACTED:SVC_TOKEN]"]
        stored = collection.db.get("old")
        assert stored is not None
        assert stored.error_message == "s3cr3t-value"

    @pytest.mark.parametrize(
        ("names", "expected_line", "expected_error"),
        [
            pytest.param(("DB_PIN",), "pin [REDACTED:DB_PIN]", "[REDACTED:DB_PIN]", id="listed"),
            pytest.param((), "pin 1234", "1234", id="unlisted"),
        ],
    )
    def test_logs_details_and_list_mask_listed_name_for_pre_existing_data(
        self,
        isolated_workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        names: tuple[str, ...],
        expected_line: str,
        expected_error: str,
    ) -> None:
        """Mask a listed DB_PIN in step log lines and stored error messages without changing the stored row.

        Return the raw values when sensitive_variables is empty.
        """
        monkeypatch.setenv("DB_PIN", "1234")
        collection = SessionCollection(initialized_workspace_paths(isolated_workspace), sensitive_variables=names)
        session = collection.get("old")
        session_log_dir = _seed_session(session, isolated_workspace)
        (session_log_dir / "01_build_attempt_1.stdout.log").write_text("pin 1234\n", encoding="utf-8")
        collection.db.update_status("old", SessionStatus.FAILED, error_message="1234")

        logs = session.logs(step="build")
        details = session.details()
        listed = collection.list(closer=RecordingStepStateCloser())

        assert logs.lines == [expected_line]
        assert details.session is not None
        assert details.session.error_message == expected_error
        assert [row.error_message for row in listed.sessions] == [expected_error]
        stored = collection.db.get("old")
        assert stored is not None
        assert stored.error_message == "1234"

    def test_logs_return_masked_step_lines(self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SVC_TOKEN", "s3cr3t-value")
        session = Session(initialized_workspace_paths(isolated_workspace), "sess")
        session_log_dir = _seed_session(session, isolated_workspace)
        (session_log_dir / "01_build_attempt_1.stdout.log").write_text("s3cr3t-value\n", encoding="utf-8")

        result = session.logs(step="build")

        assert result.error_code is None
        assert result.lines == ["[REDACTED:SVC_TOKEN]"]

    def test_already_masked_log_line_is_returned_unchanged(
        self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SVC_TOKEN", "s3cr3t-value")
        session = Session(initialized_workspace_paths(isolated_workspace), "sess")
        session_log_dir = _seed_session(session, isolated_workspace)
        (session_log_dir / "01_build_attempt_1.stdout.log").write_text("[REDACTED:SVC_TOKEN]\n", encoding="utf-8")

        result = session.logs(step="build")

        assert result.lines == ["[REDACTED:SVC_TOKEN]"]


def _details_of_missing_row(workspace: Path) -> HistoryShowResult:
    """Return details() for a session id that has no session record."""
    return Session(initialized_workspace_paths(workspace), "ghost").details()


def _logs_of_missing_row(workspace: Path) -> LogsShowResult:
    """Return logs() for a session id that has no session record."""
    return Session(initialized_workspace_paths(workspace), "ghost").logs()


def _logs_of_missing_log_dir(workspace: Path) -> LogsShowResult:
    """Return logs() for a session record whose log directory was never created."""
    session = Session(initialized_workspace_paths(workspace), "ghost")
    _create(session.db, "ghost")
    return session.logs()


class SessionNotFoundRemediationTests:
    """Domain tests pinning the session-list remediation on not-found session results."""

    @pytest.mark.parametrize(
        ("call", "expected_message"),
        [
            pytest.param(_details_of_missing_row, "Session 'ghost' not found.", id="details-missing-row"),
            pytest.param(_logs_of_missing_row, "Session 'ghost' not found.", id="logs-missing-row"),
            pytest.param(_logs_of_missing_log_dir, "No logs found for session 'ghost'.", id="logs-missing-dir"),
        ],
    )
    def test_not_found_results_carry_session_list_remediation(
        self,
        isolated_workspace: Path,
        call: Callable[[Path], HistoryShowResult | LogsShowResult],
        expected_message: str,
    ) -> None:
        """Return SESSION_NOT_FOUND with expected_message and remediations == [Remediation(message="Run 'dovo session list' to view past sessions", command="dovo session list")]."""
        result = call(isolated_workspace)

        assert result.error_code == ErrorCode.SESSION_NOT_FOUND
        assert result.errors == [expected_message]
        assert result.remediations == [_SESSION_LIST_REMEDIATION]
