"""Contract tests for core/engine/services/_shared.py: fail, load_record, finalize."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.harness.builders import WorkspaceBuilder
from worktree.core.blueprint import BlueprintRunResult
from worktree.core.db import RunsRepository, RunStatus
from worktree.core.engine.services._shared import fail, finalize, load_record
from worktree.core.runtime import RunOutcome


def _repo(tmp_path: Path) -> RunsRepository:
    workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
    return RunsRepository(workspace)


class SharedFailTests:
    def test_fail_returns_result_with_message_and_passed_warnings(self) -> None:
        """[tier-1/domain] fail: returns BlueprintRunResult(run_record=None, errors=[message], warnings=warnings) for the exact warnings list passed in."""
        warnings = ["prior warning"]

        result = fail(warnings, "boom")

        assert result == BlueprintRunResult(run_record=None, errors=["boom"], warnings=warnings)


class SharedLoadRecordTests:
    def test_load_record_returns_repository_record_on_success(self, tmp_path: Path) -> None:
        """[tier-1/domain] load_record: returns the RunRecord RunsRepository.get() returns for an existing session_id, appending no warning."""
        repo = _repo(tmp_path)
        created = repo.create(session_id="sess-1", blueprint_name="lint", blueprint_key="lint")
        warnings: list[str] = []

        record = load_record(repo, warnings, "sess-1")

        assert record == created
        assert warnings == []

    def test_load_record_appends_warning_and_returns_none_on_repository_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/domain] load_record: RunsRepository.get() raising appends "Failed to load run record for '<session_id>': <exc>" to warnings and returns None."""
        repo = _repo(tmp_path)

        def raise_error(session_id: str) -> None:
            raise RuntimeError("db unavailable")

        monkeypatch.setattr(repo, "get", raise_error)
        warnings: list[str] = []

        record = load_record(repo, warnings, "sess-2")

        assert record is None
        assert warnings == ["Failed to load run record for 'sess-2': db unavailable"]


class SharedFinalizeTests:
    def test_finalize_with_missing_record_leaves_run_record_none(self, tmp_path: Path) -> None:
        """[tier-1/domain] finalize: no DB record for session_id returns BlueprintRunResult.run_record is None, with no fallback record ever substituted, for the same input shape both BlueprintRunService and BlueprintResumeService pass."""
        repo = _repo(tmp_path)
        outcome = RunOutcome(status=RunStatus.FAILED, sandbox_path=tmp_path, errors=["step failed"])
        warnings: list[str] = []

        result = finalize(repo, warnings, outcome, "missing-session")

        assert result.run_record is None
        assert result.errors == ["step failed"]

    def test_finalize_with_existing_record_returns_it(self, tmp_path: Path) -> None:
        """[tier-1/domain] finalize: an existing RunsRepository record for session_id is returned unchanged as BlueprintRunResult.run_record."""
        repo = _repo(tmp_path)
        created = repo.create(session_id="sess-3", blueprint_name="lint", blueprint_key="lint")
        outcome = RunOutcome(status=RunStatus.COMPLETED, sandbox_path=tmp_path)
        warnings: list[str] = []

        result = finalize(repo, warnings, outcome, "sess-3")

        assert result.run_record == created

    def test_finalize_extends_passed_warnings_list_in_place(self, tmp_path: Path) -> None:
        """[tier-1/domain] finalize: mutates the caller's warnings list object with run_outcome.warnings rather than returning a new list, so the caller's own field reflects the extension after the call."""
        repo = _repo(tmp_path)
        outcome = RunOutcome(status=RunStatus.COMPLETED, sandbox_path=tmp_path, warnings=["outcome warning"])
        warnings: list[str] = ["caller warning"]

        finalize(repo, warnings, outcome, "missing-session")

        assert warnings == ["caller warning", "outcome warning"]
