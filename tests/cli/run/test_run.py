"""Single-tier CLI integration tests for dovo run."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.cli.ui.dispatcher import UiDispatcher, ui_dispatcher
from dovo.common.filesystem import Filesystem
from dovo.common.filesystem.models import RepositoryPaths, WorkspacePaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.config.models import ConfigTier
from dovo.core.db import DovoDb, RunStatus
from dovo.core.git.runner import GitRunner
from dovo.core.project.services.storage import resolve_workspace_paths
from dovo.core.worktree import Worktree
from dovo.engine import RunStateStore
from dovo.engine.writer import get_session_dir
from tests.harness.catalog import write_runnable_blueprint, write_runnable_step


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(None))


def _raise_keyboard_interrupt(*_args: object, **_kwargs: object) -> str:
    """Stand in for builtins.input, simulating a Ctrl+C during a blocking prompt read."""
    raise KeyboardInterrupt


def _force_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force UiDispatcher.is_interactive True so DispatcherFailurePrompter reads stdin.

    Console().is_terminal is always False under CliRunner.invoke (stdout is not a real
    TTY), so the prompter's interactivity gate must be forced open to exercise its
    blocking input() call from a CLI integration test.
    """
    monkeypatch.setattr(UiDispatcher, "is_interactive", property(lambda self: True))


class RunCliIntegrationTests:
    """Typer runner integration tests for dovo run."""

    def test_run_cli_no_worktree_completes_in_place_exits_zero(
        self, cli_runner: CliRunner, run_workspace: Path
    ) -> None:
        """dovo run --no-worktree: single-step blueprint completes in place, exit 0, 'Worktree: In-place (workspace)' in stdout."""
        write_runnable_blueprint(run_workspace, key="noop-task", steps=[{"id": "s1", "run": "true"}])

        result = cli_runner.invoke(app, ["-p", str(run_workspace), "run", "noop-task", "--no-worktree"])

        assert result.exit_code == 0
        assert "Blueprint Run Completed:" in result.stdout
        assert "Worktree: In-place (workspace)" in result.stdout

    def test_run_cli_worktree_enabled_completes_in_active_worktree_exits_zero(
        self, cli_runner: CliRunner, run_workspace: Path
    ) -> None:
        """dovo run (worktree default on): single-step blueprint completes, exit 0, 'Worktree: Active (' in stdout."""
        write_runnable_blueprint(run_workspace, key="worktree-task", steps=[{"id": "s1", "run": "true"}])

        result = cli_runner.invoke(app, ["-p", str(run_workspace), "run", "worktree-task"])

        assert result.exit_code == 0
        assert "Worktree: Active (" in result.stdout

    @pytest.mark.parametrize(
        "earlier_worktree",
        [
            pytest.param("kept_run", id="kept_run"),
            pytest.param("legacy_blueprint_key_worktree", id="legacy_blueprint_key_worktree"),
        ],
    )
    def test_run_cli_keep_with_existing_worktree_creates_separate_worktree_exits_zero(
        self, cli_runner: CliRunner, run_workspace: Path, earlier_worktree: str
    ) -> None:
        """[tier-3/integration] dovo run keep-task --keep: with an earlier worktree present (a prior --keep run, or one created under session id 'keep-task'), the run exits 0, its run row has worktree_id == session_id, .dovo/worktrees/<session_id> exists, branch dovo/<session_id> exists, and the earlier worktree directory is still present and distinct."""
        write_runnable_blueprint(run_workspace, key="keep-task", steps=[{"id": "s1", "run": "true"}])
        paths = _paths_for(run_workspace)
        db = DovoDb(database_file=paths.database_file, project_id=paths.project_id)
        if earlier_worktree == "kept_run":
            first = cli_runner.invoke(app, ["-p", str(run_workspace), "run", "keep-task", "--keep"])
            assert first.exit_code == 0
        else:
            Worktree(paths, db=db.worktrees).create(session_id="keep-task")
        earlier_ids = {row.id for row in db.worktrees.list()}

        result = cli_runner.invoke(app, ["-p", str(run_workspace), "run", "keep-task", "--keep"])

        assert result.exit_code == 0
        latest = db.runs.list(limit=1)[0]
        assert latest.worktree_id == latest.session_id
        assert latest.session_id not in earlier_ids
        assert paths.worktree_dir(latest.session_id).is_dir()
        assert f"dovo/{latest.session_id}" in GitRunner.list_branches(run_workspace)
        assert all(paths.worktree_dir(earlier_id).is_dir() for earlier_id in earlier_ids)

    def test_run_cli_no_worktree_agent_step_exits_one_with_worktree_diagnostic(
        self, cli_runner: CliRunner, run_workspace: Path
    ) -> None:
        """[tier-3/integration] dovo run --no-worktree: a blueprint with one type: agent step exits 1 and stdout contains 'Agent steps require an active git worktree.'."""
        write_runnable_blueprint(
            run_workspace, key="agent-task", steps=[{"id": "plan", "type": "agent", "prompt": "Plan the change"}]
        )

        result = cli_runner.invoke(app, ["-p", str(run_workspace), "run", "agent-task", "--no-worktree"])

        assert result.exit_code == 1
        assert "Agent steps require an active git worktree." in result.stdout

    def test_run_cli_prompt_user_retry_then_succeeds_exits_zero(
        self, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner, run_workspace: Path
    ) -> None:
        """dovo run: on_failure=prompt_user step fed input='r\\n' retries and completes, exit 0."""
        _force_interactive(monkeypatch)
        write_runnable_blueprint(
            run_workspace,
            key="retry-task",
            steps=[
                {
                    "id": "s1",
                    "run": "test -f marker.txt && exit 0 || (touch marker.txt && exit 1)",
                    "on_failure": "prompt_user",
                }
            ],
        )

        result = cli_runner.invoke(app, ["-p", str(run_workspace), "run", "retry-task", "--no-worktree"], input="r\n")

        assert result.exit_code == 0
        assert "Blueprint Run Completed:" in result.stdout

    def test_run_cli_prompt_user_abort_exits_one(
        self, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner, run_workspace: Path
    ) -> None:
        """dovo run: on_failure=prompt_user step fed input='a\\n' aborts the run, exit 1."""
        _force_interactive(monkeypatch)
        write_runnable_blueprint(
            run_workspace,
            key="abort-task",
            steps=[{"id": "s1", "run": "exit 1", "on_failure": "prompt_user"}],
        )

        result = cli_runner.invoke(app, ["-p", str(run_workspace), "run", "abort-task", "--no-worktree"], input="a\n")

        assert result.exit_code == 1
        assert "Run Failed" in result.stdout

    def test_run_cli_prompt_user_continue_exits_zero(
        self, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner, run_workspace: Path
    ) -> None:
        """dovo run: on_failure=prompt_user step fed input='c\\n' ignores the failure and completes, exit 0."""
        _force_interactive(monkeypatch)
        write_runnable_blueprint(
            run_workspace,
            key="continue-task",
            steps=[
                {"id": "s1", "run": "exit 1", "on_failure": "prompt_user"},
                {"id": "s2", "run": "true"},
            ],
        )

        result = cli_runner.invoke(
            app, ["-p", str(run_workspace), "run", "continue-task", "--no-worktree"], input="c\n"
        )

        assert result.exit_code == 0
        assert "Blueprint Run Completed:" in result.stdout

    def test_run_cli_prompt_user_keyboard_interrupt_persists_paused_state(
        self, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner, run_workspace: Path
    ) -> None:
        """dovo run: a KeyboardInterrupt raised from the interactive prompter after the paused state is saved sets the run record's status to PAUSED.

        `--no-tty` short-circuits to ABORT before the prompter is ever called, so PAUSED
        is only reachable via a KeyboardInterrupt from inside an interactive prompter
        call; the run still exits 1 since the failed-step diagnostic populates `errors`.
        """
        _force_interactive(monkeypatch)
        monkeypatch.setattr("builtins.input", _raise_keyboard_interrupt)
        write_runnable_blueprint(
            run_workspace,
            key="pause-task",
            steps=[{"id": "pause-step", "run": "exit 1", "on_failure": "prompt_user"}],
        )

        result = cli_runner.invoke(
            app,
            ["-p", str(run_workspace), "run", "pause-task", "--no-worktree", "--session-id", "paused-session-1"],
        )

        assert result.exit_code == 1
        run_paths = _paths_for(run_workspace)
        record = DovoDb(database_file=run_paths.database_file, project_id=run_paths.project_id).runs.get(
            "paused-session-1"
        )
        assert record is not None
        assert record.status == RunStatus.PAUSED

    def test_run_cli_json_format_emits_run_success_event(self, cli_runner: CliRunner, run_workspace: Path) -> None:
        """dovo run --format json: NDJSON stream includes a RunSuccessEvent with payload.status == 'completed'."""
        write_runnable_blueprint(run_workspace, key="json-task", steps=[{"id": "s1", "run": "true"}])

        result = cli_runner.invoke(
            app, ["-p", str(run_workspace), "run", "json-task", "--no-worktree", "--format", "json"]
        )

        assert result.exit_code == 0
        events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        success_events = [e for e in events if e["event_type"] == "RunSuccessEvent"]
        assert len(success_events) == 1
        assert success_events[0]["payload"]["status"] == "completed"

    def test_run_cli_uninitialized_git_repo_auto_initializes_and_proceeds(
        self, cli_runner: CliRunner, git_repo: Path
    ) -> None:
        """dovo run: git repo with no .dovo/ auto-initializes (project.json + config.json written) instead of raising ConfigLoadError."""
        result = cli_runner.invoke(app, ["-p", str(git_repo), "run", "missing-workflow", "--no-worktree"])

        assert "CONFIG_NOT_FOUND" not in result.stdout
        assert (git_repo / ".dovo" / "project.json").exists()
        assert (git_repo / ".dovo" / "config.json").exists()

    def test_run_cli_malformed_user_tier_exits_one_with_config_error_panel(
        self,
        cli_runner: CliRunner,
        run_workspace: Path,
        write_tier_config: Callable[[ConfigTier, dict[str, Any] | str], Path],
    ) -> None:
        """[tier-3/integration] dovo run: malformed User tier config.json → exit 1, tier-attributed message in stdout, no unhandled exception.

        The top-level callback resolves config before the run handler ever runs, so a
        tier failure here renders a "Config Error" panel, not "Run Failed" — the blueprint
        is never resolved and no run row is ever inserted.
        """
        ui_dispatcher.set_output_format("terminal")
        write_tier_config(ConfigTier.USER, "{not valid json")

        result = cli_runner.invoke(app, ["-p", str(run_workspace), "run", "unresolved-task", "--no-worktree"])

        assert result.exit_code == 1
        assert "Config Error" in result.stdout
        assert "Invalid configuration in user layer" in result.stdout

    def test_run_cli_config_show_and_run_observe_identical_merged_config(
        self,
        cli_runner: CliRunner,
        run_workspace: Path,
        write_tier_config: Callable[[ConfigTier, dict[str, Any] | str], Path],
    ) -> None:
        """[tier-3/integration] dovo config show --format json's merged history.save_attempt_logs equals the value dovo run applies: with the User tier disabling it, config show reports false and the run writes no attempt logs."""
        write_runnable_blueprint(run_workspace, key="identical-config-task", steps=[{"id": "s1", "run": "true"}])
        write_tier_config(ConfigTier.USER, {"history": {"save_attempt_logs": False}})
        Filesystem.atomic_write_json(
            run_workspace / ".dovo" / "config.json",
            {"version": 1, "project": {"name": "identical-config"}, "worktree": {"base_ref": "main"}},
        )

        show_result = cli_runner.invoke(app, ["-p", str(run_workspace), "config", "show", "--format", "json"])
        assert show_result.exit_code == 0
        shown_config = json.loads(show_result.stdout)["payload"]["config"]

        run_result = cli_runner.invoke(
            app,
            ["-p", str(run_workspace), "run", "identical-config-task", "--no-worktree", "--session-id", "cfg-1"],
        )

        assert run_result.exit_code == 0
        assert shown_config["history"]["save_attempt_logs"] is False
        assert not list((_paths_for(run_workspace).logs_dir / "cfg-1").glob("*attempt*"))

    def test_run_cli_writes_definitions_snapshot_for_uses_step(
        self, cli_runner: CliRunner, run_workspace: Path
    ) -> None:
        """dovo run --no-worktree --session-id snap-1: a blueprint with one uses: step writes .../sessions/snap-1/definitions/{key}.yml and .../definitions/steps/{step_key}.yml, and RunStateStore.load().state.manifest.steps has one entry."""
        write_runnable_step(run_workspace, key="lint-check", definition={"id": "lint-check", "run": "true"})
        write_runnable_blueprint(run_workspace, key="snapshot-task", steps=[{"id": "s1", "uses": "lint-check"}])

        result = cli_runner.invoke(
            app, ["-p", str(run_workspace), "run", "snapshot-task", "--no-worktree", "--session-id", "snap-1"]
        )

        assert result.exit_code == 0
        run_paths = _paths_for(run_workspace)
        session_dir = get_session_dir(run_paths, "snap-1")
        assert (session_dir / "definitions" / "snapshot-task.yml").is_file()
        assert (session_dir / "definitions" / "steps" / "lint-check.yml").is_file()
        db = DovoDb(database_file=run_paths.database_file, project_id=run_paths.project_id)
        loaded = RunStateStore(db.runs, run_paths, "snap-1").load()
        assert loaded.state is not None
        assert len(loaded.state.manifest.steps) == 1
