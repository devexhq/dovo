"""Contract tests for engine/services/run.py: BlueprintRunService.execute."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest

from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord
from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem.models import WorkspacePaths
from dovo.common.lock import LockTimeoutError
from dovo.core.agents.models import AgentEnvMode, AgentEnvOverrides
from dovo.core.catalog.definitions import BlueprintDefinition
from dovo.core.db import SessionsRepository, SessionStatus
from dovo.core.git.runner import GitRunner
from dovo.core.sessions import Reconciliation
from dovo.engine.models import RunOutcome
from dovo.engine.services.run import BlueprintRunService
from tests.engine.conftest import StubDriveRun
from tests.harness.catalog import write_runnable_blueprint
from tests.harness.diagnostic_log import diagnostic_record, read_single_event
from tests.harness.fakes import fixed_clock, fixed_new_id
from tests.harness.lock import time_out_first_lock
from tests.harness.sessions import fail_reconciliation_list


class BlueprintRunServiceExecuteTests:
    def test_execute_passes_git_clock_and_new_id_to_drive_run(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Hand drive_run the exact git, clock and new_id objects given to BlueprintRunService."""
        seen: list[object] = []

        def _drive(*args: object, **kwargs: object) -> RunOutcome:
            seen.extend([kwargs["git"], kwargs["clock"], kwargs["new_id"]])
            return RunOutcome(status=SessionStatus.COMPLETED, worktree_path=engine_paths.root_dir)

        monkeypatch.setattr("dovo.engine.engine.drive_run", _drive)
        write_runnable_blueprint(engine_paths.root_dir, key="lint", steps=[{"id": "a", "run": "true"}])
        git = GitRunner()

        BlueprintRunService(
            name="lint",
            paths=engine_paths,
            sessions_db=sessions_repo,
            no_worktree=True,
            git=git,
            clock=fixed_clock,
            new_id=fixed_new_id,
        ).execute()

        assert len(seen) == 3
        assert seen[0] is git
        assert seen[1] is fixed_clock
        assert seen[2] is fixed_new_id

    def test_execute_completed_run_returns_ok_result_with_run_record(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        stub_drive_run: StubDriveRun,
    ) -> None:
        write_runnable_blueprint(engine_paths.root_dir, key="lint", steps=[{"id": "a", "run": "true"}])
        stub_drive_run(None)

        result = BlueprintRunService(
            name="lint",
            paths=engine_paths,
            sessions_db=sessions_repo,
            no_worktree=True,
            session_id="run-1",
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        ).execute()

        assert result.ok
        assert result.session_record is not None
        assert result.session_record.session_id == "run-1"
        assert result.session_record.status == SessionStatus.COMPLETED

    def test_execute_run_outcome_errors_are_returned_on_the_result(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        stub_drive_run: StubDriveRun,
    ) -> None:
        write_runnable_blueprint(engine_paths.root_dir, key="lint", steps=[{"id": "a", "run": "true"}])
        stub_drive_run(
            lambda paths, sessions, session_id: RunOutcome(
                status=SessionStatus.FAILED,
                worktree_path=paths.root_dir,
                session_id=session_id,
                errors=["step 'a' failed"],
                warnings=["cleanup skipped"],
            )
        )

        result = BlueprintRunService(
            name="lint",
            paths=engine_paths,
            sessions_db=sessions_repo,
            no_worktree=True,
            session_id="run-2",
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        ).execute()

        assert not result.ok
        assert result.errors == ["step 'a' failed"]
        assert result.warnings == ["cleanup skipped"]

    def test_execute_unknown_blueprint_returns_failed_result_without_run_record(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        result = BlueprintRunService(
            name="nope",
            paths=engine_paths,
            sessions_db=sessions_repo,
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        ).execute()

        assert not result.ok
        assert result.session_record is None
        assert result.error_code == ErrorCode.CATALOG_NOT_FOUND
        assert result.errors == ["Catalog item 'nope' not found."]
        assert sessions_repo.list() == []


class BlueprintRunServiceLoadTests:
    def test_unexpected_definition_error_is_recorded_under_load_definition(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        monkeypatch: pytest.MonkeyPatch,
        frozen_diagnostic_clock: Path,
    ) -> None:
        """With BlueprintDefinition.from_document raising TypeError("bad"), return INTERNAL_ERROR with the message alone and its record in diagnostics, one catalog.load_definition event naming the blueprint, and no session row."""
        write_runnable_blueprint(engine_paths.root_dir, key="lint", steps=[{"id": "a", "run": "true"}])

        def _explode(cls: type[BlueprintDefinition], raw: dict[str, object], *, key: str) -> BlueprintDefinition:
            raise TypeError("bad")

        monkeypatch.setattr(BlueprintDefinition, "from_document", classmethod(_explode))

        result = BlueprintRunService(
            name="lint",
            paths=engine_paths,
            sessions_db=sessions_repo,
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        ).execute()

        assert result.session_record is None
        assert result.error_code == ErrorCode.INTERNAL_ERROR
        assert result.errors == ["Unexpected error during catalog.load_definition: bad"]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["operation"] == "catalog.load_definition"
        assert event["context"] == {"blueprint": "lint"}
        assert event["exception_type"] == "TypeError"
        assert sessions_repo.list() == []

    def test_reconciliation_record_precedes_catalog_record(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        frozen_diagnostic_clock: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With reconciliation and Blueprint.load both raising and uuid4 yielding two distinct ids, return diagnostics == [the exc_aaaaaaaaaaaa record, the exc_bbbbbbbbbbbb record] in that order and no warning holding a line."""
        write_runnable_blueprint(engine_paths.root_dir, key="lint", steps=[{"id": "a", "run": "true"}])
        monkeypatch.setattr(
            "dovo.common.diagnostic_log.services.record.uuid4",
            iter([UUID("aaaaaaaaaaaa" + "0" * 20), UUID("bbbbbbbbbbbb" + "0" * 20)]).__next__,
        )
        fail_reconciliation_list(monkeypatch)

        def _explode(cls: type[BlueprintDefinition], raw: dict[str, object], *, key: str) -> BlueprintDefinition:
            raise TypeError("bad")

        monkeypatch.setattr(BlueprintDefinition, "from_document", classmethod(_explode))

        result = BlueprintRunService(
            name="lint",
            paths=engine_paths,
            sessions_db=sessions_repo,
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        ).execute()

        day_dir = frozen_diagnostic_clock.parent
        assert result.diagnostics == [
            DiagnosticRecord(
                event_id="exc_aaaaaaaaaaaa", log_path=day_dir / "exc_aaaaaaaaaaaa.json", level=DiagnosticLevel.ERROR
            ),
            DiagnosticRecord(
                event_id="exc_bbbbbbbbbbbb", log_path=day_dir / "exc_bbbbbbbbbbbb.json", level=DiagnosticLevel.ERROR
            ),
        ]
        assert result.warnings == []
        assert result.errors == ["Unexpected error during catalog.load_definition: bad"]
        operations = [
            json.loads((day_dir / f"{event_id}.json").read_text(encoding="utf-8"))["operation"]
            for event_id in ("exc_aaaaaaaaaaaa", "exc_bbbbbbbbbbbb")
        ]
        assert operations == ["session.reconcile", "catalog.load_definition"]

    def test_execute_missing_required_input_returns_formatted_input_error(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        write_runnable_blueprint(engine_paths.root_dir, key="needs-input", steps=[{"id": "a", "run": "true"}])
        blueprint_path = engine_paths.root_dir / ".dovo" / "catalog" / "blueprints" / "needs-input.yml"
        blueprint_path.write_text(
            blueprint_path.read_text(encoding="utf-8").replace(
                "steps:", "inputs:\n- name: target\n  type: string\n  required: true\nsteps:", 1
            ),
            encoding="utf-8",
        )

        result = BlueprintRunService(
            name="needs-input",
            paths=engine_paths,
            sessions_db=sessions_repo,
            no_worktree=True,
            cli_args=[],
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        ).execute()

        assert not result.ok
        assert result.session_record is None
        assert len(result.errors) == 1
        assert "target" in result.errors[0]

    def test_execute_reconciliation_warning_is_carried_onto_the_result(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Append the reconciliation warning before the blueprint loads, so a failed load still reports it."""
        monkeypatch.setattr(
            "dovo.engine.services.run.reconcile_stale_sessions",
            lambda sessions_db, *, path: Reconciliation(warning="reconciled 1 stale run"),
        )

        result = BlueprintRunService(
            name="nope",
            paths=engine_paths,
            sessions_db=sessions_repo,
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        ).execute()

        assert result.warnings == ["reconciled 1 stale run"]

    def test_execute_propagates_reconciliation_lock_timeout(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Raise LockTimeoutError("lock held") from execute() and leave the sessions table empty."""
        write_runnable_blueprint(engine_paths.root_dir, key="lint", steps=[{"id": "a", "run": "true"}])
        time_out_first_lock(monkeypatch)

        with pytest.raises(LockTimeoutError, match="lock held"):
            BlueprintRunService(
                name="lint",
                paths=engine_paths,
                sessions_db=sessions_repo,
                git=GitRunner(),
                clock=fixed_clock,
                new_id=fixed_new_id,
            ).execute()

        monkeypatch.undo()
        assert sessions_repo.list() == []


class BlueprintRunServiceEnvTests:
    def test_invalid_passthrough_returns_fail_result_without_constructing_the_engine(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        constructed: list[object] = []
        monkeypatch.setattr("dovo.engine.services.run.Engine", lambda *args, **kwargs: constructed.append(args))
        write_runnable_blueprint(engine_paths.root_dir, key="lint", steps=[{"id": "a", "run": "true"}])

        result = BlueprintRunService(
            name="lint",
            paths=engine_paths,
            sessions_db=sessions_repo,
            env_passthrough=["A*B"],
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        ).execute()

        assert not result.ok
        assert result.errors[0] == (
            "Invalid --env-passthrough value 'A*B': use a variable name or a prefix ending in a single '*'. "
            "Fix: for example --env-passthrough DOCKER_HOST or --env-passthrough 'DOCKER_*'."
        )
        assert constructed == []
        assert sessions_repo.list() == []

    @pytest.mark.parametrize(
        ("env_mode", "env_passthrough", "expected"),
        [
            pytest.param(
                "inherit", ["DOCKER_*"], AgentEnvOverrides(env_mode="inherit", env_passthrough=["DOCKER_*"]), id="both"
            ),
            pytest.param(None, [], None, id="neither"),
        ],
    )
    def test_valid_flags_reach_drive_run_as_overrides(
        self,
        env_mode: AgentEnvMode | None,
        env_passthrough: list[str],
        expected: AgentEnvOverrides | None,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        seen: list[AgentEnvOverrides | None] = []

        def _drive(*args: object, env_overrides: AgentEnvOverrides | None = None, **kwargs: object) -> RunOutcome:
            seen.append(env_overrides)
            return RunOutcome(status=SessionStatus.COMPLETED, worktree_path=engine_paths.root_dir)

        monkeypatch.setattr("dovo.engine.engine.drive_run", _drive)
        write_runnable_blueprint(engine_paths.root_dir, key="lint", steps=[{"id": "a", "run": "true"}])

        BlueprintRunService(
            name="lint",
            paths=engine_paths,
            sessions_db=sessions_repo,
            no_worktree=True,
            env_mode=env_mode,
            env_passthrough=env_passthrough,
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        ).execute()

        assert seen == [expected]
