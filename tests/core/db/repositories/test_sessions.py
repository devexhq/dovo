"""Contract tests for SessionsRepository project_id scoping."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TypedDict

import pytest
from sqlalchemy import Engine

from dovo.core.db.models import SessionStatus
from dovo.core.db.repositories.sessions import SessionsRepository
from dovo.core.db.repositories.worktrees import WorktreesRepository


class SessionsRepositoryTests:
    """Contract tests proving SessionsRepository queries never cross project_id boundaries."""

    def test_list_scopes_to_repository_project_id(self, db_path: Path, db_engine: Engine) -> None:
        repo_a = SessionsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo_b = SessionsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-b")
        repo_a.create(session_id="wf_a", blueprint_name="deploy", blueprint_key="deploy")
        repo_b.create(session_id="wf_b", blueprint_name="deploy", blueprint_key="deploy")

        assert [record.session_id for record in repo_a.list()] == ["wf_a"]
        assert [record.session_id for record in repo_b.list()] == ["wf_b"]

    def test_get_returns_none_for_session_in_different_project(self, db_path: Path, db_engine: Engine) -> None:
        repo_a = SessionsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo_b = SessionsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-b")
        repo_a.create(session_id="wf_a", blueprint_name="deploy", blueprint_key="deploy")

        assert repo_b.get("wf_a") is None
        assert repo_a.get("wf_a") is not None

    def test_create_persists_project_id_on_record(self, db_path: Path, db_engine: Engine) -> None:
        repo = SessionsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")

        record = repo.create(session_id="wf_a", blueprint_name="deploy", blueprint_key="deploy")

        assert record.project_id == "proj-a"

    def test_create_duplicate_session_id_raises_value_error_naming_session(
        self, db_path: Path, db_engine: Engine
    ) -> None:
        repo = SessionsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo.create(session_id="wf_a", blueprint_name="deploy", blueprint_key="deploy")

        with pytest.raises(ValueError, match="Session with session_id 'wf_a' already exists or failed constraints"):
            repo.create(session_id="wf_a", blueprint_name="deploy", blueprint_key="deploy")


class SessionsRepositoryLatestTests:
    """Contract tests for SessionsRepository.get_latest."""

    def test_get_latest_orders_by_started_at_then_id_across_all_statuses(
        self, db_path: Path, db_engine: Engine
    ) -> None:
        repo = SessionsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo.create(session_id="wf_old", blueprint_name="deploy", blueprint_key="deploy")
        repo.create(
            session_id="wf_tied_first", blueprint_name="deploy", blueprint_key="deploy", status=SessionStatus.COMPLETED
        )
        repo.create(
            session_id="wf_tied_last", blueprint_name="deploy", blueprint_key="deploy", status=SessionStatus.FAILED
        )
        connection = sqlite3.connect(db_path)
        try:
            connection.execute(
                "UPDATE sessions SET started_at = ? WHERE session_id = ?", ("2026-01-01 00:00:00", "wf_old")
            )
            connection.execute(
                "UPDATE sessions SET started_at = ? WHERE session_id IN (?, ?)",
                ("2026-01-02 00:00:00", "wf_tied_first", "wf_tied_last"),
            )
            connection.commit()
        finally:
            connection.close()

        latest = repo.get_latest()

        assert latest is not None
        assert latest.session_id == "wf_tied_last"

    def test_get_latest_returns_none_when_project_has_no_sessions(self, db_path: Path, db_engine: Engine) -> None:
        repo_a = SessionsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo_b = SessionsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-b")
        repo_a.create(session_id="wf_a", blueprint_name="deploy", blueprint_key="deploy")

        assert repo_b.get_latest() is None


class WorktreeSessionIndependenceTests:
    def test_deleting_worktree_record_does_not_alter_associated_session_record(
        self, db_path: Path, db_engine: Engine, tmp_path: Path
    ) -> None:
        sessions_repo = SessionsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        worktrees_repo = WorktreesRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        sessions_repo.create(session_id="wf_a", blueprint_name="deploy", blueprint_key="deploy")
        worktrees_repo.create(
            id="dovo_123", branch_name="feature", base_commit="abc123", worktree_path=tmp_path / "dovo_123"
        )
        connection = sqlite3.connect(db_path)
        try:
            connection.execute("UPDATE sessions SET worktree_id = ? WHERE session_id = ?", ("dovo_123", "wf_a"))
            connection.commit()
        finally:
            connection.close()
        before = sessions_repo.get("wf_a")

        deleted = worktrees_repo.delete("dovo_123")

        assert deleted is True
        assert sessions_repo.get("wf_a") == before


class SessionsRepositoryConfigTests:
    """Contract tests for run configuration columns written by SessionsRepository."""

    def test_create_persists_run_configuration_columns(self, db_path: Path, db_engine: Engine) -> None:
        repo = SessionsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo.create(
            session_id="wf_a",
            blueprint_name="deploy",
            blueprint_key="deploy",
            blueprint_tier="repo",
            commit_sha="abc123",
            use_worktree=False,
            keep=True,
            agent="claude",
            inputs_json='{"env": "prod"}',
            auto_apply=True,
        )

        record = repo.get("wf_a")

        assert record is not None
        assert record.blueprint_tier == "repo"
        assert record.commit_sha == "abc123"
        assert record.use_worktree is False
        assert record.keep is True
        assert record.agent == "claude"
        assert record.inputs_json == '{"env": "prod"}'
        assert record.auto_apply is True

    def test_update_status_with_worktree_id_records_it(self, db_path: Path, db_engine: Engine) -> None:
        """Leave an existing worktree_id unchanged when update_status omits it."""
        repo = SessionsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo.create(session_id="wf_a", blueprint_name="deploy", blueprint_key="deploy")

        repo.update_status("wf_a", SessionStatus.PAUSED, worktree_id="dovo_1")
        repo.update_status("wf_a", SessionStatus.COMPLETED)

        record = repo.get("wf_a")
        assert record is not None
        assert record.worktree_id == "dovo_1"


class _PidUpdate(TypedDict, total=False):
    """Process-identity keyword arguments forwarded to update_status."""

    pid: int
    pid_started_at: str


class SessionsRepositoryPidTests:
    """Contract tests for the process identity (pid, pid_started_at) written by SessionsRepository."""

    def test_create_persists_pid_started_at(self, db_path: Path, db_engine: Engine) -> None:
        repo = SessionsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo.create(
            session_id="wf_a",
            blueprint_name="deploy",
            blueprint_key="deploy",
            pid=42,
            pid_started_at="2026-10-18T04:04:09.250000+00:00",
        )

        record = repo.get("wf_a")

        assert record is not None
        assert (record.pid, record.pid_started_at) == (42, "2026-10-18T04:04:09.250000+00:00")

    @pytest.mark.parametrize(
        ("update", "expected"),
        [
            pytest.param(
                {"pid": 7, "pid_started_at": "2026-10-18T05:00:00.000000+00:00"},
                (7, "2026-10-18T05:00:00.000000+00:00"),
                id="pid-with-time",
            ),
            pytest.param({"pid": 7}, (7, None), id="pid-clears-time"),
            pytest.param({}, (42, "2026-10-18T04:04:09.250000+00:00"), id="no-pid-keeps-both"),
            pytest.param(
                {"pid_started_at": "2026-10-18T05:00:00.000000+00:00"},
                (42, "2026-10-18T04:04:09.250000+00:00"),
                id="time-without-pid-ignored",
            ),
        ],
    )
    def test_update_status_writes_pid_started_at_only_with_pid(
        self,
        db_path: Path,
        db_engine: Engine,
        update: _PidUpdate,
        expected: tuple[int, str | None],
    ) -> None:
        repo = SessionsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo.create(
            session_id="wf_a",
            blueprint_name="deploy",
            blueprint_key="deploy",
            pid=42,
            pid_started_at="2026-10-18T04:04:09.250000+00:00",
        )

        repo.update_status("wf_a", SessionStatus.RUNNING, **update)

        record = repo.get("wf_a")
        assert record is not None
        assert (record.pid, record.pid_started_at) == expected


class SessionsRepositoryExecutionStateTests:
    """Contract tests for the compare-and-swap execution-state write."""

    def test_save_execution_state_matching_revision_writes_json_and_revision(
        self, db_path: Path, db_engine: Engine
    ) -> None:
        repo = SessionsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo.create(session_id="wf_a", blueprint_name="deploy", blueprint_key="deploy")

        record = repo.save_execution_state(
            "wf_a",
            '{"revision": 1}',
            expected_revision=0,
            next_revision=1,
            status=SessionStatus.COMPLETED,
            worktree_id="dovo_1",
        )

        assert record is not None
        assert record.execution_state_json == '{"revision": 1}'
        assert record.execution_state_revision == 1
        assert record.status == SessionStatus.COMPLETED
        assert record.completed_at is not None
        assert record.worktree_id == "dovo_1"

    def test_save_execution_state_and_update_status_record_worktree_kept(
        self, db_path: Path, db_engine: Engine
    ) -> None:
        """Keep worktree_kept True when a later call omits it."""
        repo = SessionsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo.create(session_id="wf_a", blueprint_name="deploy", blueprint_key="deploy")
        repo.create(session_id="wf_b", blueprint_name="deploy", blueprint_key="deploy")

        repo.save_execution_state("wf_a", "{}", expected_revision=0, next_revision=1, worktree_kept=True)
        repo.update_status("wf_a", SessionStatus.COMPLETED)
        repo.update_status("wf_b", SessionStatus.COMPLETED, worktree_kept=True)
        repo.update_status("wf_b", SessionStatus.COMPLETED)

        for session_id in ("wf_a", "wf_b"):
            record = repo.get(session_id)
            assert record is not None
            assert record.worktree_kept is True

    def test_save_execution_state_stale_revision_returns_none_and_leaves_row(
        self, db_path: Path, db_engine: Engine
    ) -> None:
        repo = SessionsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo.create(session_id="wf_a", blueprint_name="deploy", blueprint_key="deploy")
        before = repo.get("wf_a")

        record = repo.save_execution_state(
            "wf_a", '{"revision": 6}', expected_revision=5, next_revision=6, status=SessionStatus.COMPLETED
        )

        assert record is None
        assert repo.get("wf_a") == before


class SessionsRepositoryRedactionTests:
    @pytest.mark.parametrize("writer", ["update_status", "save_execution_state"])
    def test_error_message_is_stored_masked_by_both_writers(
        self, db_path: Path, db_engine: Engine, monkeypatch: pytest.MonkeyPatch, writer: str
    ) -> None:
        monkeypatch.setenv("SVC_TOKEN", "s3cr3t-value")
        repo = SessionsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo.create(session_id="wf_a", blueprint_name="deploy", blueprint_key="deploy")
        message = "failed with s3cr3t-value"

        if writer == "update_status":
            repo.update_status("wf_a", SessionStatus.FAILED, error_message=message)
        else:
            repo.save_execution_state(
                "wf_a", "{}", expected_revision=0, next_revision=1, status=SessionStatus.FAILED, error_message=message
            )

        stored = repo.get("wf_a")
        assert stored is not None
        assert stored.error_message == "failed with [REDACTED:SVC_TOKEN]"

    def test_error_message_none_is_stored_as_none(self, db_path: Path, db_engine: Engine) -> None:
        repo = SessionsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo.create(session_id="wf_a", blueprint_name="deploy", blueprint_key="deploy")

        repo.update_status("wf_a", SessionStatus.COMPLETED, error_message=None)

        stored = repo.get("wf_a")
        assert stored is not None
        assert stored.error_message is None
