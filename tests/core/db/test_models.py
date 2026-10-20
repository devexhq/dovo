"""Contract tests for centralized database record models."""

from __future__ import annotations

import ast
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.dialects import sqlite
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select

import dovo.core.db.models as models_module
from dovo.core.db.connection import get_engine
from dovo.core.db.models import (
    ArtifactRecord,
    CostRecord,
    SessionRecord,
    SessionStatus,
    StrEnumType,
    WorktreeRecord,
    WorktreeStatus,
)
from dovo.core.db.repositories import ArtifactsRepository, SessionsRepository, WorktreesRepository
from tests.harness.builders import init_test_database

RecordClass = type[SessionRecord] | type[WorktreeRecord] | type[CostRecord] | type[ArtifactRecord]


@pytest.fixture
def migrated_engine(tmp_path: Path) -> Engine:
    """Engine bound to a freshly migrated, isolated SQLite database file."""
    db_path = tmp_path / "dovo.db"
    init_test_database(db_path)
    return get_engine(db_path)


class DbRecordModelTests:
    """Contract tests pinning project_id as a required, NOT NULL column on every centralized record."""

    @pytest.mark.parametrize(
        ("record_cls", "expected_tablename"),
        [
            pytest.param(SessionRecord, "sessions", id="session_record"),
            pytest.param(WorktreeRecord, "worktrees", id="worktree_record"),
            pytest.param(CostRecord, "costs", id="cost_record"),
            pytest.param(ArtifactRecord, "artifacts", id="artifact_record"),
        ],
    )
    def test_record_declares_project_id_as_required_field(
        self, record_cls: RecordClass, expected_tablename: str
    ) -> None:
        assert record_cls.__tablename__ == expected_tablename
        assert record_cls.model_fields["project_id"].is_required()

    @pytest.mark.parametrize(
        "record_factory",
        [
            pytest.param(
                lambda: SessionRecord(  # ty: ignore[missing-argument]  # intentional: omitted project_id is this test's subject
                    session_id="wf_abc123", blueprint_name="deploy", blueprint_key="deploy"
                ),
                id="session_record",
            ),
            pytest.param(
                lambda: WorktreeRecord(  # ty: ignore[missing-argument]  # intentional: omitted project_id is this test's subject
                    id="dovo_abc123",
                    branch_name="dovo/dovo_abc123",
                    base_commit="deadbeef",
                    worktree_path="/tmp/dovo_abc123",
                ),
                id="worktree_record",
            ),
            pytest.param(
                lambda: CostRecord(  # ty: ignore[missing-argument]  # intentional: omitted project_id is this test's subject
                    session_id="wf_abc123", branch_name="deploy", model_id="claude-sonnet-5"
                ),
                id="cost_record",
            ),
            pytest.param(
                lambda: ArtifactRecord(  # ty: ignore[missing-argument]  # intentional: omitted project_id is this test's subject
                    session_id="wf_abc123", name="dist", path="/tmp/artifacts/wf_abc123/dist"
                ),
                id="artifact_record",
            ),
        ],
    )
    def test_record_missing_project_id_violates_not_null_constraint(
        self,
        migrated_engine: Engine,
        record_factory: Callable[[], SQLModel],
    ) -> None:
        record = record_factory()
        with Session(migrated_engine) as session:
            session.add(record)
            with pytest.raises(IntegrityError):
                session.commit()


class SessionRecordDefaultsTests:
    """Contract tests for SessionRecord column defaults."""

    def test_session_record_without_auto_apply_defaults_false(self, migrated_engine: Engine) -> None:
        with Session(migrated_engine) as session:
            session.add(
                SessionRecord(project_id="proj-a", session_id="wf_a", blueprint_name="deploy", blueprint_key="deploy")
            )
            session.commit()

        with Session(migrated_engine) as session:
            record = session.exec(select(SessionRecord)).one()
            assert record.auto_apply is False


_TIMESTAMP = "2026-10-19 12:00:00"


def _worktree(worktree_path: Path | str, status: WorktreeStatus | str) -> WorktreeRecord:
    return WorktreeRecord(
        id="dovo_abc123",
        project_id="proj-a",
        branch_name="dovo/dovo_abc123",
        base_commit="deadbeef",
        worktree_path=worktree_path,
        status=status,
        created_at=_TIMESTAMP,
        updated_at=_TIMESTAMP,
    )


def _session(status: SessionStatus | str) -> SessionRecord:
    return SessionRecord(
        project_id="proj-a",
        session_id="wf_abc123",
        blueprint_key="deploy",
        blueprint_name="deploy",
        status=status,
        started_at=_TIMESTAMP,
    )


def _artifact(path: Path | str) -> ArtifactRecord:
    return ArtifactRecord(project_id="proj-a", session_id="wf_abc123", name="dist", path=path, created_at=_TIMESTAMP)


class StrEnumTypeTests:
    """Contract tests for the generic StrEnum column type."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            pytest.param(WorktreeStatus.MERGED, "merged", id="member"),
            pytest.param("merged", "merged", id="str"),
            pytest.param(None, None, id="none"),
        ],
    )
    def test_bind_param_stores_string_value(self, value: WorktreeStatus | str | None, expected: str | None) -> None:
        bound = StrEnumType(WorktreeStatus).process_bind_param(value, sqlite.dialect())

        assert bound == expected
        assert bound is None or type(bound) is str

    @pytest.mark.parametrize(
        ("enum_cls", "stored", "expected"),
        [
            pytest.param(WorktreeStatus, "conflict", WorktreeStatus.CONFLICT, id="worktree"),
            pytest.param(SessionStatus, "paused", SessionStatus.PAUSED, id="session"),
            pytest.param(SessionStatus, None, None, id="none"),
        ],
    )
    def test_result_value_loads_enum_member(
        self, enum_cls: type[StrEnum], stored: str | None, expected: StrEnum | None
    ) -> None:
        assert StrEnumType(enum_cls).process_result_value(stored, sqlite.dialect()) is expected

    def test_result_value_rejects_non_member(self) -> None:
        with pytest.raises(ValueError, match="not-a-status"):
            StrEnumType(WorktreeStatus).process_result_value("not-a-status", sqlite.dialect())


class RecordCoercionTests:
    """Contract tests for str coercion of Path and StrEnum fields when a record is constructed."""

    @pytest.mark.parametrize(
        ("record_factory", "field", "expected"),
        [
            pytest.param(lambda: _worktree("/tmp/wt", "active"), "worktree_path", Path("/tmp/wt"), id="worktree_path"),
            pytest.param(lambda: _worktree("/tmp/wt", "merged"), "status", WorktreeStatus.MERGED, id="worktree"),
            pytest.param(lambda: _session("running"), "status", SessionStatus.RUNNING, id="session"),
            pytest.param(lambda: _artifact("/tmp/art"), "path", Path("/tmp/art"), id="artifact"),
        ],
    )
    def test_string_fields_coerce_to_declared_types(
        self, record_factory: Callable[[], SQLModel], field: str, expected: object
    ) -> None:
        value = getattr(record_factory(), field)

        assert value == expected
        assert type(value) is type(expected)

    def test_typed_values_are_stored_as_given(self) -> None:
        path = Path("/tmp/wt")
        status = WorktreeStatus.CONFLICT

        record = _worktree(path, status)

        assert record.worktree_path is path
        assert record.status is status

    @pytest.mark.parametrize(
        "record_factory",
        [
            pytest.param(lambda: _worktree("/tmp/wt", "not-a-status"), id="worktree"),
            pytest.param(lambda: _session("not-a-status"), id="session"),
        ],
    )
    def test_unknown_status_string_raises_value_error(self, record_factory: Callable[[], SQLModel]) -> None:
        with pytest.raises(ValueError, match="not-a-status"):
            record_factory()

    def test_model_validate_yields_declared_types(self) -> None:
        record = SessionRecord.model_validate(
            {
                "project_id": "proj-a",
                "session_id": "wf_abc123",
                "blueprint_key": "deploy",
                "blueprint_name": "deploy",
                "status": "failed",
            }
        )

        assert record.status is SessionStatus.FAILED

    @pytest.mark.parametrize("record_name", ["WorktreeRecord", "SessionRecord", "ArtifactRecord"])
    def test_records_define_no_init(self, record_name: str) -> None:
        tree = ast.parse(Path(models_module.__file__).read_text(encoding="utf-8"))
        [record_class] = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == record_name]

        method_names = [node.name for node in record_class.body if isinstance(node, ast.FunctionDef)]

        assert "__init__" not in method_names


def _read_worktree(db_path: Path, db_engine: Engine) -> SQLModel | None:
    return WorktreesRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a").get("dovo_abc123")


def _read_session(db_path: Path, db_engine: Engine) -> SQLModel | None:
    return SessionsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a").get("wf_abc123")


def _read_artifact(db_path: Path, db_engine: Engine) -> SQLModel | None:
    return ArtifactsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a").get("wf_abc123", "dist")


class RecordRoundTripTests:
    """Contract tests that a str-built record persists and reloads equal to its typed equivalent."""

    @pytest.mark.parametrize(
        ("str_record", "typed_record", "read", "exclude"),
        [
            pytest.param(
                lambda: _worktree("/tmp/wt", "merged"),
                lambda: _worktree(Path("/tmp/wt"), WorktreeStatus.MERGED),
                _read_worktree,
                set(),
                id="worktree",
            ),
            pytest.param(
                lambda: _session("paused"),
                lambda: _session(SessionStatus.PAUSED),
                _read_session,
                {"id"},
                id="session",
            ),
            pytest.param(
                lambda: _artifact("/tmp/art"),
                lambda: _artifact(Path("/tmp/art")),
                _read_artifact,
                {"id"},
                id="artifact",
            ),
        ],
    )
    def test_string_built_record_round_trips_equal_to_typed_record(
        self,
        db_path: Path,
        db_engine: Engine,
        str_record: Callable[[], SQLModel],
        typed_record: Callable[[], SQLModel],
        read: Callable[[Path, Engine], SQLModel | None],
        exclude: set[str],
    ) -> None:
        with Session(db_engine) as session:
            session.add(str_record())
            session.commit()

        loaded = read(db_path, db_engine)

        assert loaded is not None
        assert loaded.model_dump(exclude=exclude) == typed_record().model_dump(exclude=exclude)
