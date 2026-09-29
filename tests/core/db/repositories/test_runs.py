"""Contract tests for RunsRepository project_id scoping."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import Engine

from worktree.core.db.connection import get_engine
from worktree.core.db.migrations import init_database
from worktree.core.db.models import RunStatus
from worktree.core.db.repositories.runs import RunsRepository
from worktree.core.db.repositories.sandboxes import SandboxesRepository


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Path to a freshly migrated, isolated SQLite database file."""
    path = tmp_path / "worktree.db"
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


class SandboxRunIndependenceTests:
    """[tier-1/integration] Cross-repository contract: sandbox deletion never mutates a referencing RunRecord."""

    def test_deleting_sandbox_record_does_not_alter_associated_run_record(
        self, db_path: Path, db_engine: Engine, tmp_path: Path
    ) -> None:
        """[tier-1/integration] SandboxesRepository.delete: deleting a sandbox row referenced by RunRecord.sandbox_id leaves RunsRepository.get(session_id) returning the same, unmodified RunRecord."""
        runs_repo = RunsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        sandboxes_repo = SandboxesRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        runs_repo.create(session_id="wf_a", blueprint_name="deploy", blueprint_key="deploy")
        sandboxes_repo.create(
            id="sbx_123", branch_name="feature", base_commit="abc123", sandbox_path=tmp_path / "sbx_123"
        )
        connection = sqlite3.connect(db_path)
        try:
            connection.execute("UPDATE runs SET sandbox_id = ? WHERE session_id = ?", ("sbx_123", "wf_a"))
            connection.commit()
        finally:
            connection.close()
        before = runs_repo.get("wf_a")

        deleted = sandboxes_repo.delete("sbx_123")

        assert deleted is True
        assert runs_repo.get("wf_a") == before


class RunsRepositoryConfigTests:
    """Contract tests for run configuration columns written by RunsRepository."""

    def test_create_persists_run_configuration_columns(self, db_path: Path, db_engine: Engine) -> None:
        """[tier-1/integration] RunsRepository.create: keyword arguments blueprint_tier, commit_sha, use_sandbox, keep, agent, inputs_json, auto_apply round-trip through RunsRepository.get unchanged."""
        repo = RunsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo.create(
            session_id="wf_a",
            blueprint_name="deploy",
            blueprint_key="deploy",
            blueprint_tier="repo",
            commit_sha="abc123",
            use_sandbox=False,
            keep=True,
            agent="claude",
            inputs_json='{"env": "prod"}',
            auto_apply=True,
        )

        record = repo.get("wf_a")

        assert record is not None
        assert record.blueprint_tier == "repo"
        assert record.commit_sha == "abc123"
        assert record.use_sandbox is False
        assert record.keep is True
        assert record.agent == "claude"
        assert record.inputs_json == '{"env": "prod"}'
        assert record.auto_apply is True

    def test_update_status_with_sandbox_id_records_it(self, db_path: Path, db_engine: Engine) -> None:
        """[tier-1/integration] RunsRepository.update_status: sandbox_id='sbx-1' leaves get(session_id).sandbox_id == 'sbx-1', and omitting it leaves an existing value unchanged."""
        repo = RunsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo.create(session_id="wf_a", blueprint_name="deploy", blueprint_key="deploy")

        repo.update_status("wf_a", RunStatus.PAUSED, sandbox_id="sbx-1")
        repo.update_status("wf_a", RunStatus.COMPLETED)

        record = repo.get("wf_a")
        assert record is not None
        assert record.sandbox_id == "sbx-1"


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
            sandbox_id="sbx-1",
        )

        assert record is not None
        assert record.execution_state_json == '{"revision": 1}'
        assert record.execution_state_revision == 1
        assert record.status == RunStatus.COMPLETED
        assert record.completed_at is not None
        assert record.sandbox_id == "sbx-1"

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
