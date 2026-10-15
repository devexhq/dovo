"""Contract tests for engine/services/_shared.py: fail, load_record, finalize."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.core.db import SessionsRepository, SessionStatus
from dovo.engine.models import BlueprintRunResult, RunOutcome
from dovo.engine.services._shared import fail, finalize, load_record
from tests.harness.builders import WorkspaceBuilder
from tests.harness.workspace_paths import initialized_workspace_paths


def _repo(tmp_path: Path) -> SessionsRepository:
    workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
    paths = initialized_workspace_paths(workspace)
    return SessionsRepository(db_path=paths.database_file, project_id=paths.project_id)


class SharedFailTests:
    def test_fail_returns_result_with_message_and_passed_warnings(self) -> None:
        warnings = ["prior warning"]

        result = fail(warnings, "boom")

        assert result == BlueprintRunResult(session_record=None, errors=["boom"], warnings=warnings)


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


class SharedFinalizeTests:
    def test_finalize_with_missing_record_leaves_run_record_none(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        outcome = RunOutcome(status=SessionStatus.FAILED, worktree_path=tmp_path, errors=["step failed"])
        warnings: list[str] = []

        result = finalize(repo, warnings, outcome, "missing-session")

        assert result.session_record is None
        assert result.errors == ["step failed"]

    def test_finalize_with_existing_record_returns_it(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        created = repo.create(session_id="sess-3", blueprint_name="lint", blueprint_key="lint")
        outcome = RunOutcome(status=SessionStatus.COMPLETED, worktree_path=tmp_path)
        warnings: list[str] = []

        result = finalize(repo, warnings, outcome, "sess-3")

        assert result.session_record == created

    def test_finalize_extends_passed_warnings_list_in_place(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        outcome = RunOutcome(status=SessionStatus.COMPLETED, worktree_path=tmp_path, warnings=["outcome warning"])
        warnings: list[str] = ["caller warning"]

        finalize(repo, warnings, outcome, "missing-session")

        assert warnings == ["caller warning", "outcome warning"]


class FinalizePausedPromptTests:
    def test_finalize_copies_paused_prompt_id(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        paused = RunOutcome(status=SessionStatus.PAUSED, worktree_path=tmp_path, paused_prompt_id="approve")
        completed = RunOutcome(status=SessionStatus.COMPLETED, worktree_path=tmp_path)

        assert finalize(repo, [], paused, "missing").paused_prompt_id == "approve"
        assert finalize(repo, [], completed, "missing").paused_prompt_id is None
