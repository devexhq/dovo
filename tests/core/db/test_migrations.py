"""Contract tests for the Alembic migration chain."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

import dovo.core.db.migrations as migrations
from dovo.core.db.connection import sqlite_url
from dovo.core.db.migrations import (
    ARTIFACT_REVISIONS_REVISION,
    INITIAL_SCHEMA_REVISION,
    LATEST_SCHEMA_REVISION,
    SESSIONS_RENAME_REVISION,
    init_database,
)
from dovo.core.db.repositories.sessions import SessionsRepository


def _alembic_config(database_file: Path) -> Config:
    """Build the Alembic Config init_database uses, so tests can stop at an intermediate revision."""
    config = Config()
    config.set_main_option("script_location", str(Path(migrations.__file__).parent / "alembic"))
    config.set_main_option("sqlalchemy.url", sqlite_url(database_file))
    return config


def _insert_run_row(connection: sqlite3.Connection, session_id: str) -> None:
    """Insert one runs row into a database at the 0001 schema."""
    connection.execute(
        "INSERT INTO runs (project_id, session_id, blueprint_key, blueprint_name, status) "
        "VALUES ('proj', ?, 'bp', 'Blueprint', 'completed')",
        (session_id,),
    )


def _schema_names(database_file: Path, table: str) -> tuple[set[str], set[str], str]:
    """Return the database's table names, the named indexes on table, and table's DDL."""
    connection = sqlite3.connect(database_file)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?",
                (table,),
            )
        }
        ddl = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
    finally:
        connection.close()
    return tables, indexes, ddl


class InitDatabaseTests:
    """[tier-1/integration] init_database: caller-supplied database_file handling."""

    def test_database_file_creates_tables_at_that_exact_location(self, tmp_path: Path) -> None:
        """[tier-1/integration] init_database(database_file): schema is created at the caller-supplied path, and that exact path is returned."""
        explicit_path = tmp_path / "custom" / "nested" / "dovo.db"

        returned_path = init_database(explicit_path)

        assert returned_path == explicit_path
        assert explicit_path.is_file()
        connection = sqlite3.connect(explicit_path)
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            connection.close()
        assert "sessions" in tables

    def test_database_file_with_missing_parent_directories_creates_them(self, tmp_path: Path) -> None:
        """[tier-1/integration] init_database(database_file): parent directories that do not yet exist are created before the database file is written."""
        explicit_path = tmp_path / "does" / "not" / "exist" / "yet" / "dovo.db"
        assert not explicit_path.parent.exists()

        init_database(explicit_path)

        assert explicit_path.is_file()


class SessionsMigrationTests:
    """[tier-1/integration] 0002_rename_runs_to_sessions: table, index, and constraint rename."""

    def test_upgrade_from_initial_schema_preserves_rows_and_column_definitions(self, tmp_path: Path) -> None:
        """[tier-1/integration] init_database: upgrading a 0001_initial_schema database holding two runs rows leaves SELECT * FROM sessions ORDER BY id equal to the pre-upgrade SELECT * FROM runs ORDER BY id plus the later discard column, PRAGMA table_info(sessions) equal to the pre-upgrade PRAGMA table_info(runs) plus the later discard column, a unique index on session_id, and SessionsRepository.list() returning both session_ids."""
        database_file = tmp_path / "dovo.db"
        command.upgrade(_alembic_config(database_file), INITIAL_SCHEMA_REVISION)
        connection = sqlite3.connect(database_file)
        try:
            _insert_run_row(connection, "wf_a")
            _insert_run_row(connection, "wf_b")
            connection.commit()
            rows_before = connection.execute("SELECT * FROM runs ORDER BY id").fetchall()
            columns_before = connection.execute("PRAGMA table_info(runs)").fetchall()
        finally:
            connection.close()

        init_database(database_file)

        connection = sqlite3.connect(database_file)
        try:
            assert [row[:-1] for row in connection.execute("SELECT * FROM sessions ORDER BY id")] == rows_before
            columns_after = connection.execute("PRAGMA table_info(sessions)").fetchall()
            assert [column[1] for column in columns_after][-1] == "discard"
            assert columns_after[:-1] == columns_before
            unique_columns = [
                connection.execute(f"PRAGMA index_info({index[1]})").fetchall()[0][2]
                for index in connection.execute("PRAGMA index_list(sessions)").fetchall()
                if index[2]
            ]
        finally:
            connection.close()
        assert unique_columns == ["session_id"]
        listed = SessionsRepository(db_path=database_file, project_id="proj").list()
        assert sorted(record.session_id for record in listed) == ["wf_a", "wf_b"]

    def test_fresh_database_names_sessions_table_indexes_and_constraint(self, tmp_path: Path) -> None:
        """[tier-1/integration] init_database: a new database has tables exactly {alembic_version, artifacts, costs, sessions, worktrees}, sessions indexes exactly {idx_sessions_project_id, idx_sessions_started, idx_sessions_status} plus the session_id unique autoindex, DDL naming ck_sessions_status, and alembic_version equal to LATEST_SCHEMA_REVISION."""
        database_file = init_database(tmp_path / "dovo.db")

        tables, indexes, ddl = _schema_names(database_file, "sessions")
        connection = sqlite3.connect(database_file)
        try:
            version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        finally:
            connection.close()

        assert tables == {"alembic_version", "artifacts", "costs", "sessions", "worktrees"}
        assert {name for name in indexes if not name.startswith("sqlite_autoindex")} == {
            "idx_sessions_project_id",
            "idx_sessions_started",
            "idx_sessions_status",
        }
        assert "ck_sessions_status" in ddl
        assert version == LATEST_SCHEMA_REVISION

    def test_sessions_status_check_rejects_unknown_status(self, tmp_path: Path) -> None:
        """[tier-1/integration] init_database: inserting status='bogus' into sessions through sqlite3 raises sqlite3.IntegrityError after the constraint recreate."""
        database_file = init_database(tmp_path / "dovo.db")

        connection = sqlite3.connect(database_file)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO sessions (project_id, session_id, blueprint_key, blueprint_name, status) "
                    "VALUES ('proj', 'wf_bogus', 'bp', 'Blueprint', 'bogus')"
                )
        finally:
            connection.close()

    def test_downgrade_restores_runs_names_and_keeps_rows_written_after_upgrade(self, tmp_path: Path) -> None:
        """[tier-1/integration] alembic downgrade to INITIAL_SCHEMA_REVISION: a row created through SessionsRepository.create survives in table runs, indexes are exactly {idx_runs_project_id, idx_runs_started, idx_runs_status} plus the unique autoindex, DDL names ck_runs_status, and no sessions table remains."""
        database_file = init_database(tmp_path / "dovo.db")
        SessionsRepository(db_path=database_file, project_id="proj").create(
            "wf_kept", blueprint_name="Blueprint", blueprint_key="bp"
        )

        command.downgrade(_alembic_config(database_file), INITIAL_SCHEMA_REVISION)

        tables, indexes, ddl = _schema_names(database_file, "runs")
        connection = sqlite3.connect(database_file)
        try:
            kept = [row[0] for row in connection.execute("SELECT session_id FROM runs")]
        finally:
            connection.close()
        assert "sessions" not in tables
        assert kept == ["wf_kept"]
        assert {name for name in indexes if not name.startswith("sqlite_autoindex")} == {
            "idx_runs_project_id",
            "idx_runs_started",
            "idx_runs_status",
        }
        assert any(name.startswith("sqlite_autoindex") for name in indexes)
        assert "ck_runs_status" in ddl

    def test_second_init_database_is_noop(self, tmp_path: Path) -> None:
        """[tier-1/integration] init_database: a second call on a head database leaves the sessions rows and alembic_version unchanged."""
        database_file = init_database(tmp_path / "dovo.db")
        SessionsRepository(db_path=database_file, project_id="proj").create(
            "wf_once", blueprint_name="Blueprint", blueprint_key="bp"
        )

        def snapshot() -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
            connection = sqlite3.connect(database_file)
            try:
                return (
                    connection.execute("SELECT * FROM sessions ORDER BY id").fetchall(),
                    connection.execute("SELECT * FROM alembic_version").fetchall(),
                )
            finally:
                connection.close()

        before = snapshot()

        init_database(database_file)

        assert snapshot() == before


def _insert_artifact_row(connection: sqlite3.Connection, session_id: str, name: str, *, size_bytes: int = 7) -> None:
    """Insert one artifacts row into a database at the 0002 schema."""
    connection.execute(
        "INSERT INTO artifacts (project_id, session_id, name, path, size_bytes, file_count, expires_at) "
        "VALUES ('proj', ?, ?, ?, ?, 1, '2030-01-01 00:00:00')",
        (session_id, name, f"/artifacts/{session_id}/{name}", size_bytes),
    )


class ArtifactRevisionsMigrationTests:
    """[tier-1/integration] 0003_artifact_revisions: revision, digest and provenance columns."""

    def test_upgrade_from_sessions_rename_backfills_revision_one_and_preserves_rows(self, tmp_path: Path) -> None:
        """[tier-1/integration] init_database: a 0002 database with two artifacts rows upgrades so revision, digest, step_id, attempt, loop_iteration are (1, None, None, None, None) for both and every pre-existing column value is unchanged."""
        database_file = tmp_path / "dovo.db"
        command.upgrade(_alembic_config(database_file), SESSIONS_RENAME_REVISION)
        connection = sqlite3.connect(database_file)
        try:
            _insert_artifact_row(connection, "wf_a", "plan")
            _insert_artifact_row(connection, "wf_b", "dist", size_bytes=9)
            connection.commit()
            rows_before = connection.execute(
                "SELECT id, project_id, session_id, name, path, size_bytes, file_count, created_at, expires_at "
                "FROM artifacts ORDER BY id"
            ).fetchall()
        finally:
            connection.close()

        init_database(database_file)

        connection = sqlite3.connect(database_file)
        try:
            rows_after = connection.execute(
                "SELECT id, project_id, session_id, name, path, size_bytes, file_count, created_at, expires_at "
                "FROM artifacts ORDER BY id"
            ).fetchall()
            new_columns = connection.execute(
                "SELECT revision, digest, step_id, attempt, loop_iteration FROM artifacts ORDER BY id"
            ).fetchall()
        finally:
            connection.close()
        assert rows_after == rows_before
        assert new_columns == [(1, None, None, None, None), (1, None, None, None, None)]

    def test_upgraded_unique_constraint_allows_second_revision_and_rejects_duplicate(self, tmp_path: Path) -> None:
        """[tier-1/integration] init_database: after upgrade, inserting revision=2 of an existing name succeeds and inserting revision=1 again raises sqlite3.IntegrityError."""
        database_file = init_database(tmp_path / "dovo.db")
        connection = sqlite3.connect(database_file)
        try:
            _insert_artifact_row(connection, "wf_a", "plan")
            connection.execute(
                "INSERT INTO artifacts (project_id, session_id, name, path, revision) "
                "VALUES ('proj', 'wf_a', 'plan', '/artifacts/wf_a/plan/2', 2)"
            )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO artifacts (project_id, session_id, name, path, revision) "
                    "VALUES ('proj', 'wf_a', 'plan', '/artifacts/wf_a/plan/dup', 1)"
                )
        finally:
            connection.close()

    def test_downgrade_keeps_highest_revision_per_name_and_restores_old_constraint(self, tmp_path: Path) -> None:
        """[tier-1/integration] alembic downgrade to 0002: revisions 1 and 2 of 'plan' collapse to the revision-2 row, other names keep their row, and a second row for the same (project, session, name) raises sqlite3.IntegrityError."""
        database_file = init_database(tmp_path / "dovo.db")
        connection = sqlite3.connect(database_file)
        try:
            for revision in (1, 2):
                connection.execute(
                    "INSERT INTO artifacts (project_id, session_id, name, path, revision) "
                    "VALUES ('proj', 'wf_a', 'plan', ?, ?)",
                    (f"/artifacts/wf_a/plan/{revision}", revision),
                )
            connection.execute(
                "INSERT INTO artifacts (project_id, session_id, name, path, revision) "
                "VALUES ('proj', 'wf_a', 'dist', '/artifacts/wf_a/dist/1', 1)"
            )
            connection.commit()
        finally:
            connection.close()

        command.downgrade(_alembic_config(database_file), SESSIONS_RENAME_REVISION)

        connection = sqlite3.connect(database_file)
        try:
            kept = connection.execute("SELECT name, path FROM artifacts ORDER BY name").fetchall()
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO artifacts (project_id, session_id, name, path) "
                    "VALUES ('proj', 'wf_a', 'plan', '/artifacts/wf_a/plan/again')"
                )
        finally:
            connection.close()
        assert kept == [("dist", "/artifacts/wf_a/dist/1"), ("plan", "/artifacts/wf_a/plan/2")]


def _insert_session_row(connection: sqlite3.Connection, session_id: str) -> None:
    """Insert one sessions row through raw SQL, leaving every optional column at its schema default."""
    connection.execute(
        "INSERT INTO sessions (project_id, session_id, blueprint_key, blueprint_name, status) "
        "VALUES ('proj', ?, 'bp', 'Blueprint', 'completed')",
        (session_id,),
    )


class SessionDiscardMigrationTests:
    """[tier-1/integration] 0004_session_discard: the sessions.discard column."""

    def test_upgrade_adds_discard_column_defaulting_existing_rows_false(self, tmp_path: Path) -> None:
        """[tier-1/integration] upgrade 0003 -> 0004: sessions gains column discard and every pre-existing row reads discard == 0 with its other columns unchanged."""
        database_file = tmp_path / "dovo.db"
        command.upgrade(_alembic_config(database_file), ARTIFACT_REVISIONS_REVISION)
        connection = sqlite3.connect(database_file)
        try:
            _insert_session_row(connection, "wf_a")
            _insert_session_row(connection, "wf_b")
            connection.commit()
            rows_before = connection.execute(
                "SELECT id, project_id, session_id, blueprint_key, blueprint_name, status, keep FROM sessions ORDER BY id"
            ).fetchall()
        finally:
            connection.close()

        init_database(database_file)

        connection = sqlite3.connect(database_file)
        try:
            rows_after = connection.execute(
                "SELECT id, project_id, session_id, blueprint_key, blueprint_name, status, keep FROM sessions ORDER BY id"
            ).fetchall()
            discards = connection.execute("SELECT discard FROM sessions ORDER BY id").fetchall()
        finally:
            connection.close()
        assert rows_after == rows_before
        assert discards == [(0,), (0,)]

    def test_downgrade_drops_discard_column_and_keeps_rows(self, tmp_path: Path) -> None:
        """[tier-1/integration] downgrade 0004 -> 0003: sessions has no discard column and rows written after the upgrade are still present."""
        database_file = init_database(tmp_path / "dovo.db")
        connection = sqlite3.connect(database_file)
        try:
            _insert_session_row(connection, "wf_a")
            connection.commit()
        finally:
            connection.close()

        command.downgrade(_alembic_config(database_file), ARTIFACT_REVISIONS_REVISION)

        connection = sqlite3.connect(database_file)
        try:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(sessions)")}
            session_ids = connection.execute("SELECT session_id FROM sessions").fetchall()
        finally:
            connection.close()
        assert "discard" not in columns
        assert session_ids == [("wf_a",)]
