"""Contract tests for engine/services/resume.py: BlueprintResumeService.execute."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest

from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord
from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem.models import WorkspacePaths
from dovo.common.lock import LockTimeoutError
from dovo.common.models import Remediation
from dovo.core.agents.models import AgentEnvOverrides
from dovo.core.db import SessionRecord, SessionsRepository, SessionStatus, WorktreesRepository
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from dovo.core.git.runner import GitRunner
from dovo.core.interaction import HeadlessAnswerInput
from dovo.core.worktree import Worktree
from dovo.engine import BlueprintRunService
from dovo.engine.exceptions import EngineError
from dovo.engine.executors.models import StepResult
from dovo.engine.models import BlueprintRunResult, RunOutcome
from dovo.engine.services.resume import BlueprintResumeService
from dovo.engine.state_models import ExecutionLeafNode, NodeState, StepAttemptRecord
from dovo.engine.state_store import SessionStateStore
from dovo.engine.writer import snapshot_blueprint_path
from tests.engine.conftest import StubDriveRun
from tests.harness.ask import scripted_loop_blueprint
from tests.harness.builders import WorkspaceBuilder
from tests.harness.catalog import write_runnable_blueprint
from tests.harness.diagnostic_log import diagnostic_record, read_single_event
from tests.harness.fakes import fixed_clock, fixed_new_id
from tests.harness.lock import time_out_first_lock
from tests.harness.sessions import (
    SEEDED_SPAN,
    RecordingRunObserver,
    seed_crashed_session,
    seed_paused_ask_session,
    seed_paused_session,
)
from tests.harness.workspace_paths import initialized_workspace_paths

PAUSED_SENTENCE = "Paused 1 interrupted session; continue it with 'dovo resume <session_id>'."
RERUN_WARNING = (
    "Step 'a' was interrupted before it recorded a result and was run again as attempt 1; "
    "check for side effects its interrupted attempt already made."
)
STALE_RUN_MESSAGE = "Session interrupted by abnormal process termination"


def _seed(paths: WorkspacePaths, sessions: SessionsRepository, session_id: str) -> None:
    seed_paused_session(
        paths,
        sessions,
        session_id=session_id,
        steps=[{"id": "a", "run": "true", "on_failure": "prompt_user"}],
        paused_step_id="a",
    )


def _end_crashed_run(paths: WorkspacePaths, sessions: SessionsRepository, session_id: str, status: str) -> None:
    """Record a result of status on the crashed run's one step, leaving every top-level node terminal."""
    store = SessionStateStore(sessions, paths, session_id)
    state = store.load([])
    leaf = state.nodes[0]
    assert isinstance(leaf, ExecutionLeafNode)
    result = StepResult(
        step_id=leaf.id,
        status=status,
        exit_code=0 if status == "completed" else 1,
        stdout="",
        stderr="",
        duration_seconds=0.0,
    )
    leaf.attempts = [StepAttemptRecord(number=1, span=SEEDED_SPAN, result=result)]
    leaf.state = NodeState.COMPLETED if status == "completed" else NodeState.FAILED
    store.save(state, [])


def _resume_service(
    paths: WorkspacePaths, sessions: SessionsRepository, session_id: str | None
) -> BlueprintResumeService:
    return BlueprintResumeService(
        paths=paths, db=sessions, session_id=session_id, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id
    )


class BlueprintResumeServiceExecuteTests:
    @pytest.mark.parametrize(
        "session_id", [pytest.param("crashed", id="explicit-id"), pytest.param(None, id="latest-paused")]
    )
    def test_execute_reconciles_and_resumes_a_crashed_run(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, session_id: str | None
    ) -> None:
        """Pause the RUNNING crashed row, rerun its interrupted step and complete it with no error message."""
        seed_crashed_session(
            engine_paths,
            sessions_repo,
            session_id="crashed",
            steps=[{"id": "a", "type": "command", "command": "echo ran > a.txt"}],
            interrupted_step_id="a",
        )

        result = _resume_service(engine_paths, sessions_repo, session_id).execute()

        assert result.ok
        assert result.session_record is not None
        assert result.session_record.session_id == "crashed"
        assert result.session_record.status == SessionStatus.COMPLETED
        assert result.session_record.error_message is None
        assert result.warnings == [PAUSED_SENTENCE, RERUN_WARNING]

    def test_execute_crashed_run_with_a_failed_step_and_no_step_left_is_finalized(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        """Return SESSION_STATE_CORRUPT and leave the row failed, so no paused session is left to pick."""
        seed_crashed_session(
            engine_paths,
            sessions_repo,
            session_id="crashed",
            steps=[{"id": "a", "type": "command", "command": "exit 1"}],
            interrupted_step_id="a",
        )
        _end_crashed_run(engine_paths, sessions_repo, "crashed", "failed")

        result = _resume_service(engine_paths, sessions_repo, "crashed").execute()

        assert result.error_code == ErrorCode.SESSION_STATE_CORRUPT
        assert sessions_repo.get_latest_paused() is None
        row = sessions_repo.get("crashed")
        assert row is not None
        assert row.status == SessionStatus.FAILED
        assert row.error_message == STALE_RUN_MESSAGE

    def test_execute_finished_crashed_run_completes_and_auto_applies(self, tmp_path: Path) -> None:
        """Complete a worktree-backed auto_apply crashed run whose every node completed and apply its committed change."""
        paths = initialized_workspace_paths(
            WorkspaceBuilder(tmp_path / "git-workspace").with_git().with_database().build()
        )
        sessions = SessionsRepository(db_path=paths.database_file, project_id=paths.project_id)
        created = Worktree(
            paths,
            db=WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id),
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        ).create(session_id="crashed")
        assert created.session is not None
        worktree_id = created.session.session_id
        worktree = paths.worktree_dir(worktree_id)
        (worktree / "applied.txt").write_text("change\n", encoding="utf-8")
        GitRunner().run(["add", "applied.txt"], worktree)
        GitRunner().run(["commit", "-m", "change"], worktree)
        seed_crashed_session(
            paths,
            sessions,
            session_id="crashed",
            steps=[{"id": "a", "type": "command", "command": "true"}],
            interrupted_step_id="a",
            use_worktree=True,
            worktree_id=worktree_id,
            auto_apply=True,
        )
        _end_crashed_run(paths, sessions, "crashed", "completed")

        result = _resume_service(paths, sessions, "crashed").execute()

        assert result.ok
        row = sessions.get("crashed")
        assert row is not None
        assert row.status == SessionStatus.COMPLETED
        assert row.error_message is None
        assert (paths.root_dir / "applied.txt").read_text(encoding="utf-8") == "change\n"

    def test_execute_passes_git_clock_and_new_id_to_drive_run(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Hand drive_run the exact git, clock and new_id objects given to BlueprintResumeService."""
        seen: list[object] = []

        def _drive(*args: object, **kwargs: object) -> RunOutcome:
            seen.extend([kwargs["git"], kwargs["clock"], kwargs["new_id"]])
            return RunOutcome(status=SessionStatus.COMPLETED, worktree_path=engine_paths.root_dir)

        monkeypatch.setattr("dovo.engine.engine.drive_run", _drive)
        _seed(engine_paths, sessions_repo, "paused-1")
        git = GitRunner()

        BlueprintResumeService(
            paths=engine_paths, db=sessions_repo, session_id="paused-1", git=git, clock=fixed_clock, new_id=fixed_new_id
        ).execute()

        assert len(seen) == 3
        assert seen[0] is git
        assert seen[1] is fixed_clock
        assert seen[2] is fixed_new_id

    def test_execute_with_explicit_session_resumes_it_and_returns_its_record(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        stub_drive_run: StubDriveRun,
    ) -> None:
        _seed(engine_paths, sessions_repo, "paused-1")
        stub_drive_run(None)

        result = BlueprintResumeService(
            paths=engine_paths,
            db=sessions_repo,
            session_id="paused-1",
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        ).execute()

        assert result.ok
        assert result.session_record is not None
        assert result.session_record.session_id == "paused-1"

    def test_execute_without_session_resumes_the_latest_paused_run(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        stub_drive_run: StubDriveRun,
    ) -> None:
        _seed(engine_paths, sessions_repo, "paused-old")
        _seed(engine_paths, sessions_repo, "paused-new")
        latest = sessions_repo.get_latest_paused()
        assert latest is not None
        stub_drive_run(None)

        result = BlueprintResumeService(
            paths=engine_paths, db=sessions_repo, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id
        ).execute()

        assert result.session_record is not None
        assert result.session_record.session_id == latest.session_id

    def test_execute_without_session_and_no_paused_run_returns_failed_result(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        result = BlueprintResumeService(
            paths=engine_paths, db=sessions_repo, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id
        ).execute()

        assert not result.ok
        assert result.session_record is None
        assert result.error_code is None
        assert result.errors == ["No paused session found to resume."]

    def test_execute_unknown_session_returns_session_not_found(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        result = BlueprintResumeService(
            paths=engine_paths,
            db=sessions_repo,
            session_id="ghost",
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        ).execute()

        assert not result.ok
        assert result.session_record is None
        assert result.error_code == ErrorCode.SESSION_NOT_FOUND
        assert result.errors == ["Session 'ghost' not found."]


class BlueprintResumeServiceValidationTests:
    def test_invalid_snapshot_returns_every_validate_error_and_runs_nothing(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        _seed(engine_paths, sessions_repo, "paused-1")
        before = sessions_repo.get("paused-1")
        assert before is not None
        snapshot = snapshot_blueprint_path(engine_paths.session_dir("paused-1"), "paused-1")
        snapshot.write_text(
            "name: paused-1\nsteps:\n  - {id: a, run: 'true'}\n  - {id: a, type: script, script_path: missing.sh}\n",
            encoding="utf-8",
        )
        observer = RecordingRunObserver()

        result = BlueprintResumeService(
            paths=engine_paths,
            db=sessions_repo,
            session_id="paused-1",
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
            observer=observer,
        ).execute()

        assert (result.invalid_blueprint, result.error_code) == ("paused-1", ErrorCode.CATALOG_SCHEMA_INVALID)
        assert result.errors == ["Step id 'a' is used by more than one step."]
        assert result.remediations == [Remediation(message="Resolve: Step id 'a' is used by more than one step.")]
        assert observer.calls == [
            (
                "validation_warnings",
                ["Step 'a' script_path 'missing.sh' does not exist on disk (CATALOG_SCRIPT_NOT_FOUND)."],
            )
        ]
        after = sessions_repo.get("paused-1")
        assert after is not None
        assert (after.status, after.execution_state_revision) == (SessionStatus.PAUSED, before.execution_state_revision)


class BlueprintResumeServiceCatchTests:
    def test_unexpected_error_after_load_returns_internal_error_recorded_under_engine_resume(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        stub_drive_run: StubDriveRun,
        frozen_diagnostic_clock: Path,
    ) -> None:
        """With a paused session and drive_run raising TypeError("boom"), return INTERNAL_ERROR, the message, the record, and one engine.resume event."""
        _seed(engine_paths, sessions_repo, "paused-1")

        def _boom(paths: WorkspacePaths, sessions: SessionsRepository, session_id: str) -> RunOutcome:
            raise TypeError("boom")

        stub_drive_run(_boom)

        result = BlueprintResumeService(
            paths=engine_paths,
            db=sessions_repo,
            session_id="paused-1",
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        ).execute()

        assert result.error_code == ErrorCode.INTERNAL_ERROR
        assert result.errors == ["Unexpected error during engine.resume: boom"]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["operation"] == "engine.resume"
        assert event["context"] == {"session_id": "paused-1"}

    def test_read_run_record_precedes_resume_failure_record(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        stub_drive_run: StubDriveRun,
        frozen_diagnostic_clock: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With the first session read raising and drive_run raising an EngineError from an OSError, return diagnostics == [the engine.read_run record, the engine.resume record] in that order."""
        _seed(engine_paths, sessions_repo, "paused-1")
        monkeypatch.setattr(
            "dovo.common.diagnostic_log.services.record.uuid4",
            iter([UUID("aaaaaaaaaaaa" + "0" * 20), UUID("bbbbbbbbbbbb" + "0" * 20)]).__next__,
        )
        real_get = SessionsRepository.get
        reads: list[str] = []

        def _fail_first_read(self: SessionsRepository, session_id: str) -> SessionRecord | None:
            reads.append(session_id)
            if len(reads) == 1:
                raise TypeError("read failed")
            return real_get(self, session_id)

        monkeypatch.setattr(SessionsRepository, "get", _fail_first_read)

        def _boom(paths: WorkspacePaths, sessions: SessionsRepository, session_id: str) -> RunOutcome:
            try:
                raise PermissionError("state.json")
            except OSError as exc:
                raise EngineError(ErrorCode.SESSION_STATE_CORRUPT, "Unable to read session state.") from exc

        stub_drive_run(_boom)

        result = BlueprintResumeService(
            paths=engine_paths,
            db=sessions_repo,
            session_id="paused-1",
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        ).execute()

        day_dir = frozen_diagnostic_clock.parent
        assert result.error_code == ErrorCode.SESSION_STATE_CORRUPT
        assert result.errors == ["Unable to read session state."]
        assert result.diagnostics == [
            DiagnosticRecord(
                event_id="exc_aaaaaaaaaaaa", log_path=day_dir / "exc_aaaaaaaaaaaa.json", level=DiagnosticLevel.ERROR
            ),
            DiagnosticRecord(
                event_id="exc_bbbbbbbbbbbb", log_path=day_dir / "exc_bbbbbbbbbbbb.json", level=DiagnosticLevel.ERROR
            ),
        ]
        operations = [
            json.loads((day_dir / f"{event_id}.json").read_text(encoding="utf-8"))["operation"]
            for event_id in ("exc_aaaaaaaaaaaa", "exc_bbbbbbbbbbbb")
        ]
        assert operations == ["engine.read_run", "engine.resume"]

    def test_unexpected_error_resolving_latest_paused_returns_internal_error(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        monkeypatch: pytest.MonkeyPatch,
        frozen_diagnostic_clock: Path,
    ) -> None:
        """With no session id and get_latest_paused raising TypeError("boom"), return INTERNAL_ERROR and one engine.resume_target event."""

        def _boom(self: SessionsRepository) -> SessionRecord | None:
            raise TypeError("boom")

        monkeypatch.setattr(SessionsRepository, "get_latest_paused", _boom)

        result = BlueprintResumeService(
            paths=engine_paths, db=sessions_repo, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id
        ).execute()

        assert result.error_code == ErrorCode.INTERNAL_ERROR
        assert result.errors == ["Unexpected error during engine.resume_target: boom"]
        assert read_single_event(frozen_diagnostic_clock)["operation"] == "engine.resume_target"

    def test_lock_timeout_propagates(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With the first lock timing out, raise LockTimeoutError("lock held") and leave the session paused."""
        _seed(engine_paths, sessions_repo, "paused-1")
        time_out_first_lock(monkeypatch)

        with pytest.raises(LockTimeoutError, match="lock held"):
            BlueprintResumeService(
                paths=engine_paths,
                db=sessions_repo,
                session_id="paused-1",
                git=GitRunner(),
                clock=fixed_clock,
                new_id=fixed_new_id,
            ).execute()

        paused = sessions_repo.get("paused-1")
        assert paused is not None
        assert paused.status == SessionStatus.PAUSED


class BlueprintResumeServiceEnvTests:
    def test_invalid_passthrough_returns_fail_result_without_resuming(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resumed: list[object] = []
        monkeypatch.setattr("dovo.engine.services.resume.Engine", lambda *args, **kwargs: resumed.append(args))
        _seed(engine_paths, sessions_repo, "paused-1")

        result = BlueprintResumeService(
            paths=engine_paths,
            db=sessions_repo,
            session_id="paused-1",
            env_passthrough=["*"],
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        ).execute()

        assert not result.ok
        assert result.errors[0] == (
            "Invalid --env-passthrough value '*': use a variable name or a prefix ending in a single '*'. "
            "Fix: for example --env-passthrough DOCKER_HOST or --env-passthrough 'DOCKER_*'."
        )
        assert resumed == []
        paused = sessions_repo.get("paused-1")
        assert paused is not None
        assert paused.status == SessionStatus.PAUSED

    def test_valid_flags_reach_drive_run_as_overrides(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[AgentEnvOverrides | None] = []

        def _drive(*args: object, env_overrides: AgentEnvOverrides | None = None, **kwargs: object) -> RunOutcome:
            seen.append(env_overrides)
            return RunOutcome(status=SessionStatus.COMPLETED, worktree_path=engine_paths.root_dir)

        monkeypatch.setattr("dovo.engine.engine.drive_run", _drive)
        _seed(engine_paths, sessions_repo, "paused-1")

        BlueprintResumeService(
            paths=engine_paths,
            db=sessions_repo,
            session_id="paused-1",
            env_mode="allowlist",
            env_passthrough=["NAME"],
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        ).execute()

        assert seen == [AgentEnvOverrides(env_mode="allowlist", env_passthrough=["NAME"])]


def _ask_step(step_id: str = "approve") -> dict[str, object]:
    """Return a select ask step over approve / revise / deny."""
    return {
        "id": step_id,
        "type": "ask",
        "interaction": {
            "mode": "select",
            "message": f"{step_id}?",
            "options": [
                {"value": "approve", "label": "Approve", "comment": "disabled"},
                {"value": "revise", "label": "Revise", "comment": "required"},
                {"value": "deny", "label": "Deny", "fail": True},
            ],
        },
    }


def _marker_step(name: str) -> dict[str, object]:
    """Return a command step that writes marker file name in the workspace root."""
    return {"id": f"write-{name}", "type": "command", "command": f"echo ran > {name}.txt"}


class BlueprintResumeAnswerTests:
    def test_valid_answer_continues_the_run_in_process(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        artifacts_repository: ArtifactsRepository,
    ) -> None:
        steps = [_ask_step(), _marker_step("after")]
        seed_paused_ask_session(engine_paths, sessions_repo, artifacts_repository, session_id="ask-1", steps=steps)

        result = BlueprintResumeService(
            paths=engine_paths,
            db=sessions_repo,
            session_id="ask-1",
            answers=[HeadlessAnswerInput(step_id="approve", value="approve")],
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        ).execute()

        assert result.ok
        assert result.paused_prompt_id is None
        assert result.session_record is not None
        assert result.session_record.status == SessionStatus.COMPLETED
        assert (engine_paths.root_dir / "after.txt").exists()

    def test_invalid_answer_leaves_session_paused_and_runs_nothing(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        artifacts_repository: ArtifactsRepository,
    ) -> None:
        steps = [_ask_step(), _marker_step("after")]
        seed_paused_ask_session(engine_paths, sessions_repo, artifacts_repository, session_id="ask-1", steps=steps)

        result = BlueprintResumeService(
            paths=engine_paths,
            db=sessions_repo,
            session_id="ask-1",
            answers=[HeadlessAnswerInput(step_id="approve", value="maybe")],
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        ).execute()

        assert result.errors == [
            "'maybe' is not a valid answer for prompt 'approve'.\nFix: use one of: approve, revise, deny"
        ]
        row = sessions_repo.get("ask-1")
        assert row is not None
        assert row.status == SessionStatus.PAUSED
        assert not (engine_paths.root_dir / "after.txt").exists()

    def test_answers_without_pending_prompt_fail_with_pending_message(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        _seed(engine_paths, sessions_repo, "paused-1")

        result = BlueprintResumeService(
            paths=engine_paths,
            db=sessions_repo,
            session_id="paused-1",
            answers=[HeadlessAnswerInput(step_id="a", value="x")],
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        ).execute()

        assert result.errors == ["No prompt is pending for session 'paused-1'."]
        row = sessions_repo.get("paused-1")
        assert row is not None
        assert row.status == SessionStatus.PAUSED

    def test_later_ask_after_a_preset_answer_pauses_again(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        artifacts_repository: ArtifactsRepository,
    ) -> None:
        steps = [_ask_step("first"), _marker_step("between"), _ask_step("second")]
        seed_paused_ask_session(engine_paths, sessions_repo, artifacts_repository, session_id="ask-1", steps=steps)

        result = BlueprintResumeService(
            paths=engine_paths,
            db=sessions_repo,
            session_id="ask-1",
            answers=[HeadlessAnswerInput(step_id="first", value="approve")],
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        ).execute()

        assert result.ok
        assert result.paused_prompt_id == "second"
        assert result.session_record is not None
        assert result.session_record.status == SessionStatus.PAUSED
        assert (engine_paths.root_dir / "between.txt").exists()


@dataclass
class AskWorkspace:
    """Workspace, repositories and service helpers for driving ask blueprints headlessly."""

    paths: WorkspacePaths
    sessions: SessionsRepository
    artifacts: ArtifactsRepository

    def run(self, key: str, steps: list[dict[str, object]]) -> BlueprintRunResult:
        """Write steps as blueprint key and run it with --no-tty, so it pauses at its first ask."""
        write_runnable_blueprint(self.paths.root_dir, key=key, steps=steps)
        return BlueprintRunService(
            name=key,
            paths=self.paths,
            sessions_db=self.sessions,
            no_worktree=True,
            no_tty=True,
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        ).execute()

    def resume(self, session_id: str, *answers: HeadlessAnswerInput) -> BlueprintRunResult:
        """Resume session_id with --no-tty and the given headless answers."""
        return BlueprintResumeService(
            paths=self.paths,
            db=self.sessions,
            session_id=session_id,
            no_tty=True,
            answers=list(answers),
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        ).execute()

    def lines(self, name: str) -> list[str]:
        """Return the lines of workspace file name, or [] when it does not exist."""
        path: Path = self.paths.root_dir / name
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


@pytest.fixture
def ask_workspace(
    engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, artifacts_repository: ArtifactsRepository
) -> AskWorkspace:
    """AskWorkspace over the engine workspace."""
    return AskWorkspace(engine_paths, sessions_repo, artifacts_repository)


def _session_id(result: BlueprintRunResult) -> str:
    """Return the session id of a run result."""
    assert result.session_record is not None
    return result.session_record.session_id


def _loop_steps() -> list[dict[str, object]]:
    """Return the scripted revise-until-approved steps."""
    steps = scripted_loop_blueprint()["steps"]
    assert isinstance(steps, list)
    return steps


def _triage_steps() -> list[dict[str, object]]:
    """Return a review-triage loop whose agents are command steps."""
    review = (
        "echo review >> review-runs.txt; "
        'echo \'{"findings": [{"id": "B1", "summary": "Missing validation"}]}\' > review.json'
    )
    return [
        {
            "id": "review-loop",
            "type": "loop",
            "max_iterations": 3,
            "until": ["steps.triage.outputs.selected_count == 0"],
            "do": [
                {
                    "id": "review",
                    "type": "command",
                    "command": review,
                    "artifacts": [{"name": "review", "path": "review.json"}],
                },
                {
                    "id": "triage",
                    "type": "ask",
                    "interaction": {
                        "mode": "multiselect",
                        "message": "Which findings should the agent fix?",
                        "options_from": {
                            "artifact": "review",
                            "file": "review.json",
                            "items_path": "findings",
                            "value_field": "id",
                            "label_field": "summary",
                        },
                        "comment": {"policy": "optional"},
                    },
                },
                {"id": "fix", "type": "command", "command": "echo '{{ steps.triage.outputs.selected }}' >> fixed.txt"},
            ],
        }
    ]


class AskLoopEndToEndTests:
    """End-to-end tests for ask steps inside loops."""

    def test_revise_until_approved_loop_completes_headlessly(self, ask_workspace: AskWorkspace) -> None:
        first = ask_workspace.run("revise-loop", _loop_steps())
        session_id = _session_id(first)
        assert first.paused_prompt_id == "approve"

        second = ask_workspace.resume(
            session_id,
            HeadlessAnswerInput(step_id="approve", value="revise", comment="Keep config compatible"),
        )
        assert second.paused_prompt_id == "approve"

        final = ask_workspace.resume(session_id, HeadlessAnswerInput(step_id="approve", value="approve"))

        assert final.ok
        assert final.paused_prompt_id is None
        assert final.session_record is not None
        assert final.session_record.status == SessionStatus.COMPLETED
        assert ask_workspace.lines("feedback.txt") == ["feedback: ", "feedback: Keep config compatible"]
        record = ask_workspace.artifacts.get(session_id, "plan")
        assert record is not None
        assert record.revision == 2

    def test_resuming_a_pending_ask_does_not_rerun_the_producer(self, ask_workspace: AskWorkspace) -> None:
        session_id = _session_id(ask_workspace.run("revise-loop", _loop_steps()))

        resumed = ask_workspace.resume(session_id)

        assert resumed.paused_prompt_id == "approve"
        assert ask_workspace.lines("producer-runs.txt") == ["run"]
        record = ask_workspace.artifacts.get(session_id, "plan")
        assert record is not None
        assert record.revision == 1

    @pytest.mark.parametrize(
        ("answers", "expected_fixed", "expected_reviews"),
        [
            pytest.param([[]], ["[]"], 1, id="nothing_selected_ends_the_loop"),
            pytest.param([["B1"], []], ['["B1"]', "[]"], 2, id="selected_finding_is_fixed_then_loop_ends"),
        ],
    )
    def test_triage_loop_ends_when_nothing_is_selected(
        self, ask_workspace: AskWorkspace, answers: list[list[str]], expected_fixed: list[str], expected_reviews: int
    ) -> None:
        result = ask_workspace.run("review-triage", _triage_steps())
        session_id = _session_id(result)

        for selected in answers:
            assert result.paused_prompt_id == "triage"
            result = ask_workspace.resume(session_id, HeadlessAnswerInput(step_id="triage", selected=selected))

        assert result.ok
        assert result.session_record is not None
        assert result.session_record.status == SessionStatus.COMPLETED
        assert ask_workspace.lines("fixed.txt") == expected_fixed
        assert len(ask_workspace.lines("review-runs.txt")) == expected_reviews
