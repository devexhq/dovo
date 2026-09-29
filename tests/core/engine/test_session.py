"""Contract tests for drive_run: workspace lifecycle around one RunCoordinator execution."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from tests.harness.builders import WorkspaceBuilder
from tests.harness.runs import NoOpRunObserver, seed_new_run, seed_paused_run
from worktree.common.filesystem.models import RepositoryPaths, WorkspacePaths
from worktree.common.filesystem.services.global_root import resolve_global_paths
from worktree.core.db import RunsRepository, RunStatus, SandboxesRepository
from worktree.core.engine.models import (
    FailurePromptDecision,
    FailurePrompter,
    LoopPromptDecision,
    RunObserver,
    RunOutcome,
)
from worktree.core.engine.session import drive_run
from worktree.core.project.services.storage import resolve_workspace_paths
from worktree.core.sandbox import Sandbox, SandboxApplyResult, SandboxApplyStatus
from worktree.core.step.models import LoopStepBlock, StepDefinition, StepResult


class _Prompter(FailurePrompter):
    """Scripted FailurePrompter answering with queued decisions, or interrupting when the queue is empty."""

    def __init__(self, decisions: list[FailurePromptDecision] | None = None) -> None:
        self.decisions = list(decisions or [])

    def prompt_step_failure(
        self, *, step: StepDefinition, result: StepResult, diagnostic: str
    ) -> FailurePromptDecision:
        if not self.decisions:
            raise KeyboardInterrupt
        return self.decisions.pop(0)

    def prompt_loop_max_iterations(
        self, *, loop: LoopStepBlock, iteration: int, diagnostic: str, grant_count: int = 3
    ) -> LoopPromptDecision:
        raise AssertionError("prompt_loop_max_iterations should not be called")


class _LifecycleObserver(NoOpRunObserver):
    """RunObserver recording sandbox, step, and streamed-output callbacks in call order."""

    def __init__(self) -> None:
        self.events: list[tuple[object, ...]] = []

    def on_sandbox_ready(self, path: Path, active: bool) -> None:
        self.events.append(("sandbox_ready", path, active))

    def on_step_start(self, idx: int, total: int, step: StepDefinition) -> None:
        self.events.append(("step_start", idx, total, step.id))

    def on_step_output(self, idx: int, total: int, step: StepDefinition, line: str, stream: str = "stdout") -> None:
        self.events.append(("step_output", idx, total, step.id, line, stream))

    def on_step_done(self, idx: int, total: int, result: StepResult) -> None:
        self.events.append(("step_done", idx, total, result.step_id))

    def on_sandbox_cleanup(self, kept: bool, path: Path) -> None:
        self.events.append(("sandbox_cleanup", kept, path))


def _step(step_id: str, run: str, **extra: object) -> dict[str, object]:
    return {"id": step_id, "run": run, **extra}


def _drive(
    paths: WorkspacePaths,
    runs: RunsRepository,
    session_id: str,
    *,
    prompter: FailurePrompter | None = None,
    observer: RunObserver | None = None,
) -> RunOutcome:
    return drive_run(paths, runs, session_id, observer=observer, prompter=prompter, no_tty=False)


def _paths_for(root: Path) -> WorkspacePaths:
    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(None))


@pytest.fixture
def git_paths(tmp_path: Path) -> WorkspacePaths:
    """WorkspacePaths for a git-backed workspace where sandboxes can be created."""
    return _paths_for(WorkspaceBuilder(tmp_path / "git-workspace").with_git().with_database().build())


@pytest.fixture
def git_runs(git_paths: WorkspacePaths) -> RunsRepository:
    """RunsRepository bound to the git-backed workspace database."""
    return RunsRepository(db_path=git_paths.database_file, project_id=git_paths.project_id)


class DriveRunSandboxTests:
    """[tier-1/integration] drive_run: sandbox reuse, identity, and setup failure."""

    def test_resumed_run_reuses_retained_sandbox_and_keeps_sandbox_id(
        self, git_paths: WorkspacePaths, git_runs: RunsRepository
    ) -> None:
        """[tier-1/integration] drive_run: a paused sandboxed row with an existing sandbox directory runs its steps with cwd equal to paths.sandbox_dir(sandbox_id), and the finished row's sandbox_id is unchanged."""
        created = Sandbox(
            git_paths, db=SandboxesRepository(db_path=git_paths.database_file, project_id=git_paths.project_id)
        ).create(session_id="retained")
        assert created.session is not None
        sandbox_id = created.session.session_id
        expected_cwd = git_paths.sandbox_dir(sandbox_id).resolve()
        seed_paused_run(
            git_paths,
            git_runs,
            session_id="resume-sbx",
            steps=[_step("a", "true"), _step("b", f'test "$(pwd -P)" = "{expected_cwd}"', on_failure="prompt_user")],
            paused_step_id="b",
            use_sandbox=True,
            sandbox_id=sandbox_id,
        )

        outcome = _drive(git_paths, git_runs, "resume-sbx", prompter=_Prompter([FailurePromptDecision.RETRY]))

        assert outcome.status == RunStatus.COMPLETED
        assert outcome.sandbox_id == sandbox_id
        row = git_runs.get("resume-sbx")
        assert row is not None
        assert row.sandbox_id == sandbox_id

    def test_sandboxed_run_reports_session_id_and_removes_worktree(
        self, git_paths: WorkspacePaths, git_runs: RunsRepository
    ) -> None:
        """[tier-1/integration] drive_run: a completed unkept sandboxed run returns RunOutcome.sandbox_id equal to the created session id, sandbox_kept False, and the worktree directory removed."""
        seed_new_run(git_paths, git_runs, session_id="sbx-done", steps=[_step("a", "echo hi")], use_sandbox=True)

        outcome = _drive(git_paths, git_runs, "sbx-done")

        sandboxes = SandboxesRepository(db_path=git_paths.database_file, project_id=git_paths.project_id).list()
        assert outcome.status == RunStatus.COMPLETED
        assert [record.id for record in sandboxes] == [outcome.sandbox_id]
        assert outcome.sandbox_kept is False
        assert not outcome.sandbox_path.exists()

    def test_no_sandbox_run_reports_none_sandbox_id(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] drive_run: use_sandbox=False returns RunOutcome.sandbox_id None and sandbox_path equal to the resolved cwd."""
        seed_new_run(engine_paths, runs_repo, session_id="no-sbx", steps=[_step("a", "echo hi")])

        outcome = _drive(engine_paths, runs_repo, "no-sbx")

        assert outcome.sandbox_id is None
        assert outcome.sandbox_path == engine_paths.root_dir.resolve()

    def test_sandbox_creation_failure_returns_failed_outcome_without_running_steps(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, engine_workspace: Path
    ) -> None:
        """[tier-1/integration] drive_run: use_sandbox=True in a directory that is not a git repository returns FAILED with errors[0] starting "Git sandbox creation failed:" and no step marker file."""
        seed_new_run(engine_paths, runs_repo, session_id="no-git", steps=[_step("a", "touch a.ran")], use_sandbox=True)

        outcome = _drive(engine_paths, runs_repo, "no-git")

        assert outcome.status == RunStatus.FAILED
        assert outcome.errors[0].startswith("Git sandbox creation failed:")
        assert not (engine_workspace / "a.ran").exists()


class DriveRunMissingRowTests:
    def test_unknown_session_returns_failed_outcome_without_running_steps(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] drive_run: a session id with no run row returns FAILED with errors == ["Run 'missing' not found."] and sandbox_path equal to the workspace root."""
        outcome = _drive(engine_paths, runs_repo, "missing")

        assert outcome.status == RunStatus.FAILED
        assert outcome.errors == ["Run 'missing' not found."]
        assert outcome.sandbox_path == engine_paths.root_dir


class DriveRunLifecycleTests:
    """[tier-1/integration] drive_run: cleanup, scratch directories, logs, auto-apply, and config plumbing."""

    def test_keep_true_preserves_worktree_after_completed_run(
        self, git_paths: WorkspacePaths, git_runs: RunsRepository
    ) -> None:
        """[tier-1/integration] drive_run: row keep=True with a completed sandboxed run returns sandbox_kept True and the worktree directory still present."""
        seed_new_run(
            git_paths, git_runs, session_id="keep", steps=[_step("a", "echo keep")], use_sandbox=True, keep=True
        )

        outcome = _drive(git_paths, git_runs, "keep")

        assert outcome.status == RunStatus.COMPLETED
        assert outcome.sandbox_kept is True
        assert outcome.sandbox_path.is_dir()

    def test_paused_outcome_keeps_sandbox(self, git_paths: WorkspacePaths, git_runs: RunsRepository) -> None:
        """[tier-1/integration] drive_run: a run that pauses at a prompt returns status PAUSED, sandbox_kept True, and the worktree directory present."""
        seed_new_run(
            git_paths,
            git_runs,
            session_id="pause",
            steps=[_step("a", "exit 1", on_failure="prompt_user")],
            use_sandbox=True,
        )

        outcome = _drive(git_paths, git_runs, "pause", prompter=_Prompter())

        assert outcome.status == RunStatus.PAUSED
        assert outcome.sandbox_kept is True
        assert outcome.sandbox_path.is_dir()

    def test_diff_persisted_under_session_id_not_sandbox_key(
        self, git_paths: WorkspacePaths, git_runs: RunsRepository
    ) -> None:
        """[tier-1/integration] drive_run: a sandboxed step that writes a file leaves <session_dir>/diff.patch containing that file's path."""
        seed_new_run(
            git_paths,
            git_runs,
            session_id="diff-run",
            steps=[_step("a", "echo change > tracked.txt")],
            use_sandbox=True,
        )

        outcome = _drive(git_paths, git_runs, "diff-run")

        assert outcome.status == RunStatus.COMPLETED
        diff_file = git_paths.session_dir("diff-run") / "diff.patch"
        assert "tracked.txt" in diff_file.read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        ("auto_apply", "expected_status", "expected_kept"),
        [
            pytest.param(True, RunStatus.FAILED, True, id="auto-apply-conflict"),
            pytest.param(False, RunStatus.COMPLETED, False, id="auto-apply-off"),
        ],
    )
    def test_auto_apply_read_from_row_conflict_marks_run_failed_and_keeps_sandbox(
        self,
        git_paths: WorkspacePaths,
        git_runs: RunsRepository,
        monkeypatch: pytest.MonkeyPatch,
        auto_apply: bool,
        expected_status: RunStatus,
        expected_kept: bool,
    ) -> None:
        """[tier-1/integration] drive_run: a row with auto_apply=True whose apply conflicts returns FAILED with the apply error in errors and sandbox_kept True; with auto_apply=False the same run returns COMPLETED."""
        conflict = SandboxApplyResult(
            sandbox_id="test-session",
            status=SandboxApplyStatus.CONFLICT,
            errors=["Patch merge conflict in sandbox apply"],
            warnings=["Patch hunk rejected"],
        )
        monkeypatch.setattr(Sandbox, "apply", lambda *args, **kwargs: conflict)
        seed_new_run(
            git_paths,
            git_runs,
            session_id="apply",
            steps=[_step("a", "echo change")],
            use_sandbox=True,
            auto_apply=auto_apply,
        )

        outcome = _drive(git_paths, git_runs, "apply")

        assert outcome.status == expected_status
        assert outcome.sandbox_kept is expected_kept
        assert outcome.errors == (["Patch merge conflict in sandbox apply"] if auto_apply else [])

    def test_cleanup_failure_after_step_failure_still_reports_failed(
        self, git_paths: WorkspacePaths, git_runs: RunsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] drive_run: Sandbox.cleanup raising after an aborted step still returns status FAILED."""

        def exploding_cleanup(self: Sandbox, session: object, **kwargs: object) -> list[str]:
            raise RuntimeError("Cleanup filesystem removal failed")

        monkeypatch.setattr(Sandbox, "cleanup", exploding_cleanup)
        seed_new_run(git_paths, git_runs, session_id="cleanup", steps=[_step("fail", "exit 1")], use_sandbox=True)

        outcome = _drive(git_paths, git_runs, "cleanup")

        assert outcome.status == RunStatus.FAILED
        assert outcome.sandbox_kept is False
        assert outcome.errors == ["Step 'fail' failed: Command failed with exit code 1."]

    @pytest.mark.parametrize(
        ("keep", "command", "expect_preserved"),
        [
            pytest.param(False, '[ -d "$WT_TEMP/steps" ]', False, id="completed-no-keep-deletes"),
            pytest.param(True, '[ -d "$WT_TEMP/steps" ]', True, id="completed-keep-preserves"),
            pytest.param(False, '[ -d "$WT_TEMP/steps" ] && exit 1', True, id="failed-preserves"),
        ],
    )
    def test_session_scratch_dir_created_before_first_step_and_removed_when_completed_unkept(
        self,
        engine_paths: WorkspacePaths,
        runs_repo: RunsRepository,
        keep: bool,
        command: str,
        expect_preserved: bool,
    ) -> None:
        """[tier-1/integration] drive_run: a step asserting [ -d "$WT_TEMP/steps" ] passes; after a completed unkept run the session tmp directory no longer exists, and after a failed or kept run it remains."""
        seed_new_run(engine_paths, runs_repo, session_id="scratch", steps=[_step("s1", command)], keep=keep)

        outcome = _drive(engine_paths, runs_repo, "scratch")

        assert outcome.step_results[0].exit_code == (1 if command.endswith("exit 1") else 0)
        assert (engine_paths.tmp_dir / "scratch").exists() is expect_preserved

    def test_resume_reuses_same_session_tmp_dir_and_truncates_stale_output(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] drive_run: a resumed run sees step_a's earlier scratch file under the same WT_TEMP, and a re-run step's StepResult.outputs equals {"fresh": "yes"} with no stale key."""
        seed_paused_run(
            engine_paths,
            runs_repo,
            session_id="resume-tmp",
            steps=[
                _step("step_a", 'echo "marker=yes" >> "$WT_OUTPUT"'),
                _step(
                    "step_b",
                    '[ -f "$WT_TEMP/step_step_a.output" ] && echo "fresh=yes" >> "$WT_OUTPUT"',
                    on_failure="prompt_user",
                ),
            ],
            paused_step_id="step_b",
        )
        session_tmp_dir = engine_paths.tmp_dir / "resume-tmp"
        session_tmp_dir.mkdir(parents=True)
        (session_tmp_dir / "step_step_a.output").write_text("marker=yes\n", encoding="utf-8")
        (session_tmp_dir / "step_step_b.output").write_text("stale=yes\n", encoding="utf-8")

        outcome = _drive(engine_paths, runs_repo, "resume-tmp", prompter=_Prompter([FailurePromptDecision.RETRY]))

        assert outcome.status == RunStatus.COMPLETED
        assert outcome.step_results[-1].outputs == {"fresh": "yes"}

    def test_run_log_records_started_and_completed_events(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] drive_run: run.log begins with a run_started event carrying session_id and blueprint_key and ends with run_completed carrying status "completed", both with parseable ISO timestamps."""
        seed_new_run(engine_paths, runs_repo, session_id="timeline", steps=[_step("a", "echo a")])

        _drive(engine_paths, runs_repo, "timeline")

        lines = (engine_paths.logs_dir / "timeline" / "run.log").read_text(encoding="utf-8").splitlines()
        first, last = json.loads(lines[0]), json.loads(lines[-1])
        assert (first["event"], first["session_id"], first["blueprint_key"]) == ("run_started", "timeline", "timeline")
        assert (last["event"], last["status"]) == ("run_completed", "completed")
        assert datetime.fromisoformat(first["ts"]) <= datetime.fromisoformat(last["ts"])

    @pytest.mark.parametrize(
        ("save_attempt_logs", "expect_log"),
        [pytest.param(False, False, id="disabled"), pytest.param(True, True, id="enabled")],
    )
    def test_merged_config_save_attempt_logs_reaches_step_execution(
        self, tmp_path: Path, save_attempt_logs: bool, expect_log: bool
    ) -> None:
        """[tier-1/integration] drive_run: with history.save_attempt_logs false in the workspace config, no per-attempt log file is written for a completed step."""
        workspace = (
            WorkspaceBuilder(tmp_path / "config-workspace")
            .with_database()
            .with_config(
                data={"version": 1, "project": {"name": "session"}, "history": {"save_attempt_logs": save_attempt_logs}}
            )
            .build()
        )
        paths = _paths_for(workspace)
        runs = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        seed_new_run(paths, runs, session_id="attempt-logs", steps=[_step("a", "echo a")])

        _drive(paths, runs, "attempt-logs")

        attempt_logs = list((paths.logs_dir / "attempt-logs").glob("*attempt*"))
        assert bool(attempt_logs) is expect_log

    def test_observer_receives_sandbox_step_and_cleanup_callbacks_in_order(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] drive_run: the observer sees sandbox_ready, step_start, step_done, then sandbox_cleanup in that order for a run without a sandbox."""
        seed_new_run(engine_paths, runs_repo, session_id="observed", steps=[_step("s1", "echo one")])
        observer = _LifecycleObserver()

        _drive(engine_paths, runs_repo, "observed", observer=observer)

        resolved = engine_paths.root_dir.resolve()
        assert [event for event in observer.events if event[0] != "step_output"] == [
            ("sandbox_ready", resolved, False),
            ("step_start", 1, 1, "s1"),
            ("step_done", 1, 1, "s1"),
            ("sandbox_cleanup", False, resolved),
        ]

    def test_step_output_streams_to_observer_by_line_and_stream(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] drive_run: streamed step output reaches the observer line by line tagged with its stream."""
        seed_new_run(
            engine_paths,
            runs_repo,
            session_id="streamed",
            steps=[_step("s1", "echo out; echo err >&2")],
        )
        observer = _LifecycleObserver()

        _drive(engine_paths, runs_repo, "streamed", observer=observer)

        output_events = [event for event in observer.events if event[0] == "step_output"]
        assert sorted(output_events) == [
            ("step_output", 1, 1, "s1", "err\n", "stderr"),
            ("step_output", 1, 1, "s1", "out\n", "stdout"),
        ]
