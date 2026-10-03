"""Contract tests for centralized database record models."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select

from dovo.core.db.connection import get_engine
from dovo.core.db.migrations import init_database
from dovo.core.db.models import (
    ArtifactRecord,
    CostRecord,
    RunRecord,
    WorktreeRecord,
)

RecordClass = type[RunRecord] | type[WorktreeRecord] | type[CostRecord] | type[ArtifactRecord]


@pytest.fixture
def migrated_engine(tmp_path: Path) -> Engine:
    """Engine bound to a freshly migrated, isolated SQLite database file."""
    db_path = tmp_path / "dovo.db"
    init_database(db_path)
    return get_engine(db_path)


class DbRecordModelTests:
    """Contract tests pinning project_id as a required, NOT NULL column on every centralized record."""

    @pytest.mark.parametrize(
        ("record_cls", "expected_tablename"),
        [
            pytest.param(RunRecord, "runs", id="run_record"),
            pytest.param(WorktreeRecord, "worktrees", id="worktree_record"),
            pytest.param(CostRecord, "costs", id="cost_record"),
            pytest.param(ArtifactRecord, "artifacts", id="artifact_record"),
        ],
    )
    def test_record_declares_project_id_as_required_field(
        self, record_cls: RecordClass, expected_tablename: str
    ) -> None:
        """[tier-1/unit] Record: __tablename__ matches the centralized table and project_id has no default."""
        assert record_cls.__tablename__ == expected_tablename
        assert record_cls.model_fields["project_id"].is_required()

    @pytest.mark.parametrize(
        "record_factory",
        [
            pytest.param(
                lambda: RunRecord(session_id="wf_abc123", blueprint_name="deploy", blueprint_key="deploy"),
                id="run_record",
            ),
            pytest.param(
                lambda: WorktreeRecord(
                    id="dovo_abc123",
                    branch_name="dovo/dovo_abc123",
                    base_commit="deadbeef",
                    worktree_path="/tmp/dovo_abc123",
                ),
                id="worktree_record",
            ),
            pytest.param(
                lambda: CostRecord(  # pyright: ignore[reportCallIssue] # intentional: omitted project_id is this test's subject
                    session_id="wf_abc123", branch_name="deploy", model_id="claude-sonnet-5"
                ),
                id="cost_record",
            ),
            pytest.param(
                lambda: ArtifactRecord(  # pyright: ignore[reportCallIssue] # intentional: omitted project_id is this test's subject
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
        """[tier-1/integration] RunRecord/WorktreeRecord/CostRecord/ArtifactRecord: omitting project_id raises IntegrityError on commit."""
        record = record_factory()
        with Session(migrated_engine) as session:
            session.add(record)
            with pytest.raises(IntegrityError):
                session.commit()


class RunRecordDefaultsTests:
    """Contract tests for RunRecord column defaults."""

    def test_run_record_without_auto_apply_defaults_false(self, migrated_engine: Engine) -> None:
        """[tier-1/integration] RunRecord: a record committed without auto_apply reads back auto_apply False."""
        with Session(migrated_engine) as session:
            session.add(
                RunRecord(project_id="proj-a", session_id="wf_a", blueprint_name="deploy", blueprint_key="deploy")
            )
            session.commit()

        with Session(migrated_engine) as session:
            record = session.exec(select(RunRecord)).one()
            assert record.auto_apply is False
