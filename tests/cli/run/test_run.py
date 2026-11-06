"""Single-tier CLI integration tests for dovo run."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.cli.run.observer import DispatcherRunObserver
from dovo.cli.run.prompter import DispatcherFailurePrompter, DispatcherInteractionPrompter
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
from dovo.engine.engine import Engine
from dovo.engine.models import LifecycleUpdate
from dovo.engine.state_models import ExecutionStateTree
from tests.harness import FakeAgentRunner, UnfixableAgentRunner
from tests.harness.builders import init_git_repository
from tests.harness.catalog import write_runnable_blueprint
from tests.harness.diagnostic_log import details_line, unwrapped
from tests.harness.fakes import fixed_clock, fixed_new_id, use_virtual_process_time
from tests.harness.lock import lock_timeout_reported, raise_lock_timeout
from tests.harness.run_result import RUN_RESULT, RunResultPayload, json_events, run_result_payload
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
        """Exit 1 and end with the run result carrying the catalog's not-found error and no session for `run ghost --format json`."""
        result = cli_runner.invoke(app, ["-p", str(git_repo), "run", "ghost", "--format", "json"])

        assert result.exit_code == 1
        payload = run_result_payload(result.stdout)
        assert (payload["ok"], payload["error_code"], payload["errors"]) == (
            False,
            "CATALOG_NOT_FOUND",
            ["Catalog item 'ghost' not found."],
        )
        assert (payload["session_id"], payload["blueprint_name"], payload["session_status"]) == (None, None, None)
        assert payload["paused_prompt_id"] is None


_UNDECLARED_PLACEHOLDER_STEP: dict[str, object] = {"id": "s1", "run": "true", "env": {"GHOST": "${{ inputs.ghost }}"}}


class RunValidationCliIntegrationTests:
    """Typer runner tests for GH-1096: dovo run validates the blueprint as dovo blueprint validate does."""

    def test_issue_1096_duplicate_step_ids_fail_before_any_step(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        write_runnable_blueprint(
            cli_workspace, key="deploy", steps=[{"id": "a", "run": "echo one"}, {"id": "a", "run": "echo two"}]
        )

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "run", "deploy", "--no-worktree"])

        assert result.exit_code == 1
        assert "Run Failed" in result.stdout
        assert "Step id 'a' is used by more than one step." in result.stdout
        assert "- Resolve: Step id 'a' is used by more than one step." in result.stdout
        paths = initialized_workspace_paths(cli_workspace)
        assert DovoDb(database_file=paths.database_file, project_id=paths.project_id).sessions.list() == []

    def test_issue_1096_agent_step_without_prompt_lists_one_error_without_a_raw_dump(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        write_runnable_blueprint(
            cli_workspace, key="deploy", steps=[{"id": "a", "run": "echo one"}, {"id": "b", "type": "agent"}]
        )

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "run", "deploy", "--no-worktree"])

        assert result.exit_code == 1
        assert "steps.1: Value error, Agent steps must specify a non-empty 'prompt' string." in result.stdout
        assert "errors.pydantic.dev" not in result.stdout
        assert "LoopStepBlock" not in result.stdout

    def test_invalid_blueprint_json_ends_with_the_run_result(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        write_runnable_blueprint(
            cli_workspace,
            key="deploy",
            steps=[
                {"id": "a", "run": "echo one"},
                {"id": "a", "run": "echo two"},
                {"id": "s", "type": "script", "script_path": "missing.sh"},
            ],
        )

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "run", "deploy", "--no-worktree", "--format", "json"]
        )

        assert result.exit_code == 1
        assert [event["event_type"] for event in json_events(result.stdout)] == ["message", RUN_RESULT]
        assert run_result_payload(result.stdout) == {
            "ok": False,
            "error_code": "CATALOG_SCHEMA_INVALID",
            "errors": ["Step id 'a' is used by more than one step."],
            "warnings": ["Step 's' script_path 'missing.sh' does not exist on disk (CATALOG_SCRIPT_NOT_FOUND)."],
            "remediations": [{"message": "Resolve: Step id 'a' is used by more than one step.", "command": None}],
            "diagnostics": [],
            "session_id": None,
            "blueprint_name": None,
            "session_status": None,
            "paused_prompt_id": None,
        }

    def test_validation_warning_is_carried_once_on_the_run_result_and_the_run_completes(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        write_runnable_blueprint(cli_workspace, key="build", steps=[_UNDECLARED_PLACEHOLDER_STEP])

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "run", "build", "--no-worktree", "--format", "json"])

        assert result.exit_code == 0
        payload = run_result_payload(result.stdout)
        assert (payload["ok"], payload["session_status"]) == (True, "completed")
        assert payload["warnings"] == [
            "Placeholder 'inputs.ghost' is not declared in 'inputs:' (CATALOG_UNDECLARED_INPUT_PLACEHOLDER)."
        ]

    def test_validation_warning_prints_in_the_terminal_with_validate_text(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        write_runnable_blueprint(cli_workspace, key="build", steps=[_UNDECLARED_PLACEHOLDER_STEP])

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "run", "build", "--no-worktree"])

        assert result.exit_code == 0, result.stdout
        warning = (
            "Warning: Placeholder 'inputs.ghost' is not declared in 'inputs:' (CATALOG_UNDECLARED_INPUT_PLACEHOLDER)."
        )
        assert result.stdout.count(warning) == 1
        assert result.stdout.index(warning) < result.stdout.index("Blueprint Run Completed:")


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

        `--no-tty` aborts before any prompter call, so PAUSED is reachable only this way; the cancelled run exits 130.
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

        assert result.exit_code == 130
        assert "Run Cancelled" in result.stdout
        run_paths = initialized_workspace_paths(cli_workspace)
        record = DovoDb(database_file=run_paths.database_file, project_id=run_paths.project_id).sessions.get(
            "paused-session-1"
        )
        assert record is not None
        assert record.status == SessionStatus.PAUSED

    def test_run_cli_ask_keyboard_interrupt_pauses_and_exits_130(
        self, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        """Exit 130 and leave the session paused when the interactive terminal raises KeyboardInterrupt at an ask prompt."""
        _force_interactive(monkeypatch)
        monkeypatch.setattr("builtins.input", _raise_keyboard_interrupt)
        interaction = {
            "mode": "select",
            "message": "Approve this plan?",
            "options": [{"value": "approve", "label": "Approve"}, {"value": "deny", "label": "Deny"}],
        }
        write_runnable_blueprint(
            cli_workspace, key="ask-task", steps=[{"id": "approve", "type": "ask", "interaction": interaction}]
        )

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "run", "ask-task", "--no-worktree", "--session-id", "ask-session-1"]
        )

        assert result.exit_code == 130
        assert "Run Cancelled" in result.stdout
        assert "Execution cancelled by user." in result.stdout
        run_paths = initialized_workspace_paths(cli_workspace)
        record = DovoDb(database_file=run_paths.database_file, project_id=run_paths.project_id).sessions.get(
            "ask-session-1"
        )
        assert record is not None
        assert (record.status, record.error_message) == (SessionStatus.PAUSED, "Execution cancelled by user.")

    def test_run_cli_json_format_ends_with_the_completed_run_result(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        write_runnable_blueprint(cli_workspace, key="json-task", steps=[{"id": "s1", "run": "true"}])

        result = cli_runner.invoke(
            app,
            ["-p", str(cli_workspace), "run", "json-task", "--no-worktree", "--session-id", "s-1", "--format", "json"],
        )

        assert result.exit_code == 0
        assert run_result_payload(result.stdout) == {
            "ok": True,
            "error_code": None,
            "errors": [],
            "warnings": [],
            "remediations": [],
            "diagnostics": [],
            "session_id": "s-1",
            "blueprint_name": "json-task",
            "session_status": "completed",
            "paused_prompt_id": None,
        }

    def test_run_cli_json_finalization_failure_carries_plain_warning(
        self,
        cli_runner: CliRunner,
        cli_workspace: Path,
        frozen_diagnostic_clock: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Exit 0 and carry the finalization failure alone in the run result's warnings, with no 'Details logged' text, under --format json."""
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
        payload = run_result_payload(result.stdout)
        assert payload["warnings"] == ["Failed to update run status in database: disk full"]
        assert payload["diagnostics"] == [{"event_id": "exc_3f9a1c07b2de", "log_path": str(frozen_diagnostic_clock)}]
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

    def test_run_cli_json_diagnostics_are_on_the_run_result_without_details_text(
        self,
        cli_runner: CliRunner,
        cli_workspace: Path,
        frozen_diagnostic_clock: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With Blueprint.load raising and --format json, exit 1 with the record in the run result's diagnostics, no replaced event and no 'Details logged' text."""
        write_runnable_blueprint(cli_workspace, key="load-task", steps=[{"id": "s1", "run": "true"}])
        monkeypatch.setattr(Blueprint, "load", classmethod(_raise_type_error))

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "run", "load-task", "--no-worktree", "--format", "json"]
        )

        assert result.exit_code == 1
        payload = run_result_payload(result.stdout)
        assert (payload["ok"], payload["error_code"], payload["errors"]) == (
            False,
            "INTERNAL_ERROR",
            ["Unexpected error during engine.run: bad"],
        )
        assert payload["diagnostics"] == [{"event_id": "exc_3f9a1c07b2de", "log_path": str(frozen_diagnostic_clock)}]
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

        The run handler loads config after its arguments parse and before it resolves the blueprint,
        so no blueprint is resolved and no session row is inserted.
        """
        ui_dispatcher.set_output_format(OutputFormatOptions.TERMINAL)
        write_tier_config(ConfigTier.USER, "{not valid json")

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "run", "unresolved-task", "--no-worktree"])

        assert result.exit_code == 1
        assert "Invalid Configuration" in result.stdout
        assert "Config Error" not in result.stdout
        assert "Invalid configuration in user tier" in result.stdout

    def test_run_cli_undeclared_option_is_ignored_with_a_warning_json(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        """Exit 0 with a completed run whose result warns about '--bogus' and '1' for `run <bp> --bogus 1`."""
        write_runnable_blueprint(cli_workspace, key="extra-task", steps=[{"id": "s1", "run": "true"}])

        result = cli_runner.invoke(
            app,
            ["-p", str(cli_workspace), "run", "extra-task", "--no-worktree", "--bogus", "1", "--format", "json"],
        )

        assert result.exit_code == 0
        payload = run_result_payload(result.stdout)
        assert (payload["ok"], payload["session_status"], payload["warnings"]) == (
            True,
            "completed",
            ["Ignoring unrecognized option '--bogus'.", "Ignoring unexpected argument '1'."],
        )

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

    def test_run_cli_failed_auto_init_json_writes_one_error_event_and_exits_one(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exit 1 with stdout exactly one `error` event carrying the init failure's error_code, errors and remediations."""
        target, _ = _failed_auto_init_target("bootstrap", tmp_path, monkeypatch)

        result = cli_runner.invoke(app, ["-p", str(target), "run", "tests", "--format", "json"])

        assert result.exit_code == 1
        assert len(result.stdout.splitlines()) == 1
        [event] = json_events(result.stdout)
        assert event["event_type"] == "error"
        payload = event["payload"]
        assert isinstance(payload, dict)
        assert payload["ok"] is False
        assert payload["error_code"] == ErrorCode.BOOTSTRAP_PATH_INVALID.value
        assert any("exists but is a file, not a directory." in error for error in payload["errors"])
        assert payload["remediations"]
        assert "Initialization Failed!" not in result.stdout

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

    def test_run_cli_identity_lock_timeout_during_auto_init_reports_it_and_rolls_back(
        self, cli_runner: CliRunner, git_repo: Path, monkeypatch: pytest.MonkeyPatch, dispatch_spy: list[object]
    ) -> None:
        """Exit 1 with the lock timeout as the only dispatched event, no init result, and the new .dovo/ removed."""
        monkeypatch.setattr(WorkspaceLock, "__enter__", raise_lock_timeout)

        result = cli_runner.invoke(app, ["-p", str(git_repo), "run", "tests"])

        assert result.exit_code == 1
        assert [(type(event).__name__, getattr(event, "error_code", None)) for event in dispatch_spy] == [
            ("ErrorEvent", ErrorCode.WORKSPACE_LOCK_TIMEOUT)
        ]
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

    def test_run_cli_json_step_done_event_carries_the_step_error_code(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        write_runnable_blueprint(cli_workspace, key="failing-task", steps=[{"id": "s1", "run": "exit 1"}])

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "run", "failing-task", "--no-worktree", "--format", "json"]
        )

        events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        step_done = next(event["payload"] for event in events if event["event_type"] == "run.step_done")
        assert result.exit_code == 1
        assert (step_done["step_id"], step_done["status"], step_done["exit_code"]) == ("s1", "failed", 1)
        assert (step_done["error_message"], step_done["error_code"]) == (
            "Command failed with exit code 1.",
            "STEP_COMMAND_FAILED",
        )


def _without_duration(payload: dict[str, object]) -> dict[str, object]:
    """Return payload without its wall-clock duration_seconds."""
    return {key: value for key, value in payload.items() if key != "duration_seconds"}


_SHARED_STEP_KEYS = (
    "step_id",
    "status",
    "attempts",
    "exit_code",
    "duration_seconds",
    "error_message",
    "error_code",
    "rejected_patch_path",
)


def _shared_step_fields(payload: dict[str, object]) -> dict[str, object]:
    """Return the step keys every view carries, taken from payload."""
    return {key: payload[key] for key in _SHARED_STEP_KEYS}


def _saved_last_result(cli_workspace: Path, session_id: str) -> dict[str, object]:
    """Return the saved record of the first node's last attempt in the session's session.json."""
    session_dir = initialized_workspace_paths(cli_workspace).session_dir(session_id)
    state = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
    return state["nodes"][0]["attempts"][-1]["result"]


class RunCliStepViewsIntegrationTests:
    """A step reports the same fields in run.step_done, the saved session.json and dovo session show."""

    def test_run_cli_retried_step_reports_the_same_fields_live_saved_and_shown(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        retried: dict[str, object] = {"id": "s1", "run": "exit 1", "on_failure": {"action": "retry", "max_retries": 3}}
        write_runnable_blueprint(cli_workspace, key="retry-task", steps=[retried])

        result = cli_runner.invoke(
            app,
            ["-p", str(cli_workspace), "run", "retry-task", "--no-worktree", "--session-id", "r-1", "--format", "json"],
        )
        shown = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "show", "r-1", "--format", "json"])

        events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        step_done = next(event["payload"] for event in events if event["event_type"] == "run.step_done")
        saved = _saved_last_result(cli_workspace, "r-1")
        step_row = json.loads(shown.stdout)["payload"]["steps"][0]
        assert result.exit_code == 1
        assert _without_duration(_shared_step_fields(step_done)) == {
            "step_id": "s1",
            "status": "failed",
            "attempts": 3,
            "exit_code": 1,
            "error_message": "Command failed with exit code 1.",
            "error_code": "STEP_COMMAND_FAILED",
            "rejected_patch_path": None,
        }
        assert _shared_step_fields(saved) == _shared_step_fields(step_done)
        assert _shared_step_fields(step_row) == _shared_step_fields(step_done)


class RunCliUnfixableAgentIntegrationTests:
    """Typer runner integration tests for an agent step whose agent reports the task unfixable."""

    def test_run_cli_agent_reporting_unfixable_reports_the_same_code_live_saved_and_shown(
        self, cli_runner: CliRunner, cli_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GH_TOKEN", "test-token")
        runner = UnfixableAgentRunner("no SSH key\n").returning(stdout=_AGENT_STDOUT)
        monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)
        _write_agent_blueprint(cli_workspace)
        paths = initialized_workspace_paths(cli_workspace)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "run", "agent-task", "--format", "json"])

        events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        step_done = next(event["payload"] for event in events if event["event_type"] == "run.step_done")
        output_lines = [event["payload"]["line"] for event in events if event["event_type"] == "run.step_output"]
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
        shown = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "session", "show", row.session_id, "--format", "json"]
        )
        (step_row,) = json.loads(shown.stdout)["payload"]["steps"]
        assert _without_duration(step_done) == {
            "idx": 1,
            "total": 1,
            "step_id": "plan",
            "status": "failed",
            "attempts": 1,
            "exit_code": 201,
            "error_message": "Agent reported the task unfixable: no SSH key",
            "error_code": "AGENT_TASK_UNFIXABLE",
            "rejected_patch_path": None,
        }
        assert _saved_last_result(cli_workspace, row.session_id)["error_code"] == "AGENT_TASK_UNFIXABLE"
        assert _without_duration(step_row) == {
            "kind": "step",
            "step_id": "plan",
            "name": None,
            "state": "failed",
            "status": "failed",
            "attempts": 1,
            "exit_code": 201,
            "error_message": "Agent reported the task unfixable: no SSH key",
            "error_code": "AGENT_TASK_UNFIXABLE",
            "rejected_patch_path": None,
            "loop_id": None,
            "iteration": None,
            "iterations": None,
            "skip_cause": None,
            "branch": None,
            "if_id": None,
        }


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
                ["--env-passthrough", "A*B"], 2, "Invalid --env-passthrough value 'A*B':", id="invalid-passthrough"
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
        prompt = next(envelope for envelope in envelopes if envelope["event_type"] == "run.prompt")
        assert result.exit_code == 0
        assert prompt["payload"]["step_id"] == "approve"
        assert [option["decision"] for option in prompt["payload"]["options"]] == ["approve", "revise", "deny"]


_PICKED = "steps.pick.outputs.selected_count > 0"


def _guarded_blueprint(workspace: Path) -> None:
    """Write `guarded`: a multiselect ask, a step and a loop guarded on a non-empty selection, then a plain step."""
    write_runnable_blueprint(
        workspace,
        key="guarded",
        steps=[
            {
                "id": "pick",
                "type": "ask",
                "interaction": {
                    "mode": "multiselect",
                    "message": "Pick the fixes to apply",
                    "options": [{"value": "lint", "label": "Lint"}, {"value": "types", "label": "Types"}],
                },
            },
            {"id": "fold", "name": "Fold the answers into the plan", "run": "touch fold.marker", "when": [_PICKED]},
            {
                "id": "apply",
                "type": "loop",
                "when": [_PICKED],
                "until": ["steps.fix.exit_code == 0"],
                "do": [{"id": "fix", "run": "touch fix.marker"}],
            },
            {"id": "done", "run": "touch done.marker"},
        ],
    )


def _latest_session_id(workspace: Path) -> str:
    paths = initialized_workspace_paths(workspace)
    return DovoDb(database_file=paths.database_file, project_id=paths.project_id).sessions.list(limit=1)[0].session_id


class RunGuardCliIntegrationTests:
    """Typer runner tests for `when` guards across a pause at an ask and `dovo resume --answer`."""

    def test_no_tty_empty_selection_skips_the_guarded_step_and_loop_and_completes(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        _guarded_blueprint(cli_workspace)
        paused = cli_runner.invoke(app, ["-p", str(cli_workspace), "run", "guarded", "--no-tty", "--no-worktree"])
        session_id = _latest_session_id(cli_workspace)

        resumed = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "resume", session_id, "--no-tty", "--answer", "pick="]
        )
        shown = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "show", session_id, "--format", "json"])

        steps = json.loads(shown.stdout.splitlines()[-1])["payload"]["steps"]
        assert (paused.exit_code, resumed.exit_code) == (0, 0)
        assert unwrapped(f"[2/4] Fold the answers into the plan — skipped (when: {_PICKED} → FALSE)") in unwrapped(
            resumed.stdout
        )
        assert unwrapped(f"[3/4] apply — skipped (when: {_PICKED} → FALSE)") in unwrapped(resumed.stdout)
        assert "Blueprint Run Completed:" in resumed.stdout
        assert [(step["step_id"], step["state"], step["skip_cause"]) for step in steps] == [
            ("pick", "completed", None),
            ("fold", "skipped", "when_false"),
            ("apply", "skipped", "when_false"),
            ("done", "completed", None),
        ]
        assert not (cli_workspace / "fold.marker").exists()
        assert not (cli_workspace / "fix.marker").exists()
        assert (cli_workspace / "done.marker").exists()

    def test_json_format_emits_the_step_skipped_event(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        _guarded_blueprint(cli_workspace)
        cli_runner.invoke(app, ["-p", str(cli_workspace), "run", "guarded", "--no-tty", "--no-worktree"])

        resumed = cli_runner.invoke(
            app,
            [
                "-p",
                str(cli_workspace),
                "resume",
                _latest_session_id(cli_workspace),
                "--answer",
                "pick=",
                "--format",
                "json",
            ],
        )

        envelopes = [json.loads(line) for line in resumed.stdout.splitlines() if line.startswith("{")]
        skipped = [envelope["payload"] for envelope in envelopes if envelope["event_type"] == "run.step_skipped"]
        assert skipped[0] == {
            "idx": 2,
            "total": 4,
            "step_id": "fold",
            "name": "Fold the answers into the plan",
            "cause": "when_false",
            "expression": _PICKED,
        }


def _routed_blueprint(workspace: Path) -> None:
    """Write `routed`: a check reporting `not_ready`, then an if block whose `else` branch asks before it fixes."""
    write_runnable_blueprint(
        workspace,
        key="routed",
        steps=[
            {"id": "check", "run": 'echo \'{"verdict": "not_ready"}\''},
            {
                "id": "route",
                "type": "if",
                "condition": ['steps.check.outputs.verdict == "ready"'],
                "then": [{"id": "ship", "run": "touch ship.marker"}],
                "else": [
                    {"id": "approve", "type": "ask", "interaction": {"mode": "confirm", "message": "Fix it first?"}},
                    {"id": "fix", "run": "touch fix.marker"},
                ],
            },
        ],
    )


class RunIfBlockCliIntegrationTests:
    """Typer runner tests for `if` blocks across a pause at an ask and `dovo resume --answer`."""

    def test_no_tty_else_branch_pauses_at_its_ask_and_resumes_without_the_then_steps(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        _routed_blueprint(cli_workspace)
        paused = cli_runner.invoke(app, ["-p", str(cli_workspace), "run", "routed", "--no-tty", "--no-worktree"])
        session_id = _latest_session_id(cli_workspace)

        resumed = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "resume", session_id, "--no-tty", "--answer", "approve=approve"]
        )
        shown = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "show", session_id, "--format", "json"])

        steps = json.loads(shown.stdout.splitlines()[-1])["payload"]["steps"]
        assert (paused.exit_code, resumed.exit_code) == (0, 0)
        assert unwrapped('[2/2] route — else (if: steps.check.outputs.verdict == "ready" → FALSE)') in unwrapped(
            paused.stdout
        )
        assert "route — else" not in resumed.stdout
        assert "Blueprint Run Completed:" in resumed.stdout
        assert [(step["step_id"], step["state"], step["branch"], step["if_id"]) for step in steps] == [
            ("check", "completed", None, None),
            ("route", "completed", "else", None),
            ("approve", "completed", None, "route"),
            ("fix", "completed", None, "route"),
        ]
        assert (cli_workspace / "fix.marker").exists()
        assert not (cli_workspace / "ship.marker").exists()

    def test_json_format_emits_the_if_branch_event(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        _routed_blueprint(cli_workspace)

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "run", "routed", "--no-tty", "--no-worktree", "--format", "json"]
        )

        envelopes = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        branches = [envelope["payload"] for envelope in envelopes if envelope["event_type"] == "run.if_branch"]
        assert branches == [
            {
                "idx": 2,
                "total": 2,
                "block_id": "route",
                "name": None,
                "branch": "else",
                "conditions": [
                    {"expression": 'steps.check.outputs.verdict == "ready"', "passed": False, "resolved": True}
                ],
            }
        ]


class RunContinueExitCodeCliIntegrationTests:
    """Typer runner tests for the exit code a continued failure records (#1141)."""

    def test_issue_1141_loop_over_a_continued_failure_runs_every_iteration(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        check: dict[str, object] = {"id": "check", "run": "exit 1", "on_failure": "continue"}
        loop: dict[str, object] = {
            "id": "loop",
            "type": "loop",
            "max_iterations": 3,
            "until": ["steps.check.exit_code == 0"],
            "on_max_iterations": "continue",
            "do": [check],
        }
        write_runnable_blueprint(cli_workspace, key="continued-loop", steps=[loop])

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "run", "continued-loop", "--no-tty", "--no-worktree"]
        )
        session_id = _latest_session_id(cli_workspace)
        shown = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "show", session_id, "--format", "json"])

        steps = json.loads(shown.stdout.splitlines()[-1])["payload"]["steps"]
        assert result.exit_code == 0
        assert [step["iterations"] for step in steps if step["kind"] == "loop"] == [3]
        assert [(step["state"], step["exit_code"]) for step in steps if step["step_id"] == "check"] == [
            ("ignored", 1)
        ] * 3


_APPROVE_INTERACTION: dict[str, object] = {
    "mode": "select",
    "message": "Approve this plan?",
    "options": [{"value": "approve", "label": "Approve"}, {"value": "deny", "label": "Deny"}],
}


_NON_UTF8_OUTPUT = "printf 'K=\\377\\376\\n' > \"$DOVO_OUTPUT\""


def _interrupt_on_step_start(*_args: object, **_kwargs: object) -> None:
    """Stand in for DispatcherRunObserver.on_step_start, as Ctrl-C would while a step runs."""
    raise KeyboardInterrupt


def _run_json(cli_runner: CliRunner, workspace: Path, key: str, *extra: str) -> tuple[int, RunResultPayload]:
    """Run blueprint key in place as session 's-1' under --format json; return the exit code and the run result."""
    result = cli_runner.invoke(
        app,
        ["-p", str(workspace), "run", key, "--no-worktree", "--session-id", "s-1", "--format", "json", *extra],
    )
    return result.exit_code, run_result_payload(result.stdout)


def _outcome(payload: RunResultPayload) -> tuple[object, ...]:
    """Return the run result's ok, error_code, errors, session fields and paused prompt id."""
    return (
        payload["ok"],
        payload["error_code"],
        payload["errors"],
        payload["session_id"],
        payload["blueprint_name"],
        payload["session_status"],
        payload["paused_prompt_id"],
    )


class RunResultCliTests:
    """Typer runner tests for #1311: every dovo run ends with exactly one run result, its last JSON line."""

    def test_json_headless_pause_at_ask_is_ok_with_the_hint_just_before_the_run_result(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        _ask_blueprint(cli_workspace)

        result = cli_runner.invoke(
            app,
            [
                *["-p", str(cli_workspace), "run", "ask-feature", "--no-tty", "--no-worktree"],
                *["--session-id", "s-1", "--format", "json"],
            ],
        )

        assert result.exit_code == 0
        assert _outcome(run_result_payload(result.stdout)) == (
            True,
            None,
            [],
            "s-1",
            "ask-feature",
            "paused",
            "approve",
        )
        hint = json_events(result.stdout)[-2]
        assert hint == {
            "version": 1,
            "event_type": "message",
            "payload": {
                "message": "Run paused at prompt 'approve'. Answer with 'dovo resume s-1 --answer approve=<value>'.",
                "style": None,
            },
        }

    def test_json_cancelled_during_a_step_reports_run_cancelled(
        self, cli_runner: CliRunner, cli_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(DispatcherRunObserver, "on_step_start", _interrupt_on_step_start)
        write_runnable_blueprint(cli_workspace, key="long-task", steps=[{"id": "s1", "run": "true"}])

        exit_code, payload = _run_json(cli_runner, cli_workspace, "long-task")

        assert exit_code == 130
        assert _outcome(payload) == (
            False,
            "RUN_CANCELLED",
            ["Execution cancelled by user."],
            "s-1",
            "long-task",
            "cancelled",
            None,
        )

    def test_json_interrupted_at_a_failure_prompt_is_cancelled_and_still_paused(
        self, cli_runner: CliRunner, cli_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A Ctrl-C at the failure prompt, which only a terminal reads, still ends the JSON stream with the run result."""
        monkeypatch.setattr(DispatcherFailurePrompter, "prompt_step_failure", _raise_keyboard_interrupt)
        write_runnable_blueprint(
            cli_workspace, key="pause-task", steps=[{"id": "s1", "run": "exit 1", "on_failure": "prompt_user"}]
        )

        exit_code, payload = _run_json(cli_runner, cli_workspace, "pause-task")

        assert exit_code == 130
        assert (payload["ok"], payload["error_code"], payload["session_status"]) == (False, "RUN_CANCELLED", "paused")
        assert len(payload["errors"]) == 1
        assert payload["errors"][0].startswith("Step 's1' failed")

    def test_json_interrupted_at_an_ask_prompt_is_cancelled_and_still_paused(
        self, cli_runner: CliRunner, cli_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A Ctrl-C at the ask prompt, which only a terminal reads, still ends the JSON stream with the run result."""
        monkeypatch.setattr(DispatcherInteractionPrompter, "ask", _raise_keyboard_interrupt)
        write_runnable_blueprint(
            cli_workspace, key="ask-task", steps=[{"id": "approve", "type": "ask", "interaction": _APPROVE_INTERACTION}]
        )

        exit_code, payload = _run_json(cli_runner, cli_workspace, "ask-task")

        assert exit_code == 130
        assert _outcome(payload) == (
            False,
            "RUN_CANCELLED",
            ["Execution cancelled by user."],
            "s-1",
            "ask-task",
            "paused",
            "approve",
        )

    def test_json_failed_step_reports_run_step_failed_with_the_failed_session(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        write_runnable_blueprint(cli_workspace, key="fail-task", steps=[{"id": "s1", "run": "exit 3"}])

        exit_code, payload = _run_json(cli_runner, cli_workspace, "fail-task", "--no-tty")

        assert exit_code == 1
        assert (payload["ok"], payload["error_code"], payload["session_status"]) == (
            False,
            "RUN_STEP_FAILED",
            "failed",
        )
        assert (payload["session_id"], payload["blueprint_name"]) == ("s-1", "fail-task")
        assert payload["errors"][0].startswith("Step 's1' failed")

    def test_step_unexpected_error_emits_step_done_and_named_run_result(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        """A step writing non-UTF-8 bytes to $DOVO_OUTPUT emits its failed run.step_done with INTERNAL_ERROR, then a run result naming the failed session."""
        write_runnable_blueprint(cli_workspace, key="bad-output", steps=[{"id": "s1", "run": _NON_UTF8_OUTPUT}])

        result = cli_runner.invoke(
            app,
            ["-p", str(cli_workspace), "run", "bad-output", "--no-worktree", "--session-id", "s-1", "--format", "json"],
        )

        events = json_events(result.stdout)
        step_done = [event["payload"] for event in events if event["event_type"] == "run.step_done"]
        payload = run_result_payload(result.stdout)
        assert result.exit_code == 1
        assert step_done == [
            {
                "idx": 1,
                "total": 1,
                "step_id": "s1",
                "status": "failed",
                "attempts": 1,
                "exit_code": 1,
                "duration_seconds": 0.0,
                "error_message": "Unexpected error during engine.step: "
                "'utf-8' codec can't decode byte 0xff in position 2: invalid start byte",
                "error_code": "INTERNAL_ERROR",
                "rejected_patch_path": None,
            }
        ]
        assert events[-1]["event_type"] == RUN_RESULT
        assert (payload["error_code"], payload["session_id"], payload["blueprint_name"], payload["session_status"]) == (
            "RUN_STEP_FAILED",
            "s-1",
            "bad-output",
            "failed",
        )

    @pytest.mark.parametrize("output_format", ["terminal", "json"])
    def test_interrupt_during_worktree_setup_prints_run_cancelled(
        self, cli_runner: CliRunner, cli_workspace: Path, monkeypatch: pytest.MonkeyPatch, output_format: str
    ) -> None:
        """An interrupt while the worktree is created ends with the cancelled run result, not 'Aborted!'."""
        monkeypatch.setattr(Worktree, "create", _interrupt_on_step_start)
        write_runnable_blueprint(cli_workspace, key="setup-task", steps=[{"id": "s1", "run": "true"}])

        result = cli_runner.invoke(
            app,
            ["-p", str(cli_workspace), "run", "setup-task", "--session-id", "s-1", "--format", output_format],
        )

        assert result.exit_code == 130
        assert "Aborted!" not in result.output
        if output_format == "terminal":
            assert "RunCancelled" in unwrapped(result.stdout)
            return
        assert json_events(result.stdout)[-1]["event_type"] == RUN_RESULT
        assert _outcome(run_result_payload(result.stdout)) == (
            False,
            "RUN_CANCELLED",
            ["Execution cancelled by user."],
            "s-1",
            "setup-task",
            "cancelled",
            None,
        )

    def test_json_stream_has_no_replaced_events(
        self, cli_runner: CliRunner, cli_workspace: Path, frozen_diagnostic_clock: Path
    ) -> None:
        """A run with a validation warning, a recorded stream failure and a failed step emits none of the replaced events."""
        step = {**_UNDECLARED_PLACEHOLDER_STEP, "run": r"printf '\377'"}
        write_runnable_blueprint(cli_workspace, key="pipe-task", steps=[step])

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "run", "pipe-task", "--no-worktree", "--format", "json"]
        )

        assert result.exit_code == 1
        payload = run_result_payload(result.stdout)
        assert payload["warnings"] != []
        assert payload["diagnostics"] == [{"event_id": "exc_3f9a1c07b2de", "log_path": str(frozen_diagnostic_clock)}]
        assert all("Details logged" not in text for text in [*payload["errors"], *payload["warnings"]])

    def test_terminal_prints_warnings_outcome_then_details_lines(
        self, cli_runner: CliRunner, cli_workspace: Path, frozen_diagnostic_clock: Path
    ) -> None:
        step = {**_UNDECLARED_PLACEHOLDER_STEP, "run": r"printf '\377'"}
        write_runnable_blueprint(cli_workspace, key="pipe-task", steps=[step])

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "run", "pipe-task", "--no-worktree"])

        assert result.exit_code == 1
        output = unwrapped(result.stdout)
        warning = unwrapped("Warning: Placeholder 'inputs.ghost' is not declared in 'inputs:'")
        line = unwrapped(details_line(frozen_diagnostic_clock))
        assert output.index(warning) < output.index("RunFailed") < output.index(line)
        assert output.endswith(line)

    def test_lock_timeout_propagating_from_the_run_emits_no_run_result(
        self, cli_runner: CliRunner, cli_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _lock_timeout(*_args: object, **_kwargs: object) -> None:
            raise LockTimeoutError("lock held")

        monkeypatch.setattr(Engine, "run", _lock_timeout)
        write_runnable_blueprint(cli_workspace, key="json-task", steps=[{"id": "s1", "run": "true"}])

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "run", "json-task", "--no-worktree", "--format", "json"]
        )

        events = json_events(result.stdout)
        assert result.exit_code == 1
        assert RUN_RESULT not in {event["event_type"] for event in events}
        assert lock_timeout_reported(result.stdout)

    def test_discard_lock_timeout_ends_with_error_event_only(
        self, cli_runner: CliRunner, cli_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A lock timeout while a completed run's worktree is discarded ends the invocation with the error event only, and the session already reads completed."""

        def _lock_timeout(*_args: object, **_kwargs: object) -> None:
            raise LockTimeoutError("lock held")

        monkeypatch.setattr(Worktree, "cleanup", _lock_timeout)
        write_runnable_blueprint(cli_workspace, key="json-task", steps=[{"id": "s1", "run": "true"}])

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "run", "json-task", "--session-id", "s-1", "--format", "json"]
        )
        shown = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "show", "s-1", "--format", "json"])

        events = json_events(result.stdout)
        assert json.loads(shown.stdout.splitlines()[-1])["payload"]["session"]["status"] == "completed"
        assert result.exit_code == 1
        assert RUN_RESULT not in {event["event_type"] for event in events}
        assert events[-1] == {
            "version": 1,
            "event_type": "error",
            "payload": {
                "ok": False,
                "error_code": "WORKSPACE_LOCK_TIMEOUT",
                "errors": ["lock held"],
                "warnings": [],
                "remediations": [
                    {
                        "message": "Wait for the other dovo process to release the workspace lock, then retry.",
                        "command": None,
                    }
                ],
                "diagnostics": [],
            },
        }


def _interrupt_on_second_step(_observer: object, idx: int, *_args: object) -> None:
    """Stand in for DispatcherRunObserver.on_step_start, as Ctrl-C would once the second step starts."""
    if idx == 2:
        raise KeyboardInterrupt


def _run_with_a_surviving_group(
    cli_runner: CliRunner, workspace: Path, monkeypatch: pytest.MonkeyPatch, *extra: str
) -> tuple[int, str, int]:
    """Run a blueprint whose first step records its process group and whose second is interrupted, with that group never reported gone; return the exit code, stdout and the group id."""
    pid_file = workspace / "s1.pid"
    write_runnable_blueprint(
        workspace,
        key="survivor-task",
        steps=[{"id": "s1", "run": f"echo $$ > '{pid_file}'"}, {"id": "s2", "run": "true"}],
    )
    monkeypatch.setattr(DispatcherRunObserver, "on_step_start", _interrupt_on_second_step)
    monkeypatch.setattr("dovo.common.process.is_process_group_alive", lambda _pgid: True)
    use_virtual_process_time(monkeypatch)

    result = cli_runner.invoke(
        app, ["-p", str(workspace), "run", "survivor-task", "--no-worktree", "--session-id", "s-1", *extra]
    )
    return result.exit_code, result.stdout, int(pid_file.read_text())


class RunInterruptSurvivorCliTests:
    """Typer runner tests for #1313: an interrupted run whose process group survives the stop ends failed."""

    def test_json_survivor_fails_the_run_and_its_in_flight_step_with_a_kill_remediation(
        self, cli_runner: CliRunner, cli_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exit_code, stdout, pgid = _run_with_a_surviving_group(
            cli_runner, cli_workspace, monkeypatch, "--format", "json"
        )
        shown = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "show", "s-1", "--format", "json"])

        payload = run_result_payload(stdout)
        steps = json.loads(shown.stdout.splitlines()[-1])["payload"]["steps"]
        assert exit_code == 130
        assert _outcome(payload) == (
            False,
            "SESSION_PROCESS_STOP_FAILED",
            [f"Run interrupted, but process group {pgid} is still running."],
            "s-1",
            "survivor-task",
            "failed",
            None,
        )
        assert payload["remediations"] == [
            {
                "message": f"Its processes may still be running; stop them with 'kill -9 -{pgid}'",
                "command": f"kill -9 -{pgid}",
            }
        ]
        assert [(step["step_id"], step["state"]) for step in steps] == [("s1", "completed"), ("s2", "failed")]

    def test_terminal_survivor_prints_run_failed_with_the_group_and_its_kill_command(
        self, cli_runner: CliRunner, cli_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exit_code, stdout, pgid = _run_with_a_surviving_group(cli_runner, cli_workspace, monkeypatch)

        output = unwrapped(stdout)
        assert exit_code == 130
        assert "RunFailed" in output
        assert unwrapped(f"Run interrupted, but process group {pgid} is still running.") in output
        assert unwrapped(f"kill -9 -{pgid}") in output
