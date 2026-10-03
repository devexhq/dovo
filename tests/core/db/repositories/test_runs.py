"""Contract tests for RunsRepository project_id scoping."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import Engine

from dovo.core.db.connection import get_engine
from dovo.core.db.migrations import init_database
from dovo.core.db.models import RunStatus
from dovo.core.db.repositories.runs import RunsRepository
from dovo.core.db.repositories.worktrees import WorktreesRepository


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Path to a freshly migrated, isolated SQLite database file."""
    path = tmp_path / "dovo.db"
    init_database(path)
    return path


@pytest.fixture
def db_engine(db_path: Path) -> Engine:
    """Engine bound to the migrated database file, shared across scoped repositories."""
    return get_engine(db_path)


class RunsRepositoryTests:
    """Contract tests proving RunsRepository queries never cross project_id boundaries."""

    def test_list_scopes_to_repository_project_id(self, db_path: Path, db_engine: Engine) -> None:
        """[tier-1/integration] RunsRepository.list: run created under project_id='proj-a' is absent from a project_id='proj-b' repository's list()."""
        repo_a = RunsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo_b = RunsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-b")
        repo_a.create(session_id="wf_a", blueprint_name="deploy", blueprint_key="deploy")
        repo_b.create(session_id="wf_b", blueprint_name="deploy", blueprint_key="deploy")

        assert [record.session_id for record in repo_a.list()] == ["wf_a"]
        assert [record.session_id for record in repo_b.list()] == ["wf_b"]

    def test_get_returns_none_for_run_in_different_project(self, db_path: Path, db_engine: Engine) -> None:
        """[tier-1/integration] RunsRepository.get: run created under project_id='proj-a' returns None when fetched by a project_id='proj-b' repository."""
        repo_a = RunsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo_b = RunsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-b")
        repo_a.create(session_id="wf_a", blueprint_name="deploy", blueprint_key="deploy")

        assert repo_b.get("wf_a") is None
        assert repo_a.get("wf_a") is not None

    def test_create_persists_project_id_on_record(self, db_path: Path, db_engine: Engine) -> None:
        """[tier-1/integration] RunsRepository.create: returned RunRecord.project_id equals the repository's own project_id."""
        repo = RunsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")

        record = repo.create(session_id="wf_a", blueprint_name="deploy", blueprint_key="deploy")

        assert record.project_id == "proj-a"


class WorktreeRunIndependenceTests:
    """[tier-1/integration] Cross-repository contract: worktree deletion never mutates a referencing RunRecord."""

    def test_deleting_worktree_record_does_not_alter_associated_run_record(
        self, db_path: Path, db_engine: Engine, tmp_path: Path
    ) -> None:
        """[tier-1/integration] WorktreesRepository.delete: deleting a worktree row referenced by RunRecord.worktree_id leaves RunsRepository.get(session_id) returning the same, unmodified RunRecord."""
        runs_repo = RunsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        worktrees_repo = WorktreesRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        runs_repo.create(session_id="wf_a", blueprint_name="deploy", blueprint_key="deploy")
        worktrees_repo.create(
            id="dovo_123", branch_name="feature", base_commit="abc123", worktree_path=tmp_path / "dovo_123"
        )
        connection = sqlite3.connect(db_path)
        try:
            connection.execute("UPDATE runs SET worktree_id = ? WHERE session_id = ?", ("dovo_123", "wf_a"))
            connection.commit()
        finally:
            connection.close()
        before = runs_repo.get("wf_a")

        deleted = worktrees_repo.delete("dovo_123")

        assert deleted is True
        assert runs_repo.get("wf_a") == before


class RunsRepositoryConfigTests:
    """Contract tests for run configuration columns written by RunsRepository."""

    def test_create_persists_run_configuration_columns(self, db_path: Path, db_engine: Engine) -> None:
        """[tier-1/integration] RunsRepository.create: keyword arguments blueprint_tier, commit_sha, use_worktree, keep, agent, inputs_json, auto_apply round-trip through RunsRepository.get unchanged."""
        repo = RunsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
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
        """[tier-1/integration] RunsRepository.update_status: worktree_id='dovo_1' leaves get(session_id).worktree_id == 'dovo_1', and omitting it leaves an existing value unchanged."""
        repo = RunsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo.create(session_id="wf_a", blueprint_name="deploy", blueprint_key="deploy")

        repo.update_status("wf_a", RunStatus.PAUSED, worktree_id="dovo_1")
        repo.update_status("wf_a", RunStatus.COMPLETED)

        record = repo.get("wf_a")
        assert record is not None
        assert record.worktree_id == "dovo_1"


class RunsRepositoryExecutionStateTests:
    """Contract tests for the compare-and-swap execution-state write."""

    def test_save_execution_state_matching_revision_writes_json_and_revision(
        self, db_path: Path, db_engine: Engine
    ) -> None:
        """[tier-1/integration] RunsRepository.save_execution_state: expected_revision equal to the row's revision returns the record with the new JSON and next_revision."""
        repo = RunsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo.create(session_id="wf_a", blueprint_name="deploy", blueprint_key="deploy")

        record = repo.save_execution_state(
            "wf_a",
            '{"revision": 1}',
            expected_revision=0,
            next_revision=1,
            status=RunStatus.COMPLETED,
            worktree_id="dovo_1",
        )

        assert record is not None
        assert record.execution_state_json == '{"revision": 1}'
        assert record.execution_state_revision == 1
        assert record.status == RunStatus.COMPLETED
        assert record.completed_at is not None
        assert record.worktree_id == "dovo_1"

    def test_save_execution_state_and_update_status_record_worktree_kept(
        self, db_path: Path, db_engine: Engine
    ) -> None:
        """[tier-1/integration] RunsRepository.save_execution_state / update_status: worktree_kept=True leaves get(session_id).worktree_kept is True, and omitting it on a later call leaves it True."""
        repo = RunsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo.create(session_id="wf_a", blueprint_name="deploy", blueprint_key="deploy")
        repo.create(session_id="wf_b", blueprint_name="deploy", blueprint_key="deploy")

        repo.save_execution_state("wf_a", "{}", expected_revision=0, next_revision=1, worktree_kept=True)
        repo.update_status("wf_a", RunStatus.COMPLETED)
        repo.update_status("wf_b", RunStatus.COMPLETED, worktree_kept=True)
        repo.update_status("wf_b", RunStatus.COMPLETED)

        for session_id in ("wf_a", "wf_b"):
            record = repo.get(session_id)
            assert record is not None
            assert record.worktree_kept is True

    def test_save_execution_state_stale_revision_returns_none_and_leaves_row(
        self, db_path: Path, db_engine: Engine
    ) -> None:
        """[tier-1/integration] RunsRepository.save_execution_state: expected_revision differing from the row's returns None and the row is unchanged."""
        repo = RunsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo.create(session_id="wf_a", blueprint_name="deploy", blueprint_key="deploy")
        before = repo.get("wf_a")

        record = repo.save_execution_state(
            "wf_a", '{"revision": 6}', expected_revision=5, next_revision=6, status=RunStatus.COMPLETED
        )

        assert record is None
        assert repo.get("wf_a") == before
