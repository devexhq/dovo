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
from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import Filesystem
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.common.lock import LockTimeoutError, WorkspaceLock
from dovo.common.models import OutputFormatOptions
from dovo.core.bootstrap import WorkspaceInitResult
from dovo.core.catalog.blueprint import Blueprint
from dovo.core.catalog.exceptions import CatalogError
from dovo.core.config.models import ConfigTier
from dovo.core.db import DovoDb, SessionStatus
from dovo.core.git.runner import GitRunner
from dovo.core.worktree import Worktree
from dovo.engine import SessionStateStore
from dovo.engine.models import LifecycleUpdate
from dovo.engine.state_models import ExecutionStateTree
from tests.harness import FakeAgentRunner, UnfixableAgentRunner
from tests.harness.builders import init_git_repository
from tests.harness.catalog import write_runnable_blueprint
from tests.harness.diagnostic_log import details_line, unwrapped
from tests.harness.fakes import fixed_clock, fixed_new_id
from tests.harness.lock import raise_lock_timeout
from tests.harness.sessions import fail_reconciliation_list
from tests.harness.workspace_paths import initialized_workspace_paths

GUARD_MESSAGE_HEAD = "Workspace is not initialized: no valid project identity at"


def _raise_keyboard_interrupt(*_args: object, **_kwargs: object) -> str:
    """Stand in for builtins.input, simulating a Ctrl+C during a blocking prompt read."""
    raise KeyboardInterrupt


def _raise_type_error(*_args: object, **_kwargs: object) -> None:
    """Stand in for Blueprint.load, failing with an unexpected TypeError("bad")."""
    raise TypeError("bad")


def _force_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force UiDispatcher.is_interactive True so DispatcherFailurePrompter reads stdin.

    Console().is_terminal is always False under CliRunner.invoke (stdout is not a real
    TTY), so the prompter's interactivity gate must be forced open to exercise its
    blocking input() call from a CLI integration test.
    """
    monkeypatch.setattr(UiDispatcher, "is_interactive", property(lambda self: True))


class RunCliTests:
    """Typer runner tests for dovo run's catalog failures."""

    def test_run_missing_blueprint_json_reports_catalog_not_found(self, cli_runner: CliRunner, git_repo: Path) -> None:
        """Exit 1 with one Run Failed event whose message is the catalog's not-found error for `run ghost --format json`."""
        result = cli_runner.invoke(app, ["-p", str(git_repo), "run", "ghost", "--format", "json"])

        assert result.exit_code == 1
        events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        failures = [event["payload"] for event in events if event["event_type"] == "ErrorPanelEvent"]
        assert failures == [
            {"title": "Run Failed", "message": "Catalog item 'ghost' not found.", "border_style": "red"}
        ]


class RunCliIntegrationTests:
    """Typer runner integration tests for dovo run."""

    def test_run_cli_no_worktree_completes_in_place_exits_zero(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        write_runnable_blueprint(cli_workspace, key="noop-task", steps=[{"id": "s1", "run": "true"}])

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "run", "noop-task", "--no-worktree"])

        assert result.exit_code == 0
        assert "Blueprint Run Completed:" in result.stdout
        assert "Worktree: In-place (workspace)" in result.stdout

    def test_run_cli_worktree_enabled_completes_in_active_worktree_exits_zero(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        write_runnable_blueprint(cli_workspace, key="worktree-task", steps=[{"id": "s1", "run": "true"}])

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "run", "worktree-task"])

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
        self, cli_runner: CliRunner, cli_workspace: Path, earlier_worktree: str
    ) -> None:
        write_runnable_blueprint(cli_workspace, key="keep-task", steps=[{"id": "s1", "run": "true"}])
        paths = initialized_workspace_paths(cli_workspace)
        db = DovoDb(database_file=paths.database_file, project_id=paths.project_id)
        if earlier_worktree == "kept_run":
            first = cli_runner.invoke(app, ["-p", str(cli_workspace), "run", "keep-task", "--keep"])
            assert first.exit_code == 0
        else:
            Worktree(paths, db=db.worktrees, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id).create(
                session_id="keep-task"
            )
        earlier_ids = {row.id for row in db.worktrees.list()}

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "run", "keep-task", "--keep"])

        assert result.exit_code == 0
        latest = db.sessions.list(limit=1)[0]
        assert latest.worktree_id == latest.session_id
        assert latest.session_id not in earlier_ids
        assert paths.worktree_dir(latest.session_id).is_dir()
        assert f"dovo/{latest.session_id}" in GitRunner().list_branches(cli_workspace)
        assert all(paths.worktree_dir(earlier_id).is_dir() for earlier_id in earlier_ids)

    def test_run_cli_no_worktree_agent_step_exits_one_with_worktree_diagnostic(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        write_runnable_blueprint(
            cli_workspace, key="agent-task", steps=[{"id": "plan", "type": "agent", "prompt": "Plan the change"}]
        )

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "run", "agent-task", "--no-worktree"])

        assert result.exit_code == 1
        assert "Agent steps require an active git worktree." in result.stdout

    @pytest.mark.parametrize(
        ("first_step_run", "answer", "exit_code", "expected_text"),
        [
            pytest.param(
                "test -f marker.txt && exit 0 || (touch marker.txt && exit 1)",
                "r\n",
                0,
                "Blueprint Run Completed:",
                id="retry_then_succeeds",
            ),
            pytest.param("exit 1", "a\n", 1, "Run Failed", id="abort"),
            pytest.param("exit 1", "c\n", 0, "Blueprint Run Completed:", id="continue"),
        ],
    )
    def test_run_cli_prompt_user_decision_sets_exit_code(
        self,
        monkeypatch: pytest.MonkeyPatch,
        cli_runner: CliRunner,
        cli_workspace: Path,
        first_step_run: str,
        answer: str,
        exit_code: int,
        expected_text: str,
    ) -> None:
        _force_interactive(monkeypatch)
        write_runnable_blueprint(
            cli_workspace,
            key="prompt-task",
            steps=[
                {"id": "s1", "run": first_step_run, "on_failure": "prompt_user"},
                {"id": "s2", "run": "true"},
            ],
        )

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "run", "prompt-task", "--no-worktree"], input=answer)

        assert result.exit_code == exit_code
        assert expected_text in result.stdout

    def test_run_cli_prompt_user_keyboard_interrupt_persists_paused_state(
        self, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        """Persist PAUSED when the interactive prompter raises KeyboardInterrupt after the paused state is saved.

        `--no-tty` aborts before any prompter call, so PAUSED is reachable only this way; the run still exits 1.
        """
        _force_interactive(monkeypatch)
        monkeypatch.setattr("builtins.input", _raise_keyboard_interrupt)
        write_runnable_blueprint(
            cli_workspace,
            key="pause-task",
            steps=[{"id": "pause-step", "run": "exit 1", "on_failure": "prompt_user"}],
        )

        result = cli_runner.invoke(
            app,
            ["-p", str(cli_workspace), "run", "pause-task", "--no-worktree", "--session-id", "paused-session-1"],
        )

        assert result.exit_code == 1
        run_paths = initialized_workspace_paths(cli_workspace)
        record = DovoDb(database_file=run_paths.database_file, project_id=run_paths.project_id).sessions.get(
            "paused-session-1"
        )
        assert record is not None
        assert record.status == SessionStatus.PAUSED

    def test_run_cli_json_format_emits_run_success_event(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        write_runnable_blueprint(cli_workspace, key="json-task", steps=[{"id": "s1", "run": "true"}])

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "run", "json-task", "--no-worktree", "--format", "json"]
        )

        assert result.exit_code == 0
        events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        success_events = [e for e in events if e["event_type"] == "RunSuccessEvent"]
        assert len(success_events) == 1
        assert success_events[0]["payload"]["status"] == "completed"

    def test_run_cli_json_finalization_failure_carries_plain_warning(
        self,
        cli_runner: CliRunner,
        cli_workspace: Path,
        frozen_diagnostic_clock: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Exit 0 and emit a WarningEvent carrying the finalization failure alone, with no 'Details logged' text, under --format json."""
        write_runnable_blueprint(cli_workspace, key="json-task", steps=[{"id": "s1", "run": "true"}])
        original_save = SessionStateStore.save

        def _finalize_disk_full(
            self: SessionStateStore,
            state: ExecutionStateTree,
            warnings: list[str],
            lifecycle: LifecycleUpdate | None = None,
        ) -> ExecutionStateTree:
            if lifecycle is not None and lifecycle.worktree_kept is not None:
                raise OSError("disk full")
            return original_save(self, state, warnings, lifecycle)

        monkeypatch.setattr(SessionStateStore, "save", _finalize_disk_full)

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "run", "json-task", "--no-worktree", "--format", "json"]
        )

        assert result.exit_code == 0
        events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        warnings = [e["payload"]["message"] for e in events if e["event_type"] == "WarningEvent"]
        assert warnings == ["Failed to update run status in database: disk full"]
        assert "Details logged" not in result.stdout

    def test_run_cli_renders_diagnostics_after_outcome(
        self,
        cli_runner: CliRunner,
        cli_workspace: Path,
        frozen_diagnostic_clock: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With Blueprint.load raising, exit 1 and print 'Details logged: …' after every warning and after the 'Run Failed' panel, whose message has no line."""
        write_runnable_blueprint(cli_workspace, key="load-task", steps=[{"id": "s1", "run": "true"}])
        monkeypatch.setattr(Blueprint, "load", classmethod(_raise_type_error))

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "run", "load-task", "--no-worktree"])

        assert result.exit_code == 1
        output = unwrapped(result.stdout)
        line = unwrapped(details_line(frozen_diagnostic_clock))
        assert output.count(unwrapped("Details logged")) == 1
        assert output.index(unwrapped("Unexpected error during engine.run: bad")) < output.index(line)
        assert output.index("RunFailed") < output.index(line)
        assert output.endswith(line)

    def test_run_cli_unexpected_engine_error_exits_one_with_details_line(
        self,
        cli_runner: CliRunner,
        cli_workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        frozen_diagnostic_clock: Path,
    ) -> None:
        """With drive_run raising TypeError("boom"), exit 1, print the engine.run message and a 'Details logged:' line naming the frozen event."""
        write_runnable_blueprint(cli_workspace, key="boom-task", steps=[{"id": "s1", "run": "true"}])

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise TypeError("boom")

        monkeypatch.setattr("dovo.engine.engine.drive_run", _boom)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "run", "boom-task", "--no-worktree"])

        assert result.exit_code == 1
        output = unwrapped(result.stdout)
        line = unwrapped(details_line(frozen_diagnostic_clock))
        assert unwrapped("Unexpected error during engine.run: boom") in output
        assert output.endswith(line)

    def test_run_cli_reconciliation_failure_prints_details_line_not_warning(
        self,
        cli_runner: CliRunner,
        cli_workspace: Path,
        frozen_diagnostic_clock: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Print one 'Details logged: …' line after the run's warnings and outcome and no 'Warning: Details logged' line."""
        write_runnable_blueprint(cli_workspace, key="noop-task", steps=[{"id": "s1", "run": "true"}])
        fail_reconciliation_list(monkeypatch)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "run", "noop-task", "--no-worktree"])

        assert result.exit_code == 0
        output = unwrapped(result.stdout)
        line = unwrapped(details_line(frozen_diagnostic_clock))
        assert output.count(unwrapped("Details logged")) == 1
        assert unwrapped("Warning: Details logged") not in output
        assert output.index(unwrapped("Blueprint Run Completed:")) < output.index(line)
        assert output.endswith(line)

    def test_run_cli_step_output_failure_prints_details_line_not_warning(
        self,
        cli_runner: CliRunner,
        cli_workspace: Path,
        frozen_diagnostic_clock: Path,
    ) -> None:
        """With a command step whose stdout cannot be decoded, exit 1, print exactly one 'Details logged: …' line, ending the output, and no 'Warning: Details logged' line."""
        write_runnable_blueprint(cli_workspace, key="pipe-task", steps=[{"id": "s1", "run": r"printf '\377'"}])

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "run", "pipe-task", "--no-worktree"])

        assert result.exit_code == 1
        output = unwrapped(result.stdout)
        line = unwrapped(details_line(frozen_diagnostic_clock))
        assert unwrapped("Command pipe error: Failed reading stdout stream:") in output
        assert output.count(unwrapped("Details logged")) == 1
        assert unwrapped("Warning: Details logged") not in output
        assert output.endswith(line)

    def test_run_cli_json_emits_no_diagnostics_event(
        self,
        cli_runner: CliRunner,
        cli_workspace: Path,
        frozen_diagnostic_clock: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With Blueprint.load raising and --format json, exit 1 and emit no DiagnosticsEvent envelope and no 'Details logged' text in any event."""
        write_runnable_blueprint(cli_workspace, key="load-task", steps=[{"id": "s1", "run": "true"}])
        monkeypatch.setattr(Blueprint, "load", classmethod(_raise_type_error))

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "run", "load-task", "--no-worktree", "--format", "json"]
        )

        assert result.exit_code == 1
        events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        assert [event["payload"] for event in events if event["event_type"] == "ErrorPanelEvent"] == [
            {
                "title": "Run Failed",
                "message": "Unexpected error during engine.run: bad",
                "border_style": "red",
            }
        ]
        assert "DiagnosticsEvent" not in {event["event_type"] for event in events}
        assert "Details logged" not in result.stdout
        assert frozen_diagnostic_clock.is_file()

    def test_run_cli_uninitialized_git_repo_auto_initializes_silently_and_proceeds(
        self, cli_runner: CliRunner, git_repo: Path, dispatch_spy: list[object]
    ) -> None:
        """Initialize .dovo/ without printing the init summary or the guard panel, then resolve the blueprint."""
        result = cli_runner.invoke(app, ["-p", str(git_repo), "run", "missing-blueprint", "--no-worktree"])

        assert (git_repo / ".dovo" / "project.json").exists()
        assert (git_repo / ".dovo" / "config.json").exists()
        assert not any(isinstance(event, WorkspaceInitResult) for event in dispatch_spy)
        assert "Initialized Dovo at" not in result.stdout
        assert GUARD_MESSAGE_HEAD not in result.stdout
        assert "missing-blueprint" in result.stdout
        assert "not found" in result.stdout

    def test_run_cli_malformed_user_tier_exits_one_with_invalid_configuration_panel(
        self,
        cli_runner: CliRunner,
        cli_workspace: Path,
        write_tier_config: Callable[[ConfigTier, dict[str, Any] | str], Path],
    ) -> None:
        """Exit 1 with a tier-attributed Invalid Configuration panel when the User tier config.json is malformed.

        The top-level callback resolves config before the run handler runs,
        so no blueprint is resolved and no session row is inserted.
        """
        ui_dispatcher.set_output_format(OutputFormatOptions.TERMINAL)
        write_tier_config(ConfigTier.USER, "{not valid json")

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "run", "unresolved-task", "--no-worktree"])

        assert result.exit_code == 1
        assert "Invalid Configuration" in result.stdout
        assert "Config Error" not in result.stdout
        assert "Invalid configuration in user tier" in result.stdout

    def test_run_cli_config_show_and_run_observe_identical_merged_config(
        self,
        cli_runner: CliRunner,
        cli_workspace: Path,
        write_tier_config: Callable[[ConfigTier, dict[str, Any] | str], Path],
    ) -> None:
        write_runnable_blueprint(cli_workspace, key="identical-config-task", steps=[{"id": "s1", "run": "true"}])
        write_tier_config(ConfigTier.USER, {"history": {"save_attempt_logs": False}})
        Filesystem.atomic_write_json(
            cli_workspace / ".dovo" / "config.json",
            {"version": 1, "project": {"name": "identical-config"}, "worktree": {"base_ref": "main"}},
        )

        show_result = cli_runner.invoke(app, ["-p", str(cli_workspace), "config", "show", "--format", "json"])
        assert show_result.exit_code == 0
        shown_config = json.loads(show_result.stdout)["payload"]["config"]

        run_result = cli_runner.invoke(
            app,
            ["-p", str(cli_workspace), "run", "identical-config-task", "--no-worktree", "--session-id", "cfg-1"],
        )

        assert run_result.exit_code == 0
        assert shown_config["history"]["save_attempt_logs"] is False
        assert not list((initialized_workspace_paths(cli_workspace).logs_dir / "cfg-1").glob("*attempt*"))


def _tree_bytes(root: Path) -> dict[str, bytes | None]:
    """Map every path under root (relative, as a string) to its bytes, or None for a directory."""
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): None if path.is_dir() else path.read_bytes() for path in sorted(root.rglob("*"))
    }


def _failed_auto_init_target(case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Build the target directory for a failing auto-init case; return it and the effective DOVO_HOME."""
    if case == "non-git":
        target = tmp_path / "scratch"
        target.mkdir()
    elif case == "global-home":
        target = tmp_path / "home"
        (target / ".dovo").mkdir(parents=True)
        init_git_repository(target)
        monkeypatch.setenv("DOVO_HOME", str(target / ".dovo"))
    else:
        target = tmp_path / "repo"
        target.mkdir()
        init_git_repository(target)
        (target / ".dovo").write_text("not a directory\n", encoding="utf-8")
    return target, resolve_global_paths().root


class RunCliAutoInitTests:
    """Typer runner tests for dovo run's automatic workspace initialization."""

    @pytest.mark.parametrize(
        ("case", "error_code", "error_fragment"),
        [
            pytest.param("non-git", ErrorCode.BOOTSTRAP_REPO_NOT_FOUND, "not a valid Git repository", id="non-git"),
            pytest.param(
                "global-home",
                ErrorCode.BOOTSTRAP_TARGET_FORBIDDEN,
                "its .dovo directory is the global Dovo home",
                id="global-home",
            ),
            pytest.param(
                "bootstrap",
                ErrorCode.BOOTSTRAP_PATH_INVALID,
                "exists but is a file, not a directory.",
                id="bootstrap",
            ),
        ],
    )
    def test_run_cli_failed_auto_init_dispatches_init_result_and_exits_one(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        dispatch_spy: list[object],
        case: str,
        error_code: ErrorCode,
        error_fragment: str,
    ) -> None:
        """Exit 1 dispatching only the failed WorkspaceInitResult, with the target and DOVO_HOME trees unchanged."""
        target, dovo_home = _failed_auto_init_target(case, tmp_path, monkeypatch)
        target_before = _tree_bytes(target)
        home_before = _tree_bytes(dovo_home)

        result = cli_runner.invoke(app, ["-p", str(target), "run", "tests"])

        assert result.exit_code == 1
        assert len(dispatch_spy) == 1
        init_result = dispatch_spy[0]
        assert isinstance(init_result, WorkspaceInitResult)
        assert init_result.ok is False
        assert init_result.error_code == error_code
        assert any(error_fragment in error for error in init_result.errors)
        assert "Initialization Failed!" in result.stdout
        assert GUARD_MESSAGE_HEAD not in result.stdout
        assert _tree_bytes(target) == target_before
        assert _tree_bytes(dovo_home) == home_before

    def test_run_cli_seed_failure_during_auto_init_exits_one_and_rolls_back(
        self, cli_runner: CliRunner, git_repo: Path, monkeypatch: pytest.MonkeyPatch, dispatch_spy: list[object]
    ) -> None:
        """Exit 1 with the init failure panel and no run output, and remove the new .dovo/, when seeding fails."""

        def _seed_failed(*_args: object, **_kwargs: object) -> None:
            raise CatalogError(ErrorCode.CATALOG_SEED_FAILED, "boom")

        monkeypatch.setattr("dovo.core.bootstrap.services.initialize.seed_all_catalog_templates", _seed_failed)

        result = cli_runner.invoke(app, ["-p", str(git_repo), "run", "tests"])

        assert result.exit_code == 1
        assert "Initialization Failed!" in result.stdout
        assert len(dispatch_spy) == 1
        init_result = dispatch_spy[0]
        assert isinstance(init_result, WorkspaceInitResult)
        assert init_result.error_code == ErrorCode.CATALOG_SEED_FAILED
        assert init_result.errors == ["boom"]
        assert not (git_repo / ".dovo").exists()

    def test_run_cli_identity_lock_timeout_during_auto_init_raises_and_rolls_back(
        self, cli_runner: CliRunner, git_repo: Path, monkeypatch: pytest.MonkeyPatch, dispatch_spy: list[object]
    ) -> None:
        """Exit 1 with result.exception the LockTimeoutError, nothing dispatched, and the new .dovo/ removed."""
        monkeypatch.setattr(WorkspaceLock, "__enter__", raise_lock_timeout)

        result = cli_runner.invoke(app, ["-p", str(git_repo), "run", "tests"])

        assert result.exit_code == 1
        assert isinstance(result.exception, LockTimeoutError)
        assert str(result.exception) == "lock held"
        assert dispatch_spy == []
        assert not (git_repo / ".dovo").exists()

    def test_run_cli_initialized_workspace_does_not_attempt_initialization(
        self, cli_runner: CliRunner, cli_workspace: Path, monkeypatch: pytest.MonkeyPatch, dispatch_spy: list[object]
    ) -> None:
        """Exit 0 in an initialized workspace without attempting initialization or touching config.json."""
        write_runnable_blueprint(cli_workspace, key="noop-task", steps=[{"id": "s1", "run": "true"}])
        config_file = cli_workspace / ".dovo" / "config.json"
        config_before = (config_file.read_bytes(), config_file.stat().st_mtime_ns)

        def _not_attempted(*_args: object, **_kwargs: object) -> WorkspaceInitResult:
            raise AssertionError("initialization was attempted")

        monkeypatch.setattr("dovo.cli.context.auto_initialize_workspace", _not_attempted)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "run", "noop-task", "--no-worktree"])

        assert result.exit_code == 0
        assert not any(isinstance(event, WorkspaceInitResult) for event in dispatch_spy)
        assert (config_file.read_bytes(), config_file.stat().st_mtime_ns) == config_before


_AGENT_STDOUT = b'{"type":"assistant.message","data":{"content":"ok"}}\n{"type":"result","data":{"exitCode":0}}\n'
_UNRELATED = "DOVO_TEST_UNRELATED_SECRET"


def _fake_copilot(monkeypatch: pytest.MonkeyPatch) -> FakeAgentRunner:
    """Install a FakeAgentRunner at the Copilot process boundary with a usable GH_TOKEN."""
    monkeypatch.setenv("GH_TOKEN", "test-token")
    runner = FakeAgentRunner().returning(stdout=_AGENT_STDOUT)
    monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)

    return runner


def _fake_claude(monkeypatch: pytest.MonkeyPatch) -> FakeAgentRunner:
    """Install a FakeAgentRunner at the Claude process boundary with a usable ANTHROPIC_API_KEY."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    runner = FakeAgentRunner().returning(
        stdout=b'{"type":"result","subtype":"success","is_error":false,"result":"done"}\n'
    )
    monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)

    return runner


def _write_agent_blueprint(workspace: Path, **step_extra: object) -> None:
    write_runnable_blueprint(
        workspace,
        key="agent-task",
        steps=[{"id": "plan", "type": "agent", "prompt": "Plan the change", **step_extra}],
    )


def _set_agent_config(workspace: Path, **settings: object) -> None:
    config_path = workspace / ".dovo" / "config.json"
    data = json.loads(config_path.read_text(encoding="utf-8"))
    data.setdefault("agents", {}).setdefault("copilot", {}).update(settings)
    config_path.write_text(json.dumps(data), encoding="utf-8")


class RunCliRetentionIntegrationTests:
    """Typer runner integration tests for worktree retention flags on dovo run."""

    def test_run_cli_keep_and_discard_together_exits_two(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        write_runnable_blueprint(cli_workspace, key="keep-task", steps=[{"id": "s1", "run": "true"}])
        paths = initialized_workspace_paths(cli_workspace)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "run", "keep-task", "--keep", "--discard"])

        assert result.exit_code == 2
        assert "mutually exclusive" in result.output
        db = DovoDb(database_file=paths.database_file, project_id=paths.project_id)
        assert db.sessions.list() == []

    @pytest.mark.parametrize(
        ("flags", "worktree_kept"),
        [
            pytest.param([], True, id="without_flags_keeps_worktree"),
            pytest.param(["--discard"], False, id="with_discard_removes_worktree"),
        ],
    )
    def test_run_cli_failed_run_retention_follows_flags_exits_one(
        self, cli_runner: CliRunner, cli_workspace: Path, flags: list[str], worktree_kept: bool
    ) -> None:
        write_runnable_blueprint(cli_workspace, key="failing-task", steps=[{"id": "s1", "run": "exit 1"}])
        paths = initialized_workspace_paths(cli_workspace)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "run", "failing-task", *flags])

        assert result.exit_code == 1
        row = DovoDb(database_file=paths.database_file, project_id=paths.project_id).sessions.list(limit=1)[0]
        assert paths.worktree_dir(row.session_id).is_dir() is worktree_kept
        assert row.worktree_kept is worktree_kept

    def test_run_cli_failed_run_prints_kept_summary_and_fix(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        write_runnable_blueprint(cli_workspace, key="failing-task", steps=[{"id": "s1", "run": "exit 1"}])
        paths = initialized_workspace_paths(cli_workspace)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "run", "failing-task"])

        session_id = (
            DovoDb(database_file=paths.database_file, project_id=paths.project_id).sessions.list(limit=1)[0].session_id
        )
        output = " ".join(result.stdout.split())
        assert result.exit_code == 1
        assert f".dovo/worktrees/{session_id} (branch dovo/{session_id})" in output
        assert "Worktree: Kept at " in output
        assert f"Remove it with 'dovo worktree delete {session_id}'" in output


class RunCliUnfixableAgentIntegrationTests:
    """Typer runner integration tests for an agent step whose agent reports the task unfixable."""

    def test_run_cli_agent_reporting_unfixable_fails_run_and_keeps_worktree(
        self, cli_runner: CliRunner, cli_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GH_TOKEN", "test-token")
        runner = UnfixableAgentRunner("no SSH key\n").returning(stdout=_AGENT_STDOUT)
        monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)
        _write_agent_blueprint(cli_workspace)
        paths = initialized_workspace_paths(cli_workspace)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "run", "agent-task", "--format", "json"])

        events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        step_done = next(event["payload"] for event in events if event["event_type"] == "StepDoneEvent")
        output_lines = [event["payload"]["line"] for event in events if event["event_type"] == "StepOutputEvent"]
        summaries = [json.loads(line) for line in output_lines if line.startswith("{")]
        row = DovoDb(database_file=paths.database_file, project_id=paths.project_id).sessions.list(limit=1)[0]
        assert result.exit_code == 1
        assert step_done["exit_code"] == 201
        assert step_done["error_message"] == "Agent reported the task unfixable: no SSH key"
        assert summaries == [
            {"status": "unfixable", "summary": "ok", "unfixable_reason": "no SSH key", "touched_files": []}
        ]
        assert row.status == SessionStatus.FAILED
        assert paths.worktree_dir(row.session_id).is_dir()


class RunEnvFlagsCliIntegrationTests:
    """Typer runner integration tests for the dovo run --env-mode and --env-passthrough flags."""

    def test_run_cli_env_flags_reach_the_agent_subprocess_env_and_leave_config_untouched(
        self, cli_runner: CliRunner, cli_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = _fake_copilot(monkeypatch)
        monkeypatch.setenv(_UNRELATED, "host-value")
        monkeypatch.setenv("DOCKER_CONFIG", "/docker")
        _write_agent_blueprint(cli_workspace)
        config_path = cli_workspace / ".dovo" / "config.json"
        before = config_path.read_bytes()

        result = cli_runner.invoke(
            app,
            [
                "-p",
                str(cli_workspace),
                "run",
                "agent-task",
                "--env-mode",
                "inherit",
                "--env-passthrough",
                "DOCKER_CONFIG",
            ],
        )

        paths = initialized_workspace_paths(cli_workspace)
        row = DovoDb(database_file=paths.database_file, project_id=paths.project_id).sessions.list(limit=1)[0]
        assert result.exit_code == 0
        assert runner.last_call.env[_UNRELATED] == "host-value"
        assert runner.last_call.env["DOCKER_CONFIG"] == "/docker"
        assert config_path.read_bytes() == before
        assert "inherit" not in row.model_dump_json()
        assert "DOCKER_CONFIG" not in row.model_dump_json()

    def test_run_cli_repeated_passthrough_flags_append_to_config_entries(
        self, cli_runner: CliRunner, cli_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = _fake_copilot(monkeypatch)
        for name in ("A_HOST", "B_HOST", "C_ONE", "C_TWO", "D_HOST"):
            monkeypatch.setenv(name, "host-value")
        _write_agent_blueprint(cli_workspace)
        _set_agent_config(cli_workspace, env_passthrough=["A_HOST"])

        result = cli_runner.invoke(
            app,
            [
                "-p", str(cli_workspace), "run", "agent-task",
                "--env-passthrough", "B_HOST", "--env-passthrough", "C_*",
            ],
        )  # fmt: skip

        forwarded = {name for name in ("A_HOST", "B_HOST", "C_ONE", "C_TWO", "D_HOST") if name in runner.last_call.env}
        assert result.exit_code == 0
        assert forwarded == {"A_HOST", "B_HOST", "C_ONE", "C_TWO"}

    @pytest.mark.parametrize(
        ("flags", "exit_code", "expected_text"),
        [
            pytest.param(
                ["--env-passthrough", "A*B"], 1, "Invalid --env-passthrough value 'A*B':", id="invalid-passthrough"
            ),
            pytest.param(["--env-mode", "bogus"], 2, "'bogus' is not one of", id="invalid-mode"),
        ],
    )
    def test_run_cli_invalid_flag_value_exits_non_zero_before_any_step_starts(
        self,
        flags: list[str],
        exit_code: int,
        expected_text: str,
        cli_runner: CliRunner,
        cli_workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        runner = _fake_copilot(monkeypatch)
        _write_agent_blueprint(cli_workspace)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "run", "agent-task", *flags])

        paths = initialized_workspace_paths(cli_workspace)
        assert result.exit_code == exit_code
        assert expected_text in result.output
        assert DovoDb(database_file=paths.database_file, project_id=paths.project_id).sessions.list() == []
        assert runner.calls == []

    def test_run_cli_withheld_names_emit_the_operator_line_and_session_log_event(
        self, cli_runner: CliRunner, cli_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = _fake_copilot(monkeypatch)
        monkeypatch.setenv(_UNRELATED, "host-value")
        _write_agent_blueprint(cli_workspace)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "run", "agent-task"])

        paths = initialized_workspace_paths(cli_workspace)
        row = DovoDb(database_file=paths.database_file, project_id=paths.project_id).sessions.list(limit=1)[0]
        log_lines = (paths.logs_dir / row.session_id / "session.log").read_text(encoding="utf-8").splitlines()
        events = [json.loads(line) for line in log_lines if '"agent_env_filtered"' in line]
        withheld = runner.last_call.env["DOVO_ENV_WITHHELD"].split(",")
        assert result.exit_code == 0
        assert _UNRELATED in withheld
        assert len(events) == 1
        assert events[0]["env_withheld"].split(",") == withheld
        assert f"Agent environment filtered: {len(withheld)} variables withheld." in result.output


class RunAgentFlagCliIntegrationTests:
    """Typer runner integration tests for the dovo run --agent flag."""

    def test_run_agent_claude_reaches_the_claude_adapter(
        self, cli_runner: CliRunner, cli_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exit 0 and record one runner call whose cmd[0] is 'claude'."""
        runner = _fake_claude(monkeypatch)
        _write_agent_blueprint(cli_workspace)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "run", "agent-task", "--agent", "claude"])

        assert result.exit_code == 0
        assert [call.cmd[0] for call in runner.calls] == ["claude"]


def _ask_blueprint(workspace: Path) -> None:
    """Write a runnable blueprint `ask-feature`: a plan producer, an approve ask over it, and a closing marker step."""
    write_runnable_blueprint(
        workspace,
        key="ask-feature",
        steps=[
            {
                "id": "plan",
                "type": "command",
                "command": "echo plan > PLAN.md",
                "artifacts": [{"name": "plan", "path": "PLAN.md"}],
            },
            {
                "id": "approve",
                "type": "ask",
                "interaction": {
                    "mode": "select",
                    "message": "Approve this plan?",
                    "artifact": "plan",
                    "comment": {"policy": "optional", "message": "What should change?"},
                    "options": [
                        {"value": "approve", "label": "Approve", "comment": "disabled"},
                        {"value": "revise", "label": "Request changes", "comment": "required"},
                        {"value": "deny", "label": "Deny", "fail": True},
                    ],
                },
            },
            {"id": "done", "type": "command", "command": "touch done.marker"},
        ],
    )


class RunAskCliIntegrationTests:
    """Typer runner integration tests for dovo run over blueprints with ask steps."""

    def test_no_tty_run_pauses_at_ask_exit_zero_with_answer_hint(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        _ask_blueprint(cli_workspace)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "run", "ask-feature", "--no-tty", "--no-worktree"])

        paths = initialized_workspace_paths(cli_workspace)
        row = DovoDb(database_file=paths.database_file, project_id=paths.project_id).sessions.list(limit=1)[0]
        assert result.exit_code == 0
        assert row.status == SessionStatus.PAUSED
        assert (
            f"Run paused at prompt 'approve'. Answer with 'dovo resume {row.session_id} --answer approve=<value>'."
            in result.stdout
        )
        assert not (cli_workspace / "done.marker").exists()

    def test_json_format_emits_the_prompt_event_envelope(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        _ask_blueprint(cli_workspace)

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "run", "ask-feature", "--format", "json", "--no-worktree"]
        )

        envelopes = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        prompt = next(envelope for envelope in envelopes if envelope["event_type"] == "PromptEvent")
        assert result.exit_code == 0
        assert prompt["payload"]["step_id"] == "approve"
        assert [option["decision"] for option in prompt["payload"]["options"]] == ["approve", "revise", "deny"]
