"""Contract tests for drive_run: workspace lifecycle around one RunCoordinator execution."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import pytest

from dovo.common.filesystem.models import WorkspacePaths
from dovo.common.mcp_servers import McpServerDefinition
from dovo.common.tool_policy import ToolCapability, ToolPolicy, ToolRule
from dovo.core.agents import default_tool_policy
from dovo.core.agents.models import (
    AgentEnvOverrides,
    AgentRequest,
    AgentResponse,
    AgentResponseStatus,
    ResolvedAgentSettings,
)
from dovo.core.catalog.definitions import BlueprintStep
from dovo.core.db import SessionsRepository, SessionStatus, WorktreesRepository
from dovo.core.git.runner import GitRunner
from dovo.core.sessions.services.read_logs import read_session_log_events
from dovo.core.worktree import Worktree, WorktreeApplyResult, WorktreeCleanupResult
from dovo.engine.executors.agent_step import WORKTREE_REQUIRED_MESSAGE
from dovo.engine.executors.models import (
    StepExecutionContext,
)
from dovo.engine.models import (
    FailurePromptDecision,
    FailurePrompter,
    RunObserver,
    RunOutcome,
)
from dovo.engine.session import drive_run
from dovo.engine.writer import snapshot_blueprint_path
from tests.engine.conftest import AgentRunnerBuild
from tests.harness import AGENT_ADAPTER_FACTORY, FakeAgentProvider, new_file_diff
from tests.harness.builders import WorkspaceBuilder
from tests.harness.diagnostic_log import diagnostic_record
from tests.harness.fakes import FakeGitRunner, fixed_clock, fixed_new_id
from tests.harness.sessions import (
    NoOpRunObserver,
    RecordingRunObserver,
    ScriptedFailurePrompter,
    seed_new_session,
    seed_paused_session,
)
from tests.harness.workspace_paths import initialized_workspace_paths


class _RaisingRunLevelObserver(NoOpRunObserver):
    """RunObserver raising from both run-level callbacks."""

    def on_run_started(self, steps: Sequence[BlueprintStep]) -> None:
        raise RuntimeError("observer run started")

    def on_run_completed(self, outcome: RunOutcome) -> None:
        raise RuntimeError("observer run completed")


def _step(step_id: str, run: str, **extra: object) -> dict[str, object]:
    return {"id": step_id, "run": run, **extra}


def _drive(
    paths: WorkspacePaths,
    sessions: SessionsRepository,
    session_id: str,
    *,
    prompter: FailurePrompter | None = None,
    observer: RunObserver | None = None,
    env_overrides: AgentEnvOverrides | None = None,
    git: GitRunner | None = None,
) -> RunOutcome:
    return drive_run(
        paths,
        sessions,
        session_id,
        observer=observer,
        prompter=prompter,
        no_tty=False,
        env_overrides=env_overrides,
        git=git if git is not None else GitRunner(),
        clock=fixed_clock,
        new_id=fixed_new_id,
    )


@pytest.fixture
def git_paths(tmp_path: Path) -> WorkspacePaths:
    """Return WorkspacePaths for a git-backed workspace where worktrees can be created."""
    return initialized_workspace_paths(WorkspaceBuilder(tmp_path / "git-workspace").with_git().with_database().build())


@pytest.fixture
def git_runs(git_paths: WorkspacePaths) -> SessionsRepository:
    """SessionsRepository bound to the git-backed workspace database."""
    return SessionsRepository(db_path=git_paths.database_file, project_id=git_paths.project_id)


class DriveRunWorktreeTests:
    def test_resumed_run_reuses_retained_worktree_and_keeps_worktree_id(
        self, git_paths: WorkspacePaths, git_runs: SessionsRepository
    ) -> None:
        created = Worktree(
            git_paths,
            db=WorktreesRepository(db_path=git_paths.database_file, project_id=git_paths.project_id),
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        ).create(session_id="retained")
        assert created.session is not None
        worktree_id = created.session.session_id
        expected_cwd = git_paths.worktree_dir(worktree_id).resolve()
        seed_paused_session(
            git_paths,
            git_runs,
            session_id="resume-worktree",
            steps=[_step("a", "true"), _step("b", f'test "$(pwd -P)" = "{expected_cwd}"', on_failure="prompt_user")],
            paused_step_id="b",
            use_worktree=True,
            worktree_id=worktree_id,
        )

        outcome = _drive(
            git_paths, git_runs, "resume-worktree", prompter=ScriptedFailurePrompter([FailurePromptDecision.RETRY])
        )

        assert outcome.status == SessionStatus.COMPLETED
        assert outcome.worktree_id == worktree_id
        row = git_runs.get("resume-worktree")
        assert row is not None
        assert row.worktree_id == worktree_id

    def test_worktree_run_reports_session_id_and_removes_worktree(
        self, git_paths: WorkspacePaths, git_runs: SessionsRepository
    ) -> None:
        seed_new_session(
            git_paths, git_runs, session_id="worktree-done", steps=[_step("a", "echo hi")], use_worktree=True
        )

        outcome = _drive(git_paths, git_runs, "worktree-done")

        worktrees = WorktreesRepository(db_path=git_paths.database_file, project_id=git_paths.project_id).list()
        assert outcome.status == SessionStatus.COMPLETED
        assert [record.id for record in worktrees] == [outcome.worktree_id]
        assert outcome.worktree_kept is False
        assert not outcome.worktree_path.exists()

    def test_no_worktree_run_reports_none_worktree_id(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        seed_new_session(engine_paths, sessions_repo, session_id="no-worktree", steps=[_step("a", "echo hi")])

        outcome = _drive(engine_paths, sessions_repo, "no-worktree")

        assert outcome.worktree_id is None
        assert outcome.worktree_path == engine_paths.root_dir.resolve()

    def test_worktree_creation_failure_returns_failed_outcome_without_running_steps(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, engine_workspace: Path
    ) -> None:
        seed_new_session(
            engine_paths, sessions_repo, session_id="no-git", steps=[_step("a", "touch a.ran")], use_worktree=True
        )
        observer = RecordingRunObserver()

        outcome = _drive(engine_paths, sessions_repo, "no-git", observer=observer)

        assert outcome.status == SessionStatus.FAILED
        assert outcome.errors[0].startswith("Git worktree creation failed:")
        assert not (engine_workspace / "a.ran").exists()
        assert [call for call in observer.calls if str(call[0]).startswith("run_")] == [("run_completed", outcome)]


class DriveRunStorageBridgeTests:
    def test_bridge_collision_fails_run_without_running_steps_or_deleting_content(
        self, git_paths: WorkspacePaths, git_runs: SessionsRepository
    ) -> None:
        source_sentinel = git_paths.root_dir / ".dovo" / "run" / "sentinel.txt"
        source_sentinel.parent.mkdir(parents=True)
        source_sentinel.write_text("do not delete", encoding="utf-8")
        GitRunner().run(["add", "-f", ".dovo/run/sentinel.txt"], path=git_paths.root_dir)
        GitRunner().run(["commit", "-m", "Add storage bridge collision"], path=git_paths.root_dir)
        step_marker = git_paths.root_dir / "a.ran"
        seed_new_session(
            git_paths,
            git_runs,
            session_id="bridge-collision",
            steps=[_step("a", f"touch '{step_marker}'")],
            use_worktree=True,
        )
        bridge_path = git_paths.worktree_dir("bridge-collision").resolve() / ".dovo" / "run"

        outcome = _drive(git_paths, git_runs, "bridge-collision")

        assert outcome.status == SessionStatus.FAILED
        assert outcome.errors == [
            f"Worktree storage bridge failed: Worktree storage bridge path '{bridge_path}' is not a symlink."
        ]
        assert outcome.worktree_kept is False
        assert not step_marker.exists()
        assert source_sentinel.read_text(encoding="utf-8") == "do not delete"

    def test_link_failure_outcome_carries_setup_warnings_and_diagnostics(
        self, git_paths: WorkspacePaths, git_runs: SessionsRepository, frozen_diagnostic_clock: Path
    ) -> None:
        """Return a FAILED outcome whose errors hold the bridge error, warnings hold "Failed to remove worktree '<path>': Failed to delete branch 'dovo/<id>': boom", and diagnostics == [diagnostic_record(...)], when the storage bridge fails and git branch -D raises RuntimeError("boom") during discard."""
        collision = git_paths.root_dir / ".dovo" / "run" / "sentinel.txt"
        collision.parent.mkdir(parents=True)
        collision.write_text("collision", encoding="utf-8")
        GitRunner().run(["add", "-f", ".dovo/run/sentinel.txt"], path=git_paths.root_dir)
        GitRunner().run(["commit", "-m", "Add storage bridge collision"], path=git_paths.root_dir)
        seed_new_session(
            git_paths, git_runs, session_id="bridge-discard", steps=[_step("a", "true")], use_worktree=True
        )
        worktree_path = git_paths.worktree_dir("bridge-discard").resolve()

        outcome = _drive(
            git_paths,
            git_runs,
            "bridge-discard",
            git=FakeGitRunner({("branch", "-D"): RuntimeError("boom")}),
        )

        assert outcome.status == SessionStatus.FAILED
        assert outcome.errors == [
            f"Worktree storage bridge failed: Worktree storage bridge path '{worktree_path / '.dovo' / 'run'}' "
            "is not a symlink."
        ]
        assert outcome.warnings == [
            f"Failed to remove worktree '{worktree_path}': Failed to delete branch 'dovo/bridge-discard': boom"
        ]
        assert outcome.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]


class SessionRunnerWorktreeDiagnosticsTests:
    def test_run_removal_record_in_outcome_diagnostics(
        self, git_paths: WorkspacePaths, git_runs: SessionsRepository, frozen_diagnostic_clock: Path
    ) -> None:
        """Keep a passing run's status COMPLETED, hold "Failed to remove worktree '<path>': Failed to delete branch 'dovo/<id>': boom" in warnings with no 'Details logged:' text, and diagnostics == [diagnostic_record(...)], when git branch -D raises RuntimeError("boom") during discard."""
        seed_new_session(
            git_paths, git_runs, session_id="removal-record", steps=[_step("a", "echo hi")], use_worktree=True
        )
        worktree_path = git_paths.worktree_dir("removal-record").resolve()

        outcome = _drive(
            git_paths,
            git_runs,
            "removal-record",
            git=FakeGitRunner({("branch", "-D"): RuntimeError("boom")}),
        )

        assert outcome.status == SessionStatus.COMPLETED
        assert (
            f"Failed to remove worktree '{worktree_path}': Failed to delete branch 'dovo/removal-record': boom"
            in outcome.warnings
        )
        assert not any("Details logged:" in warning for warning in outcome.warnings)
        assert outcome.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]

    def test_auto_apply_copies_apply_result_diagnostics(
        self,
        git_paths: WorkspacePaths,
        git_runs: SessionsRepository,
        monkeypatch: pytest.MonkeyPatch,
        frozen_diagnostic_clock: Path,
    ) -> None:
        """Mark the run FAILED with the apply's errors appended to outcome.errors, its warnings to outcome.warnings, and diagnostics ending with the apply's record, with no 'Details logged:' text, when Worktree.apply returns a failed result carrying one record."""
        failed = WorktreeApplyResult(
            worktree_id="apply-record",
            error_code="INTERNAL_ERROR",
            errors=["Unexpected error during worktree.apply: boom"],
            warnings=["apply warning"],
            diagnostics=[diagnostic_record(frozen_diagnostic_clock)],
        )
        monkeypatch.setattr(Worktree, "apply", lambda *args, **kwargs: failed)
        seed_new_session(
            git_paths,
            git_runs,
            session_id="apply-record",
            steps=[_step("a", "echo change")],
            use_worktree=True,
            auto_apply=True,
        )

        outcome = _drive(git_paths, git_runs, "apply-record")

        assert outcome.status == SessionStatus.FAILED
        assert outcome.errors == ["Unexpected error during worktree.apply: boom"]
        assert outcome.warnings[-1] == "apply warning"
        assert not any("Details logged:" in message for message in [*outcome.errors, *outcome.warnings])
        assert outcome.diagnostics[-1] == diagnostic_record(frozen_diagnostic_clock)

    def test_auto_apply_unexpected_failure_record_in_outcome_diagnostics(
        self, git_paths: WorkspacePaths, git_runs: SessionsRepository, frozen_diagnostic_clock: Path
    ) -> None:
        """Mark an auto-apply run FAILED with "Unexpected error during worktree.apply: boom" in errors, no 'Details logged:' text, and diagnostics == [diagnostic_record(...)] when ("apply","--check") raises TypeError("boom")."""
        seed_new_session(
            git_paths,
            git_runs,
            session_id="apply-boom",
            steps=[_step("a", "echo change > tracked.txt")],
            use_worktree=True,
            auto_apply=True,
        )

        outcome = _drive(
            git_paths, git_runs, "apply-boom", git=FakeGitRunner({("apply", "--check"): TypeError("boom")})
        )

        assert outcome.status == SessionStatus.FAILED
        assert "Unexpected error during worktree.apply: boom" in outcome.errors
        assert not any("Details logged:" in message for message in [*outcome.errors, *outcome.warnings])
        assert outcome.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]


class DriveRunMissingRowTests:
    def test_unknown_session_returns_failed_outcome_without_running_steps_or_notifying(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        observer = RecordingRunObserver()

        outcome = _drive(engine_paths, sessions_repo, "missing", observer=observer)

        assert outcome.status == SessionStatus.FAILED
        assert outcome.errors == ["Session 'missing' not found."]
        assert outcome.worktree_path == engine_paths.root_dir
        assert observer.calls == []


class DriveRunRetentionTests:
    @pytest.mark.parametrize(
        ("step", "keep", "discard", "decisions", "expected_status", "expected_kept"),
        [
            pytest.param(_step("a", "exit 1"), False, False, [], SessionStatus.FAILED, True, id="failed-default"),
            pytest.param(_step("a", "exit 1"), False, True, [], SessionStatus.FAILED, False, id="failed-discard"),
            pytest.param(_step("a", "true"), True, False, [], SessionStatus.COMPLETED, True, id="completed-keep"),
            pytest.param(
                _step("a", "exit 1", on_failure="prompt_user"),
                False,
                False,
                [KeyboardInterrupt()],
                SessionStatus.PAUSED,
                True,
                id="paused",
            ),
        ],
    )
    def test_outcome_reports_worktree_retention(
        self,
        git_paths: WorkspacePaths,
        git_runs: SessionsRepository,
        step: dict[str, object],
        keep: bool,
        discard: bool,
        decisions: list[KeyboardInterrupt],
        expected_status: SessionStatus,
        expected_kept: bool,
    ) -> None:
        seed_new_session(
            git_paths, git_runs, session_id="retention", steps=[step], use_worktree=True, keep=keep, discard=discard
        )

        outcome = _drive(git_paths, git_runs, "retention", prompter=ScriptedFailurePrompter(decisions))

        assert outcome.status == expected_status
        assert outcome.worktree_kept is expected_kept
        assert outcome.worktree_id == "retention"
        assert outcome.worktree_path.is_dir() is expected_kept


class DriveRunCleanupTests:
    def test_diff_persisted_under_session_id_not_worktree_key(
        self, git_paths: WorkspacePaths, git_runs: SessionsRepository
    ) -> None:
        seed_new_session(
            git_paths,
            git_runs,
            session_id="diff-run",
            steps=[_step("a", "echo change > tracked.txt")],
            use_worktree=True,
        )

        outcome = _drive(git_paths, git_runs, "diff-run")

        assert outcome.status == SessionStatus.COMPLETED
        diff_file = git_paths.session_dir("diff-run") / "diff.patch"
        assert "tracked.txt" in diff_file.read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        ("auto_apply", "expected_status", "expected_kept"),
        [
            pytest.param(True, SessionStatus.FAILED, True, id="auto-apply-conflict"),
            pytest.param(False, SessionStatus.COMPLETED, False, id="auto-apply-off"),
        ],
    )
    def test_auto_apply_read_from_row_conflict_marks_run_failed_and_keeps_worktree(
        self,
        git_paths: WorkspacePaths,
        git_runs: SessionsRepository,
        monkeypatch: pytest.MonkeyPatch,
        auto_apply: bool,
        expected_status: SessionStatus,
        expected_kept: bool,
    ) -> None:
        conflict = WorktreeApplyResult(
            worktree_id="test-session",
            error_code="WORKTREE_APPLY_CONFLICT",
            errors=["Patch merge conflict in worktree apply"],
            warnings=["Patch hunk rejected"],
        )
        monkeypatch.setattr(Worktree, "apply", lambda *args, **kwargs: conflict)
        seed_new_session(
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
        self,
        git_paths: WorkspacePaths,
        git_runs: SessionsRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:

        def exploding_cleanup(self: Worktree, session: object, **kwargs: object) -> WorktreeCleanupResult:
            return WorktreeCleanupResult(
                error_code="INTERNAL_ERROR",
                errors=["Unexpected error during worktree.cleanup: Cleanup filesystem removal failed"],
            )

        monkeypatch.setattr(Worktree, "cleanup", exploding_cleanup)
        seed_new_session(
            git_paths,
            git_runs,
            session_id="cleanup",
            steps=[_step("fail", "exit 1")],
            use_worktree=True,
            discard=True,
        )

        outcome = _drive(git_paths, git_runs, "cleanup")

        assert outcome.status == SessionStatus.FAILED
        assert outcome.worktree_kept is False
        assert outcome.warnings == [
            f"Failed to remove worktree '{outcome.worktree_path}': "
            "Unexpected error during worktree.cleanup: Cleanup filesystem removal failed"
        ]
        assert outcome.diagnostics == []
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
        sessions_repo: SessionsRepository,
        keep: bool,
        command: str,
        expect_preserved: bool,
    ) -> None:
        seed_new_session(engine_paths, sessions_repo, session_id="scratch", steps=[_step("s1", command)], keep=keep)

        outcome = _drive(engine_paths, sessions_repo, "scratch")

        assert outcome.step_results[0].exit_code == (1 if command.endswith("exit 1") else 0)
        assert (engine_paths.tmp_dir / "scratch").exists() is expect_preserved

    def test_resume_reuses_same_session_tmp_dir_and_truncates_stale_output(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        seed_paused_session(
            engine_paths,
            sessions_repo,
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

        outcome = _drive(
            engine_paths, sessions_repo, "resume-tmp", prompter=ScriptedFailurePrompter([FailurePromptDecision.RETRY])
        )

        assert outcome.status == SessionStatus.COMPLETED
        assert outcome.step_results[-1].outputs == {"fresh": "yes"}

    def test_session_log_round_trips_through_reader_for_three_step_run(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        seed_new_session(
            engine_paths,
            sessions_repo,
            session_id="timeline3",
            steps=[_step("a", "echo a"), _step("b", "echo b"), _step("c", "echo c")],
        )

        _drive(engine_paths, sessions_repo, "timeline3")
        log_dir = engine_paths.logs_dir / "timeline3"

        events = read_session_log_events(log_dir, tail=None)

        assert len(events) == len((log_dir / "session.log").read_text(encoding="utf-8").splitlines())
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
            ("session_started", {"session_id": "timeline3", "blueprint_key": "timeline3"}),
            *step_events,
            ("session_completed", {"status": "completed"}),
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
        workspace = (
            WorkspaceBuilder(tmp_path / "config-workspace")
            .with_database()
            .with_config(
                data={"version": 1, "project": {"name": "session"}, "history": {"save_attempt_logs": save_attempt_logs}}
            )
            .build()
        )
        paths = initialized_workspace_paths(workspace)
        sessions = SessionsRepository(db_path=paths.database_file, project_id=paths.project_id)
        seed_new_session(paths, sessions, session_id="attempt-logs", steps=[_step("a", "echo a")])

        _drive(paths, sessions, "attempt-logs")

        attempt_logs = list((paths.logs_dir / "attempt-logs").glob("*attempt*"))
        assert bool(attempt_logs) is expect_log

    def test_step_output_streams_to_observer_by_line_and_stream(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        seed_new_session(
            engine_paths,
            sessions_repo,
            session_id="streamed",
            steps=[_step("s1", "echo out; echo err >&2")],
        )
        observer = RecordingRunObserver()

        _drive(engine_paths, sessions_repo, "streamed", observer=observer)

        output_events = [event for event in observer.calls if event[0] == "step_output"]
        assert sorted(output_events) == [
            ("step_output", 1, 1, "s1", "err\n", "stderr"),
            ("step_output", 1, 1, "s1", "out\n", "stdout"),
        ]


class SessionRunnerDiagnosticLogTests:
    def test_launch_failure_records_into_run_diagnostics_not_session_state(
        self,
        git_paths: WorkspacePaths,
        git_runs: SessionsRepository,
        frozen_diagnostic_clock: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Fail the step with no line, hold no 'Details logged:' warning, set diagnostics == [the engine.step record] and leave the session's state without the line."""

        def _exec_format_error(*_args: object, **_kwargs: object) -> None:
            raise OSError("exec format error")

        monkeypatch.setattr("dovo.engine.executors.step_executor.subprocess.Popen", _exec_format_error)
        seed_new_session(git_paths, git_runs, session_id="launch", steps=[_step("a", "true")], use_worktree=False)

        outcome = _drive(git_paths, git_runs, "launch")

        assert outcome.status == SessionStatus.FAILED
        assert outcome.errors == ["Step 'a' failed: Command execution error: exec format error"]
        assert [warning for warning in outcome.warnings if "Details logged:" in warning] == []
        assert outcome.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        _assert_state_without_line(git_paths, git_runs, "launch")
        session_json = (git_paths.session_dir("launch") / "session.json").read_text(encoding="utf-8")
        assert "Command execution error: exec format error" in session_json

    @pytest.mark.parametrize("site", ["scratch", "edit-check"])
    def test_agent_step_records_reach_run_diagnostics_not_session_state(
        self, tmp_path: Path, frozen_diagnostic_clock: Path, monkeypatch: pytest.MonkeyPatch, site: str
    ) -> None:
        """Fail the agent step with no line, set diagnostics == [the site's record], and leave the session's state without 'Details logged:'."""
        record = diagnostic_record(frozen_diagnostic_clock)
        if site == "scratch":

            def _boom(**_kwargs: object) -> None:
                raise RuntimeError("boom")

            monkeypatch.setattr("dovo.engine.executors.agent_step.allocate_invocation_paths", _boom)
            expected_error = "Unexpected error during engine.agent_step: boom"
        else:
            expected_error = "Unexpected error during agents.invoke: boom"
        provider = FakeAgentProvider(
            AgentResponse(status=AgentResponseStatus.PROVIDER_ERROR, errors=[expected_error], diagnostics=[record])
        )
        monkeypatch.setattr(AGENT_ADAPTER_FACTORY, lambda token: provider)
        paths, sessions = _agent_workspace(tmp_path, {"copilot": _AGENT_CONFIG})
        seed_new_session(paths, sessions, session_id=site, steps=[_agent_step("a")], use_worktree=True)

        outcome = _drive(paths, sessions, site)

        assert outcome.status == SessionStatus.FAILED
        assert outcome.errors == [f"Step 'a' failed: {expected_error}"]
        assert outcome.diagnostics == [record]
        _assert_state_without_line(paths, sessions, site)


def _assert_state_without_line(paths: WorkspacePaths, sessions: SessionsRepository, session_id: str) -> None:
    """Assert the row, its execution_state_json and session.json hold no 'Details logged:' text."""
    row = sessions.get(session_id)
    assert row is not None
    session_json = (paths.session_dir(session_id) / "session.json").read_text(encoding="utf-8")
    assert "Details logged:" not in (row.error_message or "")
    assert "Details logged:" not in (row.execution_state_json or "")
    assert "Details logged:" not in session_json


class DriveRunObserverContractTests:
    def test_linear_run_emits_pinned_observer_sequence(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        seed_new_session(
            engine_paths,
            sessions_repo,
            session_id="seq-linear",
            steps=[_step("a", "true"), _step("b", "true"), _step("c", "true")],
        )
        observer = RecordingRunObserver()

        outcome = _drive(engine_paths, sessions_repo, "seq-linear", observer=observer)

        root = engine_paths.root_dir.resolve()
        assert outcome.status == SessionStatus.COMPLETED
        assert observer.calls == [
            ("worktree_ready", root, False),
            ("run_started", ["a", "b", "c"]),
            ("step_start", 1, 3, "a"),
            ("step_done", 1, 3, "a", "completed"),
            ("step_start", 2, 3, "b"),
            ("step_done", 2, 3, "b", "completed"),
            ("step_start", 3, 3, "c"),
            ("step_done", 3, 3, "c", "completed"),
            ("worktree_cleanup", False, root, None, None, []),
            ("run_completed", outcome),
        ]

    def test_run_completed_receives_paused_outcome_and_resume_gets_its_own_pair(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, engine_workspace: Path
    ) -> None:
        marker = engine_workspace / "retry.marker"
        seed_new_session(
            engine_paths,
            sessions_repo,
            session_id="seq-pause",
            steps=[_step("a", f"test -e {marker}", on_failure="prompt_user")],
        )
        observer = RecordingRunObserver()

        paused = _drive(
            engine_paths,
            sessions_repo,
            "seq-pause",
            prompter=ScriptedFailurePrompter([KeyboardInterrupt()]),
            observer=observer,
        )
        marker.touch()
        resumed = _drive(
            engine_paths,
            sessions_repo,
            "seq-pause",
            prompter=ScriptedFailurePrompter([FailurePromptDecision.RETRY]),
            observer=observer,
        )

        assert paused.status == SessionStatus.PAUSED
        assert resumed.status == SessionStatus.COMPLETED
        run_level = [call for call in observer.calls if call[0] in {"run_started", "run_completed"}]
        assert run_level == [
            ("run_started", ["a"]),
            ("run_completed", paused),
            ("run_started", ["a"]),
            ("run_completed", resumed),
        ]

    def test_missing_snapshot_skips_run_started_and_completes_failed(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        seed_new_session(engine_paths, sessions_repo, session_id="seq-nosnap", steps=[_step("a", "true")])
        snapshot_blueprint_path(engine_paths.session_dir("seq-nosnap"), "seq-nosnap").unlink()
        observer = RecordingRunObserver()

        outcome = _drive(engine_paths, sessions_repo, "seq-nosnap", observer=observer)

        assert outcome.status == SessionStatus.FAILED
        assert [call for call in observer.calls if str(call[0]).startswith("run_")] == [("run_completed", outcome)]

    def test_raising_run_level_observer_leaves_outcome_unchanged(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        for session_id in ("raise-plain", "raise-observed"):
            seed_new_session(engine_paths, sessions_repo, session_id=session_id, steps=[_step("a", "true")])

        plain = _drive(engine_paths, sessions_repo, "raise-plain", observer=NoOpRunObserver())
        observed = _drive(engine_paths, sessions_repo, "raise-observed", observer=_RaisingRunLevelObserver())

        assert observed.status == plain.status == SessionStatus.COMPLETED
        assert [r.status for r in observed.step_results] == [r.status for r in plain.step_results]
        assert observed.errors == plain.errors
        assert observed.warnings == plain.warnings


_AGENT_CONFIG: dict[str, object] = {
    "model": "test-model",
    "endpoint": "http://127.0.0.1:9999",
    "temperature": 0.7,
    "max_tokens": 512,
}
_CLAUDE_CONFIG: dict[str, object] = {
    "model": "claude-model",
    "endpoint": "http://127.0.0.1:9998",
    "temperature": 1.1,
    "max_tokens": 256,
    "tools": {"allow_all": True},
}


def _agent_step(step_id: str, **extra: object) -> dict[str, object]:
    return {"id": step_id, "type": "agent", "prompt": "fix it", **extra}


def _agent_workspace(tmp_path: Path, agents: dict[str, object]) -> tuple[WorkspacePaths, SessionsRepository]:
    workspace = (
        WorkspaceBuilder(tmp_path / "agent-workspace")
        .with_git()
        .with_database()
        .with_config(data={"version": 1, "project": {"name": "session"}, "default_agent": "copilot", "agents": agents})
        .build()
    )
    paths = initialized_workspace_paths(workspace)
    return paths, SessionsRepository(db_path=paths.database_file, project_id=paths.project_id)


@pytest.fixture
def noop_agent_provider(monkeypatch: pytest.MonkeyPatch) -> FakeAgentProvider:
    """FakeAgentProvider answering NO_OP, installed behind the agent factory seam."""
    provider = FakeAgentProvider(AgentResponse(status=AgentResponseStatus.NO_OP, summary="plan text"))
    monkeypatch.setattr(AGENT_ADAPTER_FACTORY, lambda token: provider)
    return provider


class ResolveAgentSettingsEnvTests:
    def test_flag_entries_append_to_config_entries_without_duplicates(
        self,
        tmp_path: Path,
        captured_builds: list[AgentRunnerBuild],
        noop_agent_provider: FakeAgentProvider,
    ) -> None:
        paths, sessions = _agent_workspace(tmp_path, {"copilot": {**_AGENT_CONFIG, "env_passthrough": ["A", "B"]}})
        seed_new_session(paths, sessions, session_id="flags", steps=[_agent_step("a")], use_worktree=True)

        _drive(paths, sessions, "flags", env_overrides=AgentEnvOverrides(env_passthrough=["B", "C"]))

        assert captured_builds[0].agent is not None
        assert captured_builds[0].agent.env_passthrough == ["A", "B", "C"]
        assert captured_builds[0].agent.env_mode == "allowlist"

    def test_flag_mode_overrides_config_mode_for_that_drive_only(
        self,
        tmp_path: Path,
        captured_builds: list[AgentRunnerBuild],
        noop_agent_provider: FakeAgentProvider,
    ) -> None:
        paths, sessions = _agent_workspace(tmp_path, {"copilot": _AGENT_CONFIG})
        before = paths.config_file.read_bytes()
        seed_new_session(paths, sessions, session_id="mode", steps=[_agent_step("a")], use_worktree=True)
        seed_new_session(paths, sessions, session_id="plain", steps=[_agent_step("a")], use_worktree=True)

        _drive(paths, sessions, "mode", env_overrides=AgentEnvOverrides(env_mode="inherit"))
        _drive(paths, sessions, "plain")

        assert [build.agent.env_mode for build in captured_builds if build.agent is not None] == [
            "inherit",
            "allowlist",
        ]
        assert paths.config_file.read_bytes() == before


_COPILOT_SETTINGS = ResolvedAgentSettings(
    provider="copilot",
    model="test-model",
    endpoint="http://127.0.0.1:9999",
    temperature=0.7,
    max_tokens=512,
    tools=default_tool_policy(),
)


def _field_default_settings(provider: str) -> ResolvedAgentSettings:
    return ResolvedAgentSettings(
        provider=provider, model=None, endpoint=None, temperature=0.2, max_tokens=4096, tools=default_tool_policy()
    )


class DriveRunAgentSettingsTests:
    @pytest.mark.parametrize(
        ("agents", "agent", "expected"),
        [
            pytest.param(
                {"copilot": _AGENT_CONFIG, "claude": _CLAUDE_CONFIG}, None, _COPILOT_SETTINGS, id="default-agent"
            ),
            pytest.param(
                {
                    "copilot": {
                        **_AGENT_CONFIG,
                        "tools": {
                            "allow": [{"capability": "shell", "pattern": "git status"}],
                            "deny": [{"capability": "network"}],
                        },
                    }
                },
                None,
                _COPILOT_SETTINGS.model_copy(
                    update={
                        "tools": ToolPolicy(
                            allow=[ToolRule(capability=ToolCapability.SHELL, pattern="git status")],
                            deny=[ToolRule(capability=ToolCapability.NETWORK)],
                        )
                    }
                ),
                id="configured-tools-pass-through",
            ),
            pytest.param(
                {"copilot": {**_AGENT_CONFIG, "tools": {}}},
                None,
                _COPILOT_SETTINGS.model_copy(update={"tools": ToolPolicy()}),
                id="configured-empty-tools-grant-nothing",
            ),
            pytest.param(
                {"copilot": {**_AGENT_CONFIG, "mcp_servers": {"github": {"command": "npx"}}}},
                None,
                _COPILOT_SETTINGS.model_copy(update={"mcp_servers": {"github": McpServerDefinition(command="npx")}}),
                id="configured-mcp-servers",
            ),
            pytest.param(
                {
                    "copilot": {**_AGENT_CONFIG, "tools": {"deny": [{"capability": "network"}]}},
                    "claude": _CLAUDE_CONFIG,
                },
                "claude",
                ResolvedAgentSettings(
                    provider="claude",
                    model="claude-model",
                    endpoint="http://127.0.0.1:9998",
                    temperature=1.1,
                    max_tokens=256,
                    tools=ToolPolicy(allow_all=True),
                ),
                id="override-uses-its-own-block",
            ),
            pytest.param(
                {"copilot": _AGENT_CONFIG}, "claude", _field_default_settings("claude"), id="override-without-block"
            ),
        ],
    )
    def test_fresh_run_threads_effective_config_into_step_execution_context(
        self,
        tmp_path: Path,
        captured_builds: list[AgentRunnerBuild],
        noop_agent_provider: FakeAgentProvider,
        agents: dict[str, object],
        agent: str | None,
        expected: ResolvedAgentSettings,
    ) -> None:
        paths, sessions = _agent_workspace(tmp_path, agents)
        seed_new_session(paths, sessions, session_id="fresh", steps=[_agent_step("a")], use_worktree=True, agent=agent)

        outcome = _drive(paths, sessions, "fresh")

        assert outcome.status == SessionStatus.COMPLETED
        assert [build.agent for build in captured_builds] == [expected]

    def test_open_resolves_sensitive_variables_into_run_context_and_workspace_settings(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        captured_contexts: list[StepExecutionContext],
    ) -> None:
        monkeypatch.setenv("PIN", "1234")
        workspace = (
            WorkspaceBuilder(tmp_path / "env-workspace")
            .with_git()
            .with_database()
            .with_config(
                data={
                    "version": 1,
                    "project": {"name": "session"},
                    "environment": {"sensitive_variables": ["PIN", "DB_URL"]},
                }
            )
            .build()
        )
        paths = initialized_workspace_paths(workspace)
        sessions = SessionsRepository(db_path=paths.database_file, project_id=paths.project_id)
        seed_new_session(
            paths, sessions, session_id="env-run", steps=[_step("write", "echo pin=1234 > pin.txt")], use_worktree=True
        )

        outcome = _drive(paths, sessions, "env-run")

        diff_text = (paths.session_dir("env-run") / "diff.patch").read_text(encoding="utf-8")
        assert outcome.status == SessionStatus.COMPLETED
        assert captured_contexts[0].sensitive_variables == ("PIN", "DB_URL")
        assert "+pin=[REDACTED:PIN]" in diff_text
        assert "1234" not in diff_text

    def test_resumed_run_override_resolves_field_defaults_for_provider_without_block(
        self,
        tmp_path: Path,
        captured_builds: list[AgentRunnerBuild],
        noop_agent_provider: FakeAgentProvider,
    ) -> None:
        paths, sessions = _agent_workspace(tmp_path, {"copilot": _AGENT_CONFIG})
        seed_paused_session(
            paths,
            sessions,
            session_id="resumed",
            steps=[_agent_step("a", on_failure="prompt_user")],
            paused_step_id="a",
            use_worktree=True,
            agent="fake-provider",
        )

        outcome = _drive(paths, sessions, "resumed", prompter=ScriptedFailurePrompter([FailurePromptDecision.RETRY]))

        assert outcome.status == SessionStatus.COMPLETED
        assert [build.agent for build in captured_builds] == [_field_default_settings("fake-provider")]

    def test_settings_are_resolved_once_per_drive(
        self,
        tmp_path: Path,
        captured_builds: list[AgentRunnerBuild],
        noop_agent_provider: FakeAgentProvider,
    ) -> None:
        paths, sessions = _agent_workspace(tmp_path, {"copilot": _AGENT_CONFIG})
        seed_new_session(
            paths, sessions, session_id="twice", steps=[_agent_step("a"), _agent_step("b")], use_worktree=True
        )

        _drive(paths, sessions, "twice")

        assert len(captured_builds) == 2
        assert captured_builds[0].agent is not None
        assert captured_builds[0].agent is captured_builds[1].agent

    def test_config_resolution_failure_fails_run_without_running_steps(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        engine_paths.config_file.write_text("{not json", encoding="utf-8")
        seed_new_session(engine_paths, sessions_repo, session_id="bad-config", steps=[_step("a", "echo hi")])
        observer = RecordingRunObserver()

        outcome = _drive(engine_paths, sessions_repo, "bad-config", observer=observer)

        assert outcome.status == SessionStatus.FAILED
        assert "Malformed config.json at" in outcome.errors[0]
        assert outcome.step_results == []
        assert [call[0] for call in observer.calls] == ["run_completed"]

    def test_agent_step_with_unregistered_effective_provider_fails(self, tmp_path: Path) -> None:
        paths, sessions = _agent_workspace(tmp_path, {})
        seed_new_session(
            paths,
            sessions,
            session_id="unregistered",
            steps=[_agent_step("a")],
            use_worktree=True,
            agent="unregistered",
        )

        outcome = _drive(paths, sessions, "unregistered")

        assert outcome.status == SessionStatus.FAILED
        assert "Unsupported agent provider 'unregistered'." in (outcome.step_results[-1].error_message or "")


class DriveRunAgentWorktreeTests:
    @pytest.mark.parametrize("resumed", [pytest.param(False, id="fresh"), pytest.param(True, id="resumed")])
    def test_in_place_run_rejects_agent_step_but_runs_command_steps(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resumed: bool
    ) -> None:
        requested: list[str] = []

        def _spy(token: str) -> FakeAgentProvider:
            requested.append(token)
            return FakeAgentProvider(AgentResponse(status=AgentResponseStatus.NO_OP))

        monkeypatch.setattr(AGENT_ADAPTER_FACTORY, _spy)
        paths, sessions = _agent_workspace(tmp_path, {"copilot": _AGENT_CONFIG})
        steps = [
            _step("before", "true"),
            _agent_step("agent", on_failure="prompt_user"),
            _step("after", "echo after", **{"assert": {"output_contains": "after"}}),
        ]
        prompter = ScriptedFailurePrompter([FailurePromptDecision.CONTINUE])
        if resumed:
            seed_paused_session(paths, sessions, session_id="in-place", steps=steps, paused_step_id="agent")
            prompter.decisions.insert(0, FailurePromptDecision.RETRY)
        else:
            seed_new_session(paths, sessions, session_id="in-place", steps=steps)

        outcome = _drive(paths, sessions, "in-place", prompter=prompter)

        statuses = {result.step_id: result.status for result in outcome.step_results}
        assert statuses["before"] == "completed"
        assert statuses["agent"] == "ignored"
        assert statuses["after"] == "completed"
        assert WORKTREE_REQUIRED_MESSAGE in prompter.diagnostics[-1]
        assert requested == []


class DriveRunAgentExecutionTests:
    def test_agent_patch_applies_only_in_worktree_and_following_assertion_passes(
        self, git_paths: WorkspacePaths, git_runs: SessionsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:

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
        seed_new_session(
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

        assert outcome.status == SessionStatus.COMPLETED
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
        paths, sessions = _agent_workspace(tmp_path, {"copilot": _AGENT_CONFIG, "claude": _CLAUDE_CONFIG})
        provider = FakeAgentProvider(AgentResponse(status=AgentResponseStatus.NO_OP))
        requested: list[str] = []

        def _factory(token: str) -> FakeAgentProvider:
            requested.append(token)
            return provider

        monkeypatch.setattr(AGENT_ADAPTER_FACTORY, _factory)
        seed_new_session(
            paths,
            sessions,
            session_id="override",
            steps=[_agent_step("a", timeout_seconds=77)],
            use_worktree=True,
            agent="claude",
        )

        outcome = _drive(paths, sessions, "override")

        assert outcome.status == SessionStatus.COMPLETED
        assert requested == ["claude"]
        assert provider.requests[0].timeout_seconds == 77

    def test_no_op_planning_step_reports_summary_to_observer(
        self, git_paths: WorkspacePaths, git_runs: SessionsRepository, noop_agent_provider: FakeAgentProvider
    ) -> None:
        seed_new_session(git_paths, git_runs, session_id="plan-only", steps=[_agent_step("plan")], use_worktree=True)
        observer = RecordingRunObserver()

        outcome = _drive(git_paths, git_runs, "plan-only", observer=observer)

        assert outcome.status == SessionStatus.COMPLETED
        outputs = [event for event in observer.calls if event[0] == "step_output" and event[3] == "plan"]
        assert [event[4] for event in outputs] == [
            '{"status":"no_op","summary":"plan text","unfixable_reason":null,"touched_files":[]}\n'
        ]
