"""Contract tests for engine/services/run.py: BlueprintRunService.execute."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.db import RunsRepository, RunStatus
from dovo.core.history.models import ReconciliationResult
from dovo.engine.models import RunOutcome
from dovo.engine.services.run import BlueprintRunService
from tests.harness.catalog import write_runnable_blueprint


class BlueprintRunServiceExecuteTests:
    def test_execute_completed_run_returns_ok_result_with_run_record(
        self,
        engine_paths: WorkspacePaths,
        runs_repo: RunsRepository,
        stub_drive_run: Callable[[RunOutcome | None], None],
    ) -> None:
        """[tier-1/integration] BlueprintRunService.execute: a cataloged blueprint whose run completes returns ok with run_record.session_id equal to the requested session id and status COMPLETED."""
        write_runnable_blueprint(engine_paths.root_dir, key="lint", steps=[{"id": "a", "run": "true"}])
        stub_drive_run(None)

        result = BlueprintRunService(
            name="lint", paths=engine_paths, runs_db=runs_repo, no_sandbox=True, session_id="run-1"
        ).execute()

        assert result.ok
        assert result.run_record is not None
        assert result.run_record.session_id == "run-1"
        assert result.run_record.status == RunStatus.COMPLETED

    def test_execute_run_outcome_errors_are_returned_on_the_result(
        self,
        engine_paths: WorkspacePaths,
        runs_repo: RunsRepository,
        stub_drive_run: Callable[[RunOutcome | None], None],
    ) -> None:
        """[tier-1/integration] BlueprintRunService.execute: a run outcome carrying errors yields a non-ok result whose errors and warnings equal the outcome's."""
        write_runnable_blueprint(engine_paths.root_dir, key="lint", steps=[{"id": "a", "run": "true"}])
        stub_drive_run(
            RunOutcome(
                status=RunStatus.FAILED,
                sandbox_path=engine_paths.root_dir,
                errors=["step 'a' failed"],
                warnings=["cleanup skipped"],
            )
        )

        result = BlueprintRunService(
            name="lint", paths=engine_paths, runs_db=runs_repo, no_sandbox=True, session_id="run-2"
        ).execute()

        assert not result.ok
        assert result.errors == ["step 'a' failed"]
        assert result.warnings == ["cleanup skipped"]

    def test_execute_unknown_blueprint_returns_failed_result_without_run_record(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] BlueprintRunService.execute: a blueprint name absent from the catalog returns run_record None and exactly one error naming the blueprint."""
        result = BlueprintRunService(name="nope", paths=engine_paths, runs_db=runs_repo).execute()

        assert not result.ok
        assert result.run_record is None
        assert len(result.errors) == 1
        assert "nope" in result.errors[0]

    def test_execute_missing_required_input_returns_formatted_input_error(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] BlueprintRunService.execute: a blueprint declaring a required input run without it returns run_record None and one error naming the missing input."""
        write_runnable_blueprint(engine_paths.root_dir, key="needs-input", steps=[{"id": "a", "run": "true"}])
        blueprint_path = engine_paths.root_dir / ".dovo" / "catalog" / "blueprints" / "needs-input.yml"
        blueprint_path.write_text(
            blueprint_path.read_text(encoding="utf-8").replace(
                "steps:", "inputs:\n- name: target\n  type: string\n  required: true\nsteps:", 1
            ),
            encoding="utf-8",
        )

        result = BlueprintRunService(
            name="needs-input", paths=engine_paths, runs_db=runs_repo, no_sandbox=True, cli_args=[]
        ).execute()

        assert not result.ok
        assert result.run_record is None
        assert len(result.errors) == 1
        assert "target" in result.errors[0]

    def test_execute_reconciliation_warning_is_carried_onto_the_result(
        self,
        engine_paths: WorkspacePaths,
        runs_repo: RunsRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """[tier-1/unit] BlueprintRunService.execute: a stale-run reconciliation warning is appended to warnings before the blueprint loads, so even a failed load reports it."""
        monkeypatch.setattr(
            "dovo.engine.services.run.reconcile_stale_runs",
            lambda runs_db, *, path: ReconciliationResult(warning="reconciled 1 stale run"),
        )

        result = BlueprintRunService(name="nope", paths=engine_paths, runs_db=runs_repo).execute()

        assert result.warnings == ["reconciled 1 stale run"]
