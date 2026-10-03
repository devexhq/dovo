"""Contract tests for drive_run: workspace lifecycle around one RunCoordinator execution."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import pytest

from dovo.common.filesystem.models import RepositoryPaths, WorkspacePaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.agents.models import AgentRequest, AgentResponse, AgentResponseStatus, ResolvedAgentSettings
from dovo.core.catalog.definitions import LoopStepBlock, StepDefinition
from dovo.core.db import RunsRepository, RunStatus, WorktreesRepository
from dovo.core.logs.services.read import read_run_log_events
from dovo.core.project.services.storage import resolve_workspace_paths
from dovo.core.worktree import Worktree, WorktreeApplyResult, WorktreeApplyStatus
from dovo.engine.executors.agent_step import WORKTREE_REQUIRED_MESSAGE, build_agent_step_runner
from dovo.engine.executors.models import (
    AgentStepRunner,
    ConditionEvaluationResult,
    StepExecutionContext,
    StepResult,
)
from dovo.engine.executors.step_executor import StepExecution
from dovo.engine.models import (
    FailurePromptDecision,
    FailurePrompter,
    LoopPromptDecision,
    RunObserver,
    RunOutcome,
)
from dovo.engine.session import drive_run
from dovo.engine.writer import snapshot_blueprint_path
from tests.harness import AGENT_ADAPTER_FACTORY, FakeAgentProvider, new_file_diff
from tests.harness.builders import WorkspaceBuilder
from tests.harness.runs import NoOpRunObserver, seed_new_run, seed_paused_run


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


class _DiagnosticPrompter(_Prompter):
    """Scripted FailurePrompter that also records each failure diagnostic it is shown."""

    def __init__(self, decisions: list[FailurePromptDecision] | None = None) -> None:
        super().__init__(decisions)
        self.diagnostics: list[str] = []

    def prompt_step_failure(
        self, *, step: StepDefinition, result: StepResult, diagnostic: str
    ) -> FailurePromptDecision:
        self.diagnostics.append(diagnostic)
        return super().prompt_step_failure(step=step, result=result, diagnostic=diagnostic)


class _LifecycleObserver(NoOpRunObserver):
    """RunObserver recording worktree, step, and streamed-output callbacks in call order."""

    def __init__(self) -> None:
        self.events: list[tuple[object, ...]] = []

    def on_worktree_ready(self, path: Path, active: bool) -> None:
        self.events.append(("worktree_ready", path, active))

    def on_step_start(self, idx: int, total: int, step: StepDefinition) -> None:
        self.events.append(("step_start", idx, total, step.id))

    def on_step_output(self, idx: int, total: int, step: StepDefinition, line: str, stream: str = "stdout") -> None:
        self.events.append(("step_output", idx, total, step.id, line, stream))

    def on_step_done(self, idx: int, total: int, step: StepDefinition, result: StepResult) -> None:
        self.events.append(("step_done", idx, total, result.step_id))

    def on_worktree_cleanup(self, kept: bool, path: Path) -> None:
        self.events.append(("worktree_cleanup", kept, path))


class _SequenceObserver(NoOpRunObserver):
    """RunObserver recording every callback in one ordered list."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def on_worktree_ready(self, path: Path, active: bool) -> None:
        self.calls.append(("worktree_ready",))

    def on_run_started(self, steps: Sequence[StepDefinition | LoopStepBlock]) -> None:
        self.calls.append(("run_started", [step.id for step in steps]))

    def on_step_start(self, idx: int, total: int, step: StepDefinition) -> None:
        self.calls.append(("step_start", idx, total, step.id))

    def on_step_done(self, idx: int, total: int, step: StepDefinition, result: StepResult) -> None:
        self.calls.append(("step_done", idx, total, step.id, result.status))

    def on_loop_start(self, loop_id: str, max_iterations: int) -> None:
        self.calls.append(("loop_start", max_iterations))

    def on_loop_iteration_start(self, loop_id: str, iteration: int, max_iterations: int) -> None:
        self.calls.append(("loop_iteration_start", iteration, max_iterations))

    def on_loop_conditions_evaluated(
        self,
        loop_id: str,
        results: list[ConditionEvaluationResult],
        all_passed: bool,
        next_iteration: int | None = None,
    ) -> None:
        self.calls.append(("loop_conditions_evaluated", [r.passed for r in results], all_passed, next_iteration))

    def on_loop_done(self, loop_id: str, status: str, total_iterations: int) -> None:
        self.calls.append(("loop_done", status, total_iterations))

    def on_worktree_cleanup(self, kept: bool, path: Path) -> None:
        self.calls.append(("worktree_cleanup",))

    def on_run_completed(self, outcome: RunOutcome) -> None:
        self.calls.append(("run_completed", outcome))


class _RaisingRunLevelObserver(NoOpRunObserver):
    """RunObserver raising from both run-level callbacks."""

    def on_run_started(self, steps: Sequence[StepDefinition | LoopStepBlock]) -> None:
        raise RuntimeError("observer run started")

    def on_run_completed(self, outcome: RunOutcome) -> None:
        raise RuntimeError("observer run completed")


def _step(step_id: str, run: str, **extra: object) -> dict[str, object]:
    return {"id": step_id, "run": run, **extra}


def _loop(loop_id: str, do: list[dict[str, object]], *, max_iterations: int = 2) -> dict[str, object]:
    return {
        "id": loop_id,
        "type": "loop",
        "max_iterations": max_iterations,
        "until": [f"iteration.index >= {max_iterations}"],
        "do": do,
    }


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
    """WorkspacePaths for a git-backed workspace where worktrees can be created."""
    return _paths_for(WorkspaceBuilder(tmp_path / "git-workspace").with_git().with_database().build())


@pytest.fixture
def git_runs(git_paths: WorkspacePaths) -> RunsRepository:
    """RunsRepository bound to the git-backed workspace database."""
    return RunsRepository(db_path=git_paths.database_file, project_id=git_paths.project_id)


class DriveRunWorktreeTests:
    """[tier-1/integration] drive_run: worktree reuse, identity, and setup failure."""

    def test_resumed_run_reuses_retained_worktree_and_keeps_worktree_id(
        self, git_paths: WorkspacePaths, git_runs: RunsRepository
    ) -> None:
        """[tier-1/integration] drive_run: a paused worktree-backed row with an existing worktree directory runs its steps with cwd equal to paths.worktree_dir(worktree_id), and the finished row's worktree_id is unchanged."""
        created = Worktree(
            git_paths, db=WorktreesRepository(db_path=git_paths.database_file, project_id=git_paths.project_id)
        ).create(session_id="retained")
        assert created.session is not None
        worktree_id = created.session.session_id
        expected_cwd = git_paths.worktree_dir(worktree_id).resolve()
        seed_paused_run(
            git_paths,
            git_runs,
            session_id="resume-worktree",
            steps=[_step("a", "true"), _step("b", f'test "$(pwd -P)" = "{expected_cwd}"', on_failure="prompt_user")],
            paused_step_id="b",
            use_worktree=True,
            worktree_id=worktree_id,
        )

        outcome = _drive(git_paths, git_runs, "resume-worktree", prompter=_Prompter([FailurePromptDecision.RETRY]))

        assert outcome.status == RunStatus.COMPLETED
        assert outcome.worktree_id == worktree_id
        row = git_runs.get("resume-worktree")
        assert row is not None
        assert row.worktree_id == worktree_id

    def test_worktree_run_reports_session_id_and_removes_worktree(
        self, git_paths: WorkspacePaths, git_runs: RunsRepository
    ) -> None:
        """[tier-1/integration] drive_run: a completed unkept worktree-backed run returns RunOutcome.worktree_id equal to the created session id, worktree_kept False, and the worktree directory removed."""
        seed_new_run(git_paths, git_runs, session_id="worktree-done", steps=[_step("a", "echo hi")], use_worktree=True)

        outcome = _drive(git_paths, git_runs, "worktree-done")

        worktrees = WorktreesRepository(db_path=git_paths.database_file, project_id=git_paths.project_id).list()
        assert outcome.status == RunStatus.COMPLETED
        assert [record.id for record in worktrees] == [outcome.worktree_id]
        assert outcome.worktree_kept is False
        assert not outcome.worktree_path.exists()

    def test_no_worktree_run_reports_none_worktree_id(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] drive_run: use_worktree=False returns RunOutcome.worktree_id None and worktree_path equal to the resolved cwd."""
        seed_new_run(engine_paths, runs_repo, session_id="no-worktree", steps=[_step("a", "echo hi")])

        outcome = _drive(engine_paths, runs_repo, "no-worktree")

        assert outcome.worktree_id is None
        assert outcome.worktree_path == engine_paths.root_dir.resolve()

    def test_worktree_creation_failure_returns_failed_outcome_without_running_steps(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, engine_workspace: Path
    ) -> None:
        """[tier-1/integration] drive_run: use_worktree=True in a directory that is not a git repository returns FAILED with errors[0] starting "Git worktree creation failed:" and no step marker file."""
        seed_new_run(engine_paths, runs_repo, session_id="no-git", steps=[_step("a", "touch a.ran")], use_worktree=True)

        outcome = _drive(engine_paths, runs_repo, "no-git")

        assert outcome.status == RunStatus.FAILED
        assert outcome.errors[0].startswith("Git worktree creation failed:")
        assert not (engine_workspace / "a.ran").exists()


class DriveRunMissingRowTests:
    def test_unknown_session_returns_failed_outcome_without_running_steps(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] drive_run: a session id with no run row returns FAILED with errors == ["Run 'missing' not found."] and worktree_path equal to the workspace root."""
        outcome = _drive(engine_paths, runs_repo, "missing")

        assert outcome.status == RunStatus.FAILED
        assert outcome.errors == ["Run 'missing' not found."]
        assert outcome.worktree_path == engine_paths.root_dir


class DriveRunLifecycleTests:
    """[tier-1/integration] drive_run: cleanup, scratch directories, logs, auto-apply, and config plumbing."""

    def test_keep_true_preserves_worktree_after_completed_run(
        self, git_paths: WorkspacePaths, git_runs: RunsRepository
    ) -> None:
        """[tier-1/integration] drive_run: row keep=True with a completed worktree-backed run returns worktree_kept True and the worktree directory still present."""
        seed_new_run(
            git_paths, git_runs, session_id="keep", steps=[_step("a", "echo keep")], use_worktree=True, keep=True
        )

        outcome = _drive(git_paths, git_runs, "keep")

        assert outcome.status == RunStatus.COMPLETED
        assert outcome.worktree_kept is True
        assert outcome.worktree_path.is_dir()

    def test_paused_outcome_keeps_worktree(self, git_paths: WorkspacePaths, git_runs: RunsRepository) -> None:
        """[tier-1/integration] drive_run: a run that pauses at a prompt returns status PAUSED, worktree_kept True, and the worktree directory present."""
        seed_new_run(
            git_paths,
            git_runs,
            session_id="pause",
            steps=[_step("a", "exit 1", on_failure="prompt_user")],
            use_worktree=True,
        )

        outcome = _drive(git_paths, git_runs, "pause", prompter=_Prompter())

        assert outcome.status == RunStatus.PAUSED
        assert outcome.worktree_kept is True
        assert outcome.worktree_path.is_dir()

    def test_diff_persisted_under_session_id_not_worktree_key(
        self, git_paths: WorkspacePaths, git_runs: RunsRepository
    ) -> None:
        """[tier-1/integration] drive_run: a worktree-backed step that writes a file leaves <session_dir>/diff.patch containing that file's path."""
        seed_new_run(
            git_paths,
            git_runs,
            session_id="diff-run",
            steps=[_step("a", "echo change > tracked.txt")],
            use_worktree=True,
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
    def test_auto_apply_read_from_row_conflict_marks_run_failed_and_keeps_worktree(
        self,
        git_paths: WorkspacePaths,
        git_runs: RunsRepository,
        monkeypatch: pytest.MonkeyPatch,
        auto_apply: bool,
        expected_status: RunStatus,
        expected_kept: bool,
    ) -> None:
        """[tier-1/integration] drive_run: a row with auto_apply=True whose apply conflicts returns FAILED with the apply error in errors and worktree_kept True; with auto_apply=False the same run returns COMPLETED."""
        conflict = WorktreeApplyResult(
            worktree_id="test-session",
            status=WorktreeApplyStatus.CONFLICT,
            errors=["Patch merge conflict in worktree apply"],
            warnings=["Patch hunk rejected"],
        )
        monkeypatch.setattr(Worktree, "apply", lambda *args, **kwargs: conflict)
        seed_new_run(
            git_paths,
            git_runs,
            session_id="apply",
            steps=[_step("a", "echo change")],
            use_worktree=True,
            auto_apply=auto_apply,
        )

        outcome = _drive(git_paths, git_runs, "apply")

        assert outcome.status == expected_status
        assert outcome.worktree_kept is expected_kept
        assert outcome.errors == (["Patch merge conflict in worktree apply"] if auto_apply else [])

    def test_cleanup_failure_after_step_failure_still_reports_failed(
        self, git_paths: WorkspacePaths, git_runs: RunsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] drive_run: Worktree.cleanup raising after an aborted step still returns status FAILED."""

        def exploding_cleanup(self: Worktree, session: object, **kwargs: object) -> list[str]:
            raise RuntimeError("Cleanup filesystem removal failed")

        monkeypatch.setattr(Worktree, "cleanup", exploding_cleanup)
        seed_new_run(git_paths, git_runs, session_id="cleanup", steps=[_step("fail", "exit 1")], use_worktree=True)

        outcome = _drive(git_paths, git_runs, "cleanup")

        assert outcome.status == RunStatus.FAILED
        assert outcome.worktree_kept is False
        assert outcome.errors == ["Step 'fail' failed: Command failed with exit code 1."]

    @pytest.mark.parametrize(
        ("keep", "command", "expect_preserved"),
        [
            pytest.param(False, '[ -d "$DOVO_TEMP/steps" ]', False, id="completed-no-keep-deletes"),
            pytest.param(True, '[ -d "$DOVO_TEMP/steps" ]', True, id="completed-keep-preserves"),
            pytest.param(False, '[ -d "$DOVO_TEMP/steps" ] && exit 1', True, id="failed-preserves"),
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
        """[tier-1/integration] drive_run: a step asserting [ -d "$DOVO_TEMP/steps" ] passes; after a completed unkept run the session tmp directory no longer exists, and after a failed or kept run it remains."""
        seed_new_run(engine_paths, runs_repo, session_id="scratch", steps=[_step("s1", command)], keep=keep)

        outcome = _drive(engine_paths, runs_repo, "scratch")

        assert outcome.step_results[0].exit_code == (1 if command.endswith("exit 1") else 0)
        assert (engine_paths.tmp_dir / "scratch").exists() is expect_preserved

    def test_resume_reuses_same_session_tmp_dir_and_truncates_stale_output(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] drive_run: a resumed run sees step_a's earlier scratch file under the same DOVO_TEMP, and a re-run step's StepResult.outputs equals {"fresh": "yes"} with no stale key."""
        seed_paused_run(
            engine_paths,
            runs_repo,
            session_id="resume-tmp",
            steps=[
                _step("step_a", 'echo "marker=yes" >> "$DOVO_OUTPUT"'),
                _step(
                    "step_b",
                    '[ -f "$DOVO_TEMP/step_step_a.output" ] && echo "fresh=yes" >> "$DOVO_OUTPUT"',
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

    def test_run_log_round_trips_through_reader_for_three_step_run(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] read_run_log_events: a drive_run 3-step run.log returns 8 events run_started, 3 step_start/step_done pairs (step_index 1..3, attempt 1, completed, exit_code 0), run_completed status "completed", none skipped, ts non-decreasing."""
        seed_new_run(
            engine_paths,
            runs_repo,
            session_id="timeline3",
            steps=[_step("a", "echo a"), _step("b", "echo b"), _step("c", "echo c")],
        )

        _drive(engine_paths, runs_repo, "timeline3")
        log_dir = engine_paths.logs_dir / "timeline3"

        events = read_run_log_events(log_dir, tail=None)

        assert len(events) == len((log_dir / "run.log").read_text(encoding="utf-8").splitlines())
        durations = [e.duration_seconds for e in events if e.event.value == "step_done"]
        assert len(durations) == 3
        assert all(isinstance(duration, float) for duration in durations)
        step_events = [
            event
            for index, step_id in enumerate(("a", "b", "c"), start=1)
            for event in (
                ("step_start", {"step_index": index, "step_id": step_id, "attempt": 1}),
                (
                    "step_done",
                    {"step_index": index, "step_id": step_id, "attempt": 1, "status": "completed", "exit_code": 0},
                ),
            )
        ]
        assert [(e.event.value, {k: v for k, v in e.details().items() if k != "duration_seconds"}) for e in events] == [
            ("run_started", {"session_id": "timeline3", "blueprint_key": "timeline3"}),
            *step_events,
            ("run_completed", {"status": "completed"}),
        ]
        timestamps = [datetime.fromisoformat(e.ts) for e in events]
        assert timestamps == sorted(timestamps)

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


class DriveRunObserverContractTests:
    """[tier-1/integration] drive_run: run-level and step-level observer callbacks and their order."""

    def test_linear_run_emits_pinned_observer_sequence(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] drive_run: a 3-step run [a, b, c] without a worktree gives the ordered observer sequence worktree_ready, run_started(["a","b","c"]), step_start/step_done at 1/3, 2/3, 3/3 with step ids and "completed", worktree_cleanup, run_completed(COMPLETED outcome equal to the returned outcome)."""
        seed_new_run(
            engine_paths,
            runs_repo,
            session_id="seq-linear",
            steps=[_step("a", "true"), _step("b", "true"), _step("c", "true")],
        )
        observer = _SequenceObserver()

        outcome = _drive(engine_paths, runs_repo, "seq-linear", observer=observer)

        assert outcome.status == RunStatus.COMPLETED
        assert observer.calls == [
            ("worktree_ready",),
            ("run_started", ["a", "b", "c"]),
            ("step_start", 1, 3, "a"),
            ("step_done", 1, 3, "a", "completed"),
            ("step_start", 2, 3, "b"),
            ("step_done", 2, 3, "b", "completed"),
            ("step_start", 3, 3, "c"),
            ("step_done", 3, 3, "c", "completed"),
            ("worktree_cleanup",),
            ("run_completed", outcome),
        ]

    def test_loop_run_emits_pinned_observer_sequence(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] drive_run: a 2-iteration loop "loop" of [s1, s2] gives run_started(["loop"]), loop_start(2), loop_iteration_start(1, 2), s1 (1/2), s2 (2/2), loop_conditions_evaluated([False], False, 2), loop_iteration_start(2, 2), s1 (1/2), s2 (2/2), loop_conditions_evaluated([True], True, None), loop_done("completed", 2), run_completed."""
        seed_new_run(
            engine_paths,
            runs_repo,
            session_id="seq-loop",
            steps=[_loop("loop", [_step("s1", "true"), _step("s2", "true")])],
        )
        observer = _SequenceObserver()

        outcome = _drive(engine_paths, runs_repo, "seq-loop", observer=observer)

        iteration_steps = [
            ("step_start", 1, 2, "s1"),
            ("step_done", 1, 2, "s1", "completed"),
            ("step_start", 2, 2, "s2"),
            ("step_done", 2, 2, "s2", "completed"),
        ]
        assert observer.calls == [
            ("worktree_ready",),
            ("run_started", ["loop"]),
            ("loop_start", 2),
            ("loop_iteration_start", 1, 2),
            *iteration_steps,
            ("loop_conditions_evaluated", [False], False, 2),
            ("loop_iteration_start", 2, 2),
            *iteration_steps,
            ("loop_conditions_evaluated", [True], True, None),
            ("loop_done", "completed", 2),
            ("worktree_cleanup",),
            ("run_completed", outcome),
        ]

    def test_run_completed_receives_paused_outcome_and_resume_gets_its_own_pair(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, engine_workspace: Path
    ) -> None:
        """[tier-1/integration] drive_run: a run pausing at a prompt_user failure gives run_started once and run_completed once with status PAUSED equal to the returned outcome; resuming it gives a second run_started and a run_completed with status COMPLETED."""
        marker = engine_workspace / "retry.marker"
        seed_new_run(
            engine_paths,
            runs_repo,
            session_id="seq-pause",
            steps=[_step("a", f"test -e {marker}", on_failure="prompt_user")],
        )
        observer = _SequenceObserver()

        paused = _drive(engine_paths, runs_repo, "seq-pause", prompter=_Prompter(), observer=observer)
        marker.touch()
        resumed = _drive(
            engine_paths,
            runs_repo,
            "seq-pause",
            prompter=_Prompter([FailurePromptDecision.RETRY]),
            observer=observer,
        )

        assert paused.status == RunStatus.PAUSED
        assert resumed.status == RunStatus.COMPLETED
        run_level = [call for call in observer.calls if call[0] in {"run_started", "run_completed"}]
        assert run_level == [
            ("run_started", ["a"]),
            ("run_completed", paused),
            ("run_started", ["a"]),
            ("run_completed", resumed),
        ]

    def test_missing_snapshot_skips_run_started_and_completes_failed(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] drive_run: a seeded run whose definitions snapshot file is deleted returns FAILED, the observer records no run_started, and run_completed once with the returned FAILED outcome."""
        seed_new_run(engine_paths, runs_repo, session_id="seq-nosnap", steps=[_step("a", "true")])
        snapshot_blueprint_path(engine_paths.session_dir("seq-nosnap"), "seq-nosnap").unlink()
        observer = _SequenceObserver()

        outcome = _drive(engine_paths, runs_repo, "seq-nosnap", observer=observer)

        assert outcome.status == RunStatus.FAILED
        assert [call for call in observer.calls if str(call[0]).startswith("run_")] == [("run_completed", outcome)]

    def test_setup_failure_notifies_run_completed_only(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] drive_run: use_worktree=True outside a git repository returns FAILED with errors[0] starting "Git worktree creation failed:", the observer records run_completed once with that outcome and no run_started."""
        seed_new_run(engine_paths, runs_repo, session_id="seq-nogit", steps=[_step("a", "true")], use_worktree=True)
        observer = _SequenceObserver()

        outcome = _drive(engine_paths, runs_repo, "seq-nogit", observer=observer)

        assert outcome.status == RunStatus.FAILED
        assert outcome.errors[0].startswith("Git worktree creation failed:")
        assert [call for call in observer.calls if str(call[0]).startswith("run_")] == [("run_completed", outcome)]

    def test_missing_row_notifies_nothing(self, engine_paths: WorkspacePaths, runs_repo: RunsRepository) -> None:
        """[tier-1/integration] drive_run: a session id with no run row returns FAILED with errors == ["Run 'missing' not found."] and the observer records no callback."""
        observer = _SequenceObserver()

        outcome = _drive(engine_paths, runs_repo, "missing", observer=observer)

        assert outcome.status == RunStatus.FAILED
        assert outcome.errors == ["Run 'missing' not found."]
        assert observer.calls == []

    def test_raising_run_level_observer_leaves_outcome_unchanged(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] drive_run: an observer raising from on_run_started and on_run_completed on a completed 1-step run returns a RunOutcome whose status, step_results statuses, errors, and warnings equal those from a no-op observer on an identical seeded run."""
        for session_id in ("raise-plain", "raise-observed"):
            seed_new_run(engine_paths, runs_repo, session_id=session_id, steps=[_step("a", "true")])

        plain = _drive(engine_paths, runs_repo, "raise-plain", observer=NoOpRunObserver())
        observed = _drive(engine_paths, runs_repo, "raise-observed", observer=_RaisingRunLevelObserver())

        assert observed.status == plain.status == RunStatus.COMPLETED
        assert [r.status for r in observed.step_results] == [r.status for r in plain.step_results]
        assert observed.errors == plain.errors
        assert observed.warnings == plain.warnings


_AGENT_CONFIG: dict[str, object] = {
    "provider": "copilot",
    "model": "test-model",
    "endpoint": "http://127.0.0.1:9999",
    "temperature": 0.7,
    "max_tokens": 512,
}


def _agent_step(step_id: str, **extra: object) -> dict[str, object]:
    return {"id": step_id, "type": "agent", "prompt": "fix it", **extra}


def _agent_workspace(tmp_path: Path, agent: dict[str, object]) -> tuple[WorkspacePaths, RunsRepository]:
    workspace = (
        WorkspaceBuilder(tmp_path / "agent-workspace")
        .with_git()
        .with_database()
        .with_config(data={"version": 1, "project": {"name": "session"}, "agent": agent})
        .build()
    )
    paths = _paths_for(workspace)
    return paths, RunsRepository(db_path=paths.database_file, project_id=paths.project_id)


@pytest.fixture
def captured_contexts(monkeypatch: pytest.MonkeyPatch) -> list[StepExecutionContext]:
    """Contexts reaching StepExecution during drive_run, in construction order."""
    captured: list[StepExecutionContext] = []

    class _CapturingStepExecution(StepExecution):
        def __init__(self, metadata: StepExecutionContext) -> None:
            captured.append(metadata)
            super().__init__(metadata)

    monkeypatch.setattr("dovo.engine.step_coordinator.StepExecution", _CapturingStepExecution)
    return captured


@pytest.fixture
def captured_agent_args(monkeypatch: pytest.MonkeyPatch) -> list[ResolvedAgentSettings | None]:
    """Agent settings handed to build_agent_step_runner during drive_run, in call order."""
    captured: list[ResolvedAgentSettings | None] = []

    def _recording_build(agent: ResolvedAgentSettings | None, worktree_active: bool) -> AgentStepRunner:
        captured.append(agent)
        return build_agent_step_runner(agent, worktree_active)

    monkeypatch.setattr("dovo.engine.step_coordinator.build_agent_step_runner", _recording_build)
    return captured


@pytest.fixture
def noop_agent_provider(monkeypatch: pytest.MonkeyPatch) -> FakeAgentProvider:
    """FakeAgentProvider answering NO_OP, installed behind the agent factory seam."""
    provider = FakeAgentProvider(AgentResponse(status=AgentResponseStatus.NO_OP, summary="plan text"))
    monkeypatch.setattr(AGENT_ADAPTER_FACTORY, lambda token: provider)
    return provider


class DriveRunAgentSettingsTests:
    """[tier-1/integration] drive_run: effective agent settings resolved once per drive and propagated to StepExecution."""

    def test_fresh_run_threads_effective_config_into_step_execution_context(
        self,
        tmp_path: Path,
        captured_contexts: list[StepExecutionContext],
        captured_agent_args: list[ResolvedAgentSettings | None],
        noop_agent_provider: FakeAgentProvider,
    ) -> None:
        """[tier-1/integration] drive_run: a fresh row with no override and a copilot config reaches StepExecution with the full ResolvedAgentSettings and no 'agent' key in context."""
        paths, runs = _agent_workspace(tmp_path, _AGENT_CONFIG)
        seed_new_run(paths, runs, session_id="fresh", steps=[_agent_step("a")], use_worktree=True)

        outcome = _drive(paths, runs, "fresh")

        assert outcome.status == RunStatus.COMPLETED
        assert captured_agent_args == [
            ResolvedAgentSettings(
                provider="copilot",
                model="test-model",
                endpoint="http://127.0.0.1:9999",
                temperature=0.7,
                max_tokens=512,
            )
        ]
        assert "agent" not in (captured_contexts[0].context or {})

    def test_resumed_run_override_changes_only_the_provider(
        self,
        tmp_path: Path,
        captured_agent_args: list[ResolvedAgentSettings | None],
        noop_agent_provider: FakeAgentProvider,
    ) -> None:
        """[tier-1/integration] drive_run: a paused agent-step row with agent='fake-provider' resumed with RETRY reaches StepExecution with provider 'fake-provider' and the config's model, endpoint, temperature, max_tokens."""
        paths, runs = _agent_workspace(tmp_path, _AGENT_CONFIG)
        seed_paused_run(
            paths,
            runs,
            session_id="resumed",
            steps=[_agent_step("a", on_failure="prompt_user")],
            paused_step_id="a",
            use_worktree=True,
            agent="fake-provider",
        )

        outcome = _drive(paths, runs, "resumed", prompter=_Prompter([FailurePromptDecision.RETRY]))

        assert outcome.status == RunStatus.COMPLETED
        assert captured_agent_args == [
            ResolvedAgentSettings(
                provider="fake-provider",
                model="test-model",
                endpoint="http://127.0.0.1:9999",
                temperature=0.7,
                max_tokens=512,
            )
        ]

    def test_settings_are_resolved_once_per_drive(
        self,
        tmp_path: Path,
        captured_agent_args: list[ResolvedAgentSettings | None],
        noop_agent_provider: FakeAgentProvider,
    ) -> None:
        """[tier-1/integration] drive_run: two agent steps in one drive receive the identical ResolvedAgentSettings object."""
        paths, runs = _agent_workspace(tmp_path, _AGENT_CONFIG)
        seed_new_run(paths, runs, session_id="twice", steps=[_agent_step("a"), _agent_step("b")], use_worktree=True)

        _drive(paths, runs, "twice")

        assert len(captured_agent_args) == 2
        assert captured_agent_args[0] is not None
        assert captured_agent_args[0] is captured_agent_args[1]

    def test_config_resolution_failure_fails_run_without_running_steps(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] drive_run: a malformed repo config.json returns FAILED with errors[0] containing 'CONFIG_MALFORMED_JSON', no step results, and on_run_completed notified exactly once."""
        engine_paths.config_file.write_text("{not json", encoding="utf-8")
        seed_new_run(engine_paths, runs_repo, session_id="bad-config", steps=[_step("a", "echo hi")])
        observer = _SequenceObserver()

        outcome = _drive(engine_paths, runs_repo, "bad-config", observer=observer)

        assert outcome.status == RunStatus.FAILED
        assert "CONFIG_MALFORMED_JSON" in outcome.errors[0]
        assert outcome.step_results == []
        assert [call[0] for call in observer.calls] == ["run_completed"]

    def test_agent_step_with_unregistered_effective_provider_fails(self, tmp_path: Path) -> None:
        """[tier-1/integration] drive_run: an agent step whose run row sets agent 'unregistered' returns FAILED with the step's error_message containing "Unsupported agent provider 'unregistered' (AGENT_PROVIDER_UNSUPPORTED)"."""
        paths, runs = _agent_workspace(tmp_path, {})
        seed_new_run(
            paths,
            runs,
            session_id="unregistered",
            steps=[_agent_step("a")],
            use_worktree=True,
            agent="unregistered",
        )

        outcome = _drive(paths, runs, "unregistered")

        assert outcome.status == RunStatus.FAILED
        assert "Unsupported agent provider 'unregistered' (AGENT_PROVIDER_UNSUPPORTED)" in (
            outcome.step_results[-1].error_message or ""
        )


class DriveRunAgentWorktreeTests:
    """[tier-1/integration] drive_run: agent steps are rejected outside a Dovo Git worktree."""

    @pytest.mark.parametrize("resumed", [pytest.param(False, id="fresh"), pytest.param(True, id="resumed")])
    def test_in_place_run_rejects_agent_step_but_runs_command_steps(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resumed: bool
    ) -> None:
        """[tier-1/integration] drive_run: with use_worktree=False (fresh or resumed paused row), an agent step fails with errors containing WORKTREE_REQUIRED_MESSAGE, the factory spy records zero calls, and a command step in the same run completes in place."""
        requested: list[str] = []

        def _spy(token: str) -> FakeAgentProvider:
            requested.append(token)
            return FakeAgentProvider(AgentResponse(status=AgentResponseStatus.NO_OP))

        monkeypatch.setattr(AGENT_ADAPTER_FACTORY, _spy)
        paths, runs = _agent_workspace(tmp_path, _AGENT_CONFIG)
        steps = [
            _step("before", "true"),
            _agent_step("agent", on_failure="prompt_user"),
            _step("after", "echo after", **{"assert": {"output_contains": "after"}}),
        ]
        prompter = _DiagnosticPrompter([FailurePromptDecision.CONTINUE])
        if resumed:
            seed_paused_run(paths, runs, session_id="in-place", steps=steps, paused_step_id="agent")
            prompter.decisions.insert(0, FailurePromptDecision.RETRY)
        else:
            seed_new_run(paths, runs, session_id="in-place", steps=steps)

        outcome = _drive(paths, runs, "in-place", prompter=prompter)

        statuses = {result.step_id: result.status for result in outcome.step_results}
        assert statuses["before"] == "completed"
        assert statuses["agent"] == "ignored"
        assert statuses["after"] == "completed"
        assert WORKTREE_REQUIRED_MESSAGE in prompter.diagnostics[-1]
        assert requested == []


class DriveRunAgentExecutionTests:
    """[tier-1/integration] drive_run: agent steps execute through resolved providers inside the worktree."""

    def test_agent_patch_applies_only_in_worktree_and_following_assertion_passes(
        self, git_paths: WorkspacePaths, git_runs: RunsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] drive_run: a worktree-backed run [failing command with on_failure continue, agent prompt interpolating previous_step.status, cat of the patched file with an output assertion] returns COMPLETED, the single request is direct with no payload and instruction 'Handle ignored', the file is absent from the source checkout, and the cat step output carries the patched content."""

        def _write_file(request: AgentRequest) -> None:
            (request.worktree_path / "agent.txt").write_text("patched\n", encoding="utf-8")

        provider = FakeAgentProvider(
            AgentResponse(
                status=AgentResponseStatus.PROPOSED_PATCH,
                unified_diff=new_file_diff("agent.txt", "patched"),
            ),
            on_call=_write_file,
        )
        monkeypatch.setattr(AGENT_ADAPTER_FACTORY, lambda token: provider)
        seed_new_run(
            git_paths,
            git_runs,
            session_id="agent-patch",
            steps=[
                _step("fail", "exit 1", on_failure="continue"),
                {"id": "agent", "type": "agent", "prompt": "Handle ${{ previous_step.status }}"},
                _step("check", "cat agent.txt", **{"assert": {"output_contains": "patched"}}),
            ],
            use_worktree=True,
        )

        outcome = _drive(git_paths, git_runs, "agent-patch")

        assert outcome.status == RunStatus.COMPLETED
        assert len(provider.requests) == 1
        assert provider.requests[0].mode == "direct"
        assert provider.requests[0].payload is None
        assert provider.requests[0].instruction == "Handle ignored"
        assert not (git_paths.root_dir / "agent.txt").exists()
        check = next(result for result in outcome.step_results if result.step_id == "check")
        assert "patched" in check.stdout

    def test_provider_override_and_tuning_reach_the_provider(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] drive_run: config agent settings with row.agent 'fake-provider' asks the factory for 'fake-provider' and delivers an AgentRequest with the configured model, endpoint, temperature, max_tokens, and the step's timeout_seconds."""
        paths, runs = _agent_workspace(tmp_path, _AGENT_CONFIG)
        provider = FakeAgentProvider(AgentResponse(status=AgentResponseStatus.NO_OP))
        requested: list[str] = []

        def _factory(token: str) -> FakeAgentProvider:
            requested.append(token)
            return provider

        monkeypatch.setattr(AGENT_ADAPTER_FACTORY, _factory)
        seed_new_run(
            paths,
            runs,
            session_id="override",
            steps=[_agent_step("a", timeout_seconds=77)],
            use_worktree=True,
            agent="fake-provider",
        )

        outcome = _drive(paths, runs, "override")

        assert outcome.status == RunStatus.COMPLETED
        assert requested == ["fake-provider"]
        request = provider.requests[0]
        assert request.model == "test-model"
        assert request.endpoint == "http://127.0.0.1:9999"
        assert request.temperature == 0.7
        assert request.max_tokens == 512
        assert request.timeout_seconds == 77

    def test_no_op_planning_step_reports_summary_to_observer(
        self, git_paths: WorkspacePaths, git_runs: RunsRepository, noop_agent_provider: FakeAgentProvider
    ) -> None:
        """[tier-1/integration] drive_run: a NO_OP fake with summary 'plan text' returns COMPLETED and the observer's on_step_output receives the summary JSON line for the agent step."""
        seed_new_run(git_paths, git_runs, session_id="plan-only", steps=[_agent_step("plan")], use_worktree=True)
        observer = _LifecycleObserver()

        outcome = _drive(git_paths, git_runs, "plan-only", observer=observer)

        assert outcome.status == RunStatus.COMPLETED
        outputs = [event for event in observer.events if event[0] == "step_output" and event[3] == "plan"]
        assert [event[4] for event in outputs] == [
            '{"status":"no_op","summary":"plan text","unfixable_reason":null,"touched_files":[]}\n'
        ]
