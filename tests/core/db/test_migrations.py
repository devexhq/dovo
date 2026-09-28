"""Contract tests for the Alembic migration chain."""

from __future__ import annotations

import importlib
import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

import worktree.core.db.migrations as migrations_module
from worktree.core.db.connection import sqlite_url
from worktree.core.db.migrations import init_database

migration_0004 = importlib.import_module("worktree.core.db.alembic.versions.0004_add_execution_state_columns")

_NEW_RUN_COLUMNS = frozenset(
    {
        "execution_state_json",
        "execution_state_revision",
        "blueprint_tier",
        "commit_sha",
        "use_sandbox",
        "keep",
        "agent",
        "inputs_json",
        "auto_apply",
        "sandbox_id",
    }
)


def _insert_run_row(db_path: Path, status: str, checkpoint_json: str | None) -> int:
    """Insert a minimal `runs` row at the 0003 schema and return its id."""
    connection = sqlite3.connect(db_path)
    try:
        cursor = connection.execute(
            "INSERT INTO runs (project_id, session_id, blueprint_key, blueprint_name, status, checkpoint_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("proj-a", "wf_a", "deploy", "deploy", status, checkpoint_json),
        )
        connection.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid
    finally:
        connection.close()


def _alembic_config(db_path: Path) -> Config:
    """Build an Alembic Config bound to an isolated SQLite database file."""
    alembic_cfg = Config()
    alembic_dir = Path(migrations_module.__file__).parent / "alembic"
    alembic_cfg.set_main_option("script_location", str(alembic_dir))
    alembic_cfg.set_main_option("sqlalchemy.url", sqlite_url(db_path))
    return alembic_cfg


class DropCatalogTableMigrationTests:
    """[tier-1/integration] Migration-chain contracts for 0002_drop_catalog_table."""

    def test_upgrade_to_0002_drops_catalog_table(self, tmp_path: Path) -> None:
        db_path = tmp_path / "worktree.db"
        alembic_cfg = _alembic_config(db_path)

        command.upgrade(alembic_cfg, "0001_initial_schema")
        command.upgrade(alembic_cfg, "head")

        connection = sqlite3.connect(db_path)
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            connection.close()

        assert "catalog" not in tables


class AddArtifactsTableMigrationTests:
    """[tier-1/integration] Migration-chain contracts for 0003_add_artifacts_table."""

    def test_upgrade_to_0003_creates_artifacts_table_with_unique_constraint(self, tmp_path: Path) -> None:
        db_path = tmp_path / "worktree.db"
        alembic_cfg = _alembic_config(db_path)

        command.upgrade(alembic_cfg, "head")

        connection = sqlite3.connect(db_path)
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            unique_indexes = [row for row in connection.execute("PRAGMA index_list(artifacts)") if row[2] == 1]
        finally:
            connection.close()

        assert "artifacts" in tables
        assert len(unique_indexes) == 1

    def test_downgrade_from_0003_drops_artifacts_table(self, tmp_path: Path) -> None:
        db_path = tmp_path / "worktree.db"
        alembic_cfg = _alembic_config(db_path)

        command.upgrade(alembic_cfg, "head")
        command.downgrade(alembic_cfg, "0002_drop_catalog_table")

        connection = sqlite3.connect(db_path)
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            connection.close()

        assert "artifacts" not in tables


_COMPLETED_STEP_RESULT: dict[str, object] = {
    "step_id": "step-1",
    "status": "completed",
    "exit_code": 0,
    "stdout": "",
    "stderr": "",
    "duration_seconds": 1.5,
    "attempts": 1,
    "error_message": None,
    "outputs": {},
}

_FAILED_PENDING_RESULT: dict[str, object] = {
    "step_id": "step-2",
    "status": "failed",
    "exit_code": 1,
    "stdout": "",
    "stderr": "boom",
    "duration_seconds": 2.5,
    "attempts": 2,
    "error_message": "boom",
    "outputs": {},
}

_VALID_CHECKPOINT: dict[str, object] = {
    "next_step_index": 1,
    "pending_step_id": "step-2",
    "step_results": [_COMPLETED_STEP_RESULT],
    "pending_result": _FAILED_PENDING_RESULT,
    "use_sandbox": True,
    "keep": False,
    "agent": "claude",
    "inputs": {"branch": "main"},
    "identity": {"blueprint_name": "deploy", "blueprint_key": "deploy-key"},
    "sandbox_id": "sbx_123",
}


class AddExecutionStateColumnsMigrationTests:
    """[tier-1/integration] Migration-chain contracts for 0004_add_execution_state_columns."""

    def test_upgrade_to_0004_adds_all_ten_columns_with_declared_defaults(self, tmp_path: Path) -> None:
        """[tier-1/integration] upgrade to head: `runs` gains all 10 new columns; the boolean and revision columns are NOT NULL/defaulted as declared, the rest nullable without default."""
        db_path = tmp_path / "worktree.db"
        alembic_cfg = _alembic_config(db_path)

        command.upgrade(alembic_cfg, "head")

        connection = sqlite3.connect(db_path)
        try:
            table_info = {row[1]: (row[3], row[4]) for row in connection.execute("PRAGMA table_info(runs)")}
        finally:
            connection.close()

        assert _NEW_RUN_COLUMNS <= table_info.keys()
        assert {name: table_info[name] for name in _NEW_RUN_COLUMNS} == {
            "execution_state_json": (0, None),
            "execution_state_revision": (0, "'0'"),
            "blueprint_tier": (0, None),
            "commit_sha": (0, None),
            "use_sandbox": (1, "1"),
            "keep": (1, "0"),
            "agent": (0, None),
            "inputs_json": (0, None),
            "auto_apply": (1, "1"),
            "sandbox_id": (0, None),
        }

    def test_downgrade_from_0004_drops_all_ten_new_columns(self, tmp_path: Path) -> None:
        """[tier-1/integration] downgrade to 0003_add_artifacts_table: all 10 columns added by 0004 are absent, checkpoint_json remains present."""
        db_path = tmp_path / "worktree.db"
        alembic_cfg = _alembic_config(db_path)
        command.upgrade(alembic_cfg, "head")

        command.downgrade(alembic_cfg, "0003_add_artifacts_table")

        connection = sqlite3.connect(db_path)
        try:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
        finally:
            connection.close()

        assert _NEW_RUN_COLUMNS.isdisjoint(columns)
        assert "checkpoint_json" in columns

    def test_upgrade_on_empty_database_creates_columns_without_error(self, tmp_path: Path) -> None:
        """[tier-1/integration] init_database on a brand-new file: migration chain through head succeeds with all 10 new columns and zero rows to convert."""
        db_path = tmp_path / "worktree.db"

        init_database(db_path)

        connection = sqlite3.connect(db_path)
        try:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
            row_count = connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        finally:
            connection.close()

        assert _NEW_RUN_COLUMNS <= columns
        assert row_count == 0

    @pytest.mark.parametrize(
        ("status", "checkpoint_json"),
        [
            pytest.param("paused", "not json{", id="paused-corrupt-json"),
            pytest.param(
                "paused",
                json.dumps(
                    {"next_step_index": 1, "pending_step_id": "step-2", "step_results": [{"status": "completed"}]}
                ),
                id="paused-step-result-missing-step-id",
            ),
            pytest.param(
                "paused", json.dumps({**_VALID_CHECKPOINT, "identity": "oops"}), id="paused-non-dict-identity"
            ),
            pytest.param("running", json.dumps(_VALID_CHECKPOINT), id="running-valid-checkpoint"),
        ],
    )
    def test_upgrade_leaves_run_untouched_when_not_convertible(
        self, tmp_path: Path, status: str, checkpoint_json: str
    ) -> None:
        """[tier-1/integration] upgrade to head: a run that is not paused or whose checkpoint cannot be converted keeps every new column at its default (execution_state_json NULL, revision 0, scalar columns unset); migration does not raise."""
        db_path = tmp_path / "worktree.db"
        alembic_cfg = _alembic_config(db_path)
        command.upgrade(alembic_cfg, "0003_add_artifacts_table")
        run_id = _insert_run_row(db_path, status=status, checkpoint_json=checkpoint_json)

        command.upgrade(alembic_cfg, "head")

        connection = sqlite3.connect(db_path)
        try:
            row = connection.execute(
                "SELECT execution_state_json, execution_state_revision, agent, sandbox_id, inputs_json, "
                "use_sandbox, keep, auto_apply FROM runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        finally:
            connection.close()

        assert row == (None, 0, None, None, None, 1, 0, 1)

    def test_upgrade_converts_existing_paused_run_checkpoint_into_execution_state_json(self, tmp_path: Path) -> None:
        """[tier-1/integration] upgrade to head: a paused run with a well-formed checkpoint_json gets execution_state_json populated with a valid node tree and execution_state_revision == 1."""
        db_path = tmp_path / "worktree.db"
        alembic_cfg = _alembic_config(db_path)
        command.upgrade(alembic_cfg, "0003_add_artifacts_table")
        run_id = _insert_run_row(db_path, status="paused", checkpoint_json=json.dumps(_VALID_CHECKPOINT))

        command.upgrade(alembic_cfg, "head")

        connection = sqlite3.connect(db_path)
        try:
            row = connection.execute(
                "SELECT execution_state_json, execution_state_revision, sandbox_id, agent FROM runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        finally:
            connection.close()

        execution_state_json, execution_state_revision, sandbox_id, agent = row
        state_tree = json.loads(execution_state_json)
        assert execution_state_revision == 1
        assert sandbox_id == "sbx_123"
        assert agent == "claude"
        assert [node["state"] for node in state_tree["nodes"]] == ["completed", "paused"]


class ConvertCheckpointToStateTreeTests:
    """[tier-1/unit] Contract tests for convert_checkpoint_to_state_tree."""

    def test_valid_checkpoint_returns_scalar_columns_and_state_tree(self) -> None:
        """[tier-1/unit] convert_checkpoint_to_state_tree: one completed step + one pending step with a failed pending_result returns scalar_column_kwargs with sandbox_id/agent/use_sandbox/keep/inputs_json populated and execution_state_json_dict with one completed and one paused kind='step' node, both as exact literal dicts."""
        scalar_column_kwargs, execution_state_json_dict = migration_0004.convert_checkpoint_to_state_tree(
            json.dumps(_VALID_CHECKPOINT)
        )

        assert scalar_column_kwargs == {
            "use_sandbox": True,
            "keep": False,
            "agent": "claude",
            "inputs_json": json.dumps({"branch": "main"}),
            "blueprint_tier": "deploy-key",
            "sandbox_id": "sbx_123",
        }
        assert execution_state_json_dict == {
            "schema_version": 1,
            "revision": 1,
            "manifest": {"blueprint": {"ref": "repo:blueprint:deploy-key", "sha": ""}, "steps": []},
            "nodes": [
                {
                    "kind": "step",
                    "id": "step-1",
                    "state": "completed",
                    "attempts": [
                        {"number": 1, "started_at": None, "completed_at": None, "result": _COMPLETED_STEP_RESULT}
                    ],
                },
                {
                    "kind": "step",
                    "id": "step-2",
                    "state": "paused",
                    "attempts": [
                        {"number": 2, "started_at": None, "completed_at": None, "result": _FAILED_PENDING_RESULT}
                    ],
                },
            ],
        }

    def test_pending_step_without_pending_result_produces_empty_attempts_list(self) -> None:
        """[tier-1/unit] convert_checkpoint_to_state_tree: pending_result=None in the checkpoint yields a paused node with attempts == []."""
        checkpoint = {"next_step_index": 0, "pending_step_id": "step-1", "step_results": [], "pending_result": None}

        _, execution_state_json_dict = migration_0004.convert_checkpoint_to_state_tree(json.dumps(checkpoint))

        assert execution_state_json_dict["nodes"][-1] == {
            "kind": "step",
            "id": "step-1",
            "state": "paused",
            "attempts": [],
        }

    @pytest.mark.parametrize(
        "checkpoint_raw",
        [
            pytest.param("not valid json", id="invalid-json"),
            pytest.param("{}", id="missing-required-keys"),
            pytest.param("[]", id="non-object-json"),
            pytest.param(
                json.dumps({"next_step_index": 1, "pending_step_id": "s", "step_results": [{}]}),
                id="step-result-missing-step-id",
            ),
            pytest.param(
                json.dumps({"next_step_index": 0, "pending_step_id": "s", "identity": "oops"}),
                id="non-dict-identity",
            ),
        ],
    )
    def test_unparseable_checkpoint_returns_empty_dicts(self, checkpoint_raw: str) -> None:
        """[tier-1/unit] convert_checkpoint_to_state_tree: malformed or incomplete checkpoint JSON returns exactly ({}, {})."""
        assert migration_0004.convert_checkpoint_to_state_tree(checkpoint_raw) == ({}, {})


class InitDatabaseTests:
    """[tier-1/integration] init_database: caller-supplied database_file handling."""

    def test_database_file_creates_tables_at_that_exact_location(self, tmp_path: Path) -> None:
        """[tier-1/integration] init_database(database_file): schema is created at the caller-supplied path, and that exact path is returned."""
        explicit_path = tmp_path / "custom" / "nested" / "worktree.db"

        returned_path = init_database(explicit_path)

        assert returned_path == explicit_path
        assert explicit_path.is_file()
        connection = sqlite3.connect(explicit_path)
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            connection.close()
        assert "runs" in tables

    def test_database_file_with_missing_parent_directories_creates_them(self, tmp_path: Path) -> None:
        """[tier-1/integration] init_database(database_file): parent directories that do not yet exist are created before the database file is written."""
        explicit_path = tmp_path / "does" / "not" / "exist" / "yet" / "worktree.db"
        assert not explicit_path.parent.exists()

        init_database(explicit_path)

        assert explicit_path.is_file()
