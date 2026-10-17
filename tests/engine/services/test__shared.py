"""Contract tests for engine/services/_shared.py: load_record."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.core.db import SessionsRepository
from dovo.engine.services._shared import load_record
from tests.harness.builders import WorkspaceBuilder
from tests.harness.workspace_paths import initialized_workspace_paths


def _repo(tmp_path: Path) -> SessionsRepository:
    workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
    paths = initialized_workspace_paths(workspace)
    return SessionsRepository(db_path=paths.database_file, project_id=paths.project_id)


class SharedLoadRecordTests:
    def test_load_record_returns_repository_record_on_success(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        created = repo.create(session_id="sess-1", blueprint_name="lint", blueprint_key="lint")
        warnings: list[str] = []

        record = load_record(repo, warnings, "sess-1")

        assert record == created
        assert warnings == []

    def test_load_record_appends_warning_and_returns_none_on_repository_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _repo(tmp_path)

        def raise_error(session_id: str) -> None:
            raise RuntimeError("db unavailable")

        monkeypatch.setattr(repo, "get", raise_error)
        warnings: list[str] = []

        record = load_record(repo, warnings, "sess-2")

        assert record is None
        assert warnings == ["Failed to load session record for 'sess-2': db unavailable"]
