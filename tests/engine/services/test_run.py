"""Contract tests for engine/services/run.py: BlueprintRunService.execute."""

from __future__ import annotations

import pytest

from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.agents.models import AgentEnvMode, AgentEnvOverrides
from dovo.core.db import SessionsRepository, SessionStatus
from dovo.core.git.runner import GitRunner
from dovo.core.sessions import ReconciliationResult
from dovo.engine.models import RunOutcome
from dovo.engine.services.run import BlueprintRunService
from tests.engine.conftest import StubDriveRun
from tests.harness.catalog import write_runnable_blueprint
from tests.harness.fakes import fixed_clock, fixed_new_id


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
        assert len(result.errors) == 1
        assert "nope" in result.errors[0]

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
            lambda sessions_db, *, path: ReconciliationResult(warning="reconciled 1 stale run"),
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
