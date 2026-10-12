"""Contract tests for engine/services/resume.py: BlueprintResumeService.execute."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.agents.models import AgentEnvOverrides
from dovo.core.db import SessionsRepository, SessionStatus
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from dovo.core.interaction import HeadlessAnswerInput
from dovo.engine import BlueprintRunService
from dovo.engine.models import BlueprintRunResult, RunOutcome
from dovo.engine.services.resume import BlueprintResumeService
from tests.harness.ask import scripted_loop_blueprint
from tests.harness.catalog import write_runnable_blueprint
from tests.harness.sessions import seed_paused_ask_session, seed_paused_session


def _seed(paths: WorkspacePaths, sessions: SessionsRepository, session_id: str) -> None:
    seed_paused_session(
        paths,
        sessions,
        session_id=session_id,
        steps=[{"id": "a", "run": "true", "on_failure": "prompt_user"}],
        paused_step_id="a",
    )


class BlueprintResumeServiceExecuteTests:
    def test_execute_with_explicit_session_resumes_it_and_returns_its_record(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        stub_drive_run: Callable[[RunOutcome | None], None],
    ) -> None:
        """[tier-1/integration] BlueprintResumeService.execute: an explicit paused session id resumes that session and returns ok with its session_record."""
        _seed(engine_paths, sessions_repo, "paused-1")
        stub_drive_run(None)

        result = BlueprintResumeService(paths=engine_paths, db=sessions_repo, session_id="paused-1").execute()

        assert result.ok
        assert result.session_record is not None
        assert result.session_record.session_id == "paused-1"

    def test_execute_without_session_resumes_the_latest_paused_run(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        stub_drive_run: Callable[[RunOutcome | None], None],
    ) -> None:
        """[tier-1/integration] BlueprintResumeService.execute: with no session id, the most recently started paused run is resumed."""
        _seed(engine_paths, sessions_repo, "paused-old")
        _seed(engine_paths, sessions_repo, "paused-new")
        latest = sessions_repo.get_latest_paused()
        assert latest is not None
        stub_drive_run(None)

        result = BlueprintResumeService(paths=engine_paths, db=sessions_repo).execute()

        assert result.session_record is not None
        assert result.session_record.session_id == latest.session_id

    def test_execute_without_session_and_no_paused_run_returns_failed_result(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        """[tier-1/integration] BlueprintResumeService.execute: no session id and no paused run returns session_record None and the single error 'No paused session found to resume.'."""
        result = BlueprintResumeService(paths=engine_paths, db=sessions_repo).execute()

        assert not result.ok
        assert result.session_record is None
        assert result.errors == ["No paused session found to resume."]

    def test_execute_unknown_session_returns_the_engine_resume_error_message(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        """[tier-1/integration] BlueprintResumeService.execute: a session id with no session row returns session_record None and one error naming the session id."""
        result = BlueprintResumeService(paths=engine_paths, db=sessions_repo, session_id="ghost").execute()

        assert not result.ok
        assert result.session_record is None
        assert len(result.errors) == 1
        assert "ghost" in result.errors[0]


class BlueprintResumeServiceEnvTests:
    def test_invalid_passthrough_returns_fail_result_without_resuming(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] BlueprintResumeService.execute: env_passthrough ['*'] returns a non-ok BlueprintRunResult whose errors[0] is the fixed message and Engine.resume is never called."""
        resumed: list[object] = []
        monkeypatch.setattr("dovo.engine.services.resume.Engine", lambda *args, **kwargs: resumed.append(args))
        _seed(engine_paths, sessions_repo, "paused-1")

        result = BlueprintResumeService(
            paths=engine_paths, db=sessions_repo, session_id="paused-1", env_passthrough=["*"]
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
        """[tier-1/integration] BlueprintResumeService.execute: --env-mode and --env-passthrough values reach drive_run as AgentEnvOverrides."""
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
        """[tier-1/integration] BlueprintResumeService.execute: with answers approve=approve, the paused session completes and the downstream step ran."""
        steps = [_ask_step(), _marker_step("after")]
        seed_paused_ask_session(engine_paths, sessions_repo, artifacts_repository, session_id="ask-1", steps=steps)

        result = BlueprintResumeService(
            paths=engine_paths,
            db=sessions_repo,
            session_id="ask-1",
            answers=[HeadlessAnswerInput(step_id="approve", value="approve")],
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
        """[tier-1/integration] BlueprintResumeService.execute: answers approve=maybe returns the invalid-answer error, the row stays PAUSED and the step marker file is absent."""
        steps = [_ask_step(), _marker_step("after")]
        seed_paused_ask_session(engine_paths, sessions_repo, artifacts_repository, session_id="ask-1", steps=steps)

        result = BlueprintResumeService(
            paths=engine_paths,
            db=sessions_repo,
            session_id="ask-1",
            answers=[HeadlessAnswerInput(step_id="approve", value="maybe")],
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
        """[tier-1/integration] BlueprintResumeService.execute: answers against a session paused on a failed step return "No prompt is pending for session '<id>'." and leave it paused."""
        _seed(engine_paths, sessions_repo, "paused-1")

        result = BlueprintResumeService(
            paths=engine_paths,
            db=sessions_repo,
            session_id="paused-1",
            answers=[HeadlessAnswerInput(step_id="a", value="x")],
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
        """[tier-1/integration] BlueprintResumeService.execute: answering the first of two asks with no fallback prompter returns a PAUSED result with paused_prompt_id of the second ask."""
        steps = [_ask_step("first"), _marker_step("between"), _ask_step("second")]
        seed_paused_ask_session(engine_paths, sessions_repo, artifacts_repository, session_id="ask-1", steps=steps)

        result = BlueprintResumeService(
            paths=engine_paths,
            db=sessions_repo,
            session_id="ask-1",
            answers=[HeadlessAnswerInput(step_id="first", value="approve")],
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
            name=key, paths=self.paths, sessions_db=self.sessions, no_worktree=True, no_tty=True
        ).execute()

    def resume(self, session_id: str, *answers: HeadlessAnswerInput) -> BlueprintRunResult:
        """Resume session_id with --no-tty and the given headless answers."""
        return BlueprintResumeService(
            paths=self.paths, db=self.sessions, session_id=session_id, no_tty=True, answers=list(answers)
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
        """[tier-1/integration] BlueprintRunService + BlueprintResumeService: with scripted answers [revise + "Keep config compatible", approve] over command steps, the run pauses, resumes twice and COMPLETES; iteration 2's producer recorded the comment and the session has two `plan` revisions."""
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
        """[tier-1/integration] BlueprintResumeService.execute: after a --no-tty run pauses at `approve`, a --no-tty resume (no answers) leaves the producer's counter file at 1 and the plan artifact at revision 1."""
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
        """[tier-1/integration] BlueprintRunService: a triage multiselect answered with zero findings ends the loop through `selected_count == 0` after that iteration's fix step; answered with ["B1"] the fix step receives it and the loop iterates."""
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
