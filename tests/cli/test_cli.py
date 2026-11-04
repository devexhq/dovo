"""Single-tier CLI smoke and crash-protection tests for the dovo entrypoint."""

from __future__ import annotations

import dataclasses
import signal
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import click
import pytest
import typer
from typer.core import TyperGroup
from typer.testing import CliRunner

import dovo.cli.cli as cli_module
from dovo.cli import app
from dovo.cli.context import CliContext
from dovo.cli.ui import ErrorEvent
from dovo.common.clock import utc_now
from dovo.common.error_codes import ErrorCode
from dovo.common.lock import WorkspaceLock
from dovo.common.session_id import new_session_id
from dovo.core.config.generator import build_default_config
from dovo.core.config.loader import ConfigLoadResult
from dovo.core.config.models import DovoConfig
from dovo.core.config.mutate import ConfigSetResult
from dovo.core.config.validate import ConfigValidationResult
from dovo.core.diagnostics.models import DiagnosticsReport
from dovo.core.git.runner import GitRunner
from dovo.core.status.models import DovoStatusResult
from tests.harness.builders import WorkspaceBuilder, init_git_repository
from tests.harness.diagnostic_log import FROZEN_EVENT_ID, read_single_event
from tests.harness.lock import raise_lock_timeout
from tests.harness.run_result import json_events


class CliSmokeTests:
    """Smoke tests for top-level dovo CLI behavior."""

    def test_cli_bare_invocation_prints_banner_and_help_exits_zero(self, cli_runner: CliRunner) -> None:
        result = cli_runner.invoke(app, [])

        assert result.exit_code == 0
        assert "Dovo CLI" in result.stdout
        assert "init" in result.stdout

    def test_mistyped_command_suggests_a_command_group(self, cli_runner: CliRunner) -> None:
        result = cli_runner.invoke(app, ["blueprnt"])

        assert result.exit_code == 2
        assert "Did you mean 'blueprint'?" in click.unstyle(result.stderr)


class CliContextBuildTests:
    """Command-scoped path snapshots."""

    def test_build_returns_paths_for_load_config_false(self, tmp_path: Path) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").without_config().build()

        context = CliContext.build(path=workspace, load_config=False)

        assert context.paths.root_dir == workspace.resolve()
        assert context.config is None

    def test_build_sets_a_git_runner(self, tmp_path: Path) -> None:
        """Return a context whose git is exactly a GitRunner instance (type(context.git) is GitRunner)."""
        workspace = WorkspaceBuilder(tmp_path / "workspace").without_config().build()

        context = CliContext.build(path=workspace, load_config=False)

        assert type(context.git) is GitRunner

    def test_build_sets_production_clock_and_id_generator(self, tmp_path: Path) -> None:
        """Return a context whose clock is dovo.common.clock.utc_now and new_id is new_session_id."""
        workspace = WorkspaceBuilder(tmp_path / "workspace").without_config().build()

        context = CliContext.build(path=workspace, load_config=False)

        assert context.clock is utc_now
        assert context.new_id is new_session_id

    @pytest.mark.parametrize(
        ("names", "expected"),
        [
            pytest.param(["A", "B"], ("A", "B"), id="configured"),
            pytest.param(None, (), id="no_config"),
        ],
    )
    def test_sensitive_variables_returns_configured_names_or_empty_without_config(
        self, tmp_path: Path, names: list[str] | None, expected: tuple[str, ...]
    ) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").without_config().build()
        context = CliContext.build(path=workspace, load_config=False)
        if names is not None:
            payload = build_default_config("demo")
            payload["environment"] = {"sensitive_variables": names}
            context = dataclasses.replace(context, config=DovoConfig.model_validate(payload))

        assert context.sensitive_variables == expected

    def test_build_two_sequential_calls_do_not_share_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first_workspace = WorkspaceBuilder(tmp_path / "first").without_config().build()
        second_workspace = WorkspaceBuilder(tmp_path / "second").without_config().build()
        first_home = tmp_path / "first_home"
        second_home = tmp_path / "second_home"
        monkeypatch.setenv("DOVO_HOME", str(first_home))

        first = CliContext.build(path=first_workspace, load_config=False)
        monkeypatch.setenv("DOVO_HOME", str(second_home))
        second = CliContext.build(path=second_workspace, load_config=False)

        assert first.paths.root_dir == first_workspace.resolve()
        assert first.paths.global_paths.root == first_home.resolve()
        assert second.paths.root_dir == second_workspace.resolve()
        assert second.paths.global_paths.root == second_home.resolve()


class CliNonStrictConfigCommandsCliIntegrationTests:
    """Callback paths that all receive the shared context shape."""

    def test_config_doctor_status_build_identical_paths_shape(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Build every non-strict command's context through CliContext.build, not app-local reconstruction."""
        workspace = WorkspaceBuilder(tmp_path / "workspace").build()
        expected_fields = set(type(CliContext.build(path=workspace).paths).model_fields)
        captured: list[CliContext] = []
        original_build = CliContext.build

        def _capture(cls: type[CliContext], *, path: Path | None = None, load_config: bool = True) -> CliContext:
            context = original_build(path=path, load_config=load_config)
            captured.append(context)
            return context

        monkeypatch.setattr(CliContext, "build", classmethod(_capture))
        commands = (["config", "show"], ["doctor"], ["status"])
        for command in commands:
            cli_runner.invoke(app, ["--path", str(workspace), *command])

        assert len(captured) == 3
        assert all(context.config is None for context in captured)
        assert all(set(type(context.paths).model_fields) == expected_fields for context in captured)


_INIT_REMEDIATION = {"message": "Initialize the workspace.", "command": "dovo init"}
_LOCK_REMEDIATION = {
    "message": "Wait for the other dovo process to release the workspace lock, then retry.",
    "command": None,
}


def _uninitialized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Return argv for a strict command in a git repository with no project identity."""
    repository = WorkspaceBuilder(tmp_path / "workspace").without_identity().build()
    return ["-p", str(repository), "worktree", "list"]


def _invalid_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Return argv for a command in a workspace whose .dovo/project.json is not JSON."""
    workspace = WorkspaceBuilder(tmp_path / "workspace").build()
    (workspace / ".dovo" / "project.json").write_text("not json", encoding="utf-8")
    return ["-p", str(workspace), "status"]


def _broken_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Return argv for a strict command in a workspace whose config.json is not JSON."""
    workspace = WorkspaceBuilder(tmp_path / "workspace").build()
    (workspace / ".dovo" / "config.json").write_text("{broken", encoding="utf-8")
    return ["-p", str(workspace), "worktree", "list"]


def _lock_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Return argv for `init` while another process holds the workspace lock."""
    repository = tmp_path / "repository"
    init_git_repository(repository)
    monkeypatch.setattr(WorkspaceLock, "__enter__", raise_lock_timeout)
    return ["-p", str(repository), "init"]


def _unexpected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Return argv for a command whose context build raises an unexpected exception."""

    def _raise(cls: type[CliContext], **kwargs: object) -> CliContext:
        raise RuntimeError("missing record.id")

    monkeypatch.setattr(CliContext, "build", classmethod(_raise))
    return ["-p", str(tmp_path), "worktree", "list"]


_FAILURES: dict[str, tuple[Callable[[Path, pytest.MonkeyPatch], list[str]], str, str]] = {
    "uninitialized": (_uninitialized, "WORKSPACE_NOT_INITIALIZED", "Workspace Not Initialized"),
    "invalid_identity": (_invalid_identity, "WORKSPACE_NOT_INITIALIZED", "Workspace Not Initialized"),
    "broken_config": (_broken_config, "CONFIG_JSON_INVALID", "Invalid Configuration"),
    "lock_timeout": (_lock_timeout, "WORKSPACE_LOCK_TIMEOUT", "Workspace Lock Timeout"),
    "unexpected": (_unexpected, "INTERNAL_ERROR", "Fatal Error"),
}


class TopLevelFailureCliTests:
    """A failure no command result carries is reported once, as a panel or the JSON `error` event, and exits 1."""

    @pytest.mark.parametrize("spelling", [["--format", "json"], ["--format=json"]], ids=["separate", "joined"])
    @pytest.mark.parametrize("failure", list(_FAILURES))
    def test_failure_outside_a_result_reports_one_error_event(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        frozen_diagnostic_clock: Path,
        failure: str,
        spelling: list[str],
    ) -> None:
        build_argv, error_code = _FAILURES[failure][:2]

        result = cli_runner.invoke(app, [*build_argv(tmp_path, monkeypatch), *spelling])

        events = json_events(result.stdout)
        assert result.exit_code == 1
        assert result.stdout.splitlines()[-1].startswith('{"event_type": "error"')
        assert [event["event_type"] for event in events] == ["error"]
        payload = cast(dict[str, object], events[0]["payload"])
        assert (payload["ok"], payload["error_code"], payload["warnings"]) == (False, error_code, [])

    @pytest.mark.parametrize("failure", list(_FAILURES))
    def test_failure_outside_a_result_renders_panel_in_terminal(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        frozen_diagnostic_clock: Path,
        failure: str,
    ) -> None:
        build_argv, title = _FAILURES[failure][0], _FAILURES[failure][2]

        result = cli_runner.invoke(app, build_argv(tmp_path, monkeypatch))

        assert result.exit_code == 1
        assert title in result.stdout
        assert json_events(result.stdout) == []

    def test_uninitialized_workspace_payload_is_the_failed_header(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`dovo worktree list --format json`, failing before its options parse, writes the full header."""
        argv = _uninitialized(tmp_path, monkeypatch)

        result = cli_runner.invoke(app, [*argv, "--format", "json"])

        identity_path = (tmp_path / "workspace").resolve() / ".dovo" / "project.json"
        assert json_events(result.stdout) == [
            {
                "event_type": "error",
                "payload": {
                    "ok": False,
                    "error_code": "WORKSPACE_NOT_INITIALIZED",
                    "errors": [f"Workspace is not initialized: no valid project identity at '{identity_path}'."],
                    "warnings": [],
                    "remediations": [_INIT_REMEDIATION],
                    "diagnostics": [],
                },
            }
        ]

    def test_lock_timeout_payload_carries_the_wait_remediation_and_no_init_result(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        argv = _lock_timeout(tmp_path, monkeypatch)

        result = cli_runner.invoke(app, [*argv, "--format", "json"])

        assert json_events(result.stdout) == [
            {
                "event_type": "error",
                "payload": {
                    "ok": False,
                    "error_code": "WORKSPACE_LOCK_TIMEOUT",
                    "errors": ["lock held"],
                    "warnings": [],
                    "remediations": [_LOCK_REMEDIATION],
                    "diagnostics": [],
                },
            }
        ]

    def test_config_error_payload_lists_its_load_record(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_diagnostic_clock: Path
    ) -> None:
        """A required config load that cannot read config.json carries its read record into the error event."""
        workspace = WorkspaceBuilder(tmp_path / "workspace").build()
        config_path = workspace / ".dovo" / "config.json"
        real_read_text = Path.read_text

        def _denied_read(
            self: Path, encoding: str | None = None, errors: str | None = None, newline: str | None = None
        ) -> str:
            if self.name == "config.json":
                raise PermissionError("denied")
            return real_read_text(self, encoding=encoding, errors=errors, newline=newline)

        monkeypatch.setattr(Path, "read_text", _denied_read)

        result = cli_runner.invoke(app, ["-p", str(workspace), "worktree", "list", "--format", "json"])

        payload = cast(dict[str, object], json_events(result.stdout)[-1]["payload"])
        assert result.exit_code == 1
        assert payload["error_code"] == "CONFIG_UNREADABLE"
        assert payload["diagnostics"] == [{"event_id": FROZEN_EVENT_ID, "log_path": str(frozen_diagnostic_clock)}]
        assert config_path.exists()

    def test_notes_are_separate_errors(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_diagnostic_clock: Path
    ) -> None:
        """Each note added to the exception follows its message as its own errors entry, and in the panel."""

        def _raise(cls: type[CliContext], **kwargs: object) -> CliContext:
            exc = PermissionError("unwritable data dir")
            exc.add_note("Remove it, then rerun dovo run.")
            raise exc

        monkeypatch.setattr(CliContext, "build", classmethod(_raise))
        argv = ["-p", str(tmp_path), "worktree", "list"]

        json_result = cli_runner.invoke(app, [*argv, "--format", "json"])
        terminal_result = cli_runner.invoke(app, argv)

        payload = cast(dict[str, object], json_events(json_result.stdout)[-1]["payload"])
        assert payload["errors"] == [
            "A fatal unexpected error occurred. Details: unwritable data dir",
            "Remove it, then rerun dovo run.",
        ]
        assert "Remove it, then rerun dovo run." in terminal_result.stdout

    def test_fatal_error_records_cli_run_event(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_diagnostic_clock: Path
    ) -> None:
        """Write one error-level cli.run event naming the subcommand, and list it in the event's diagnostics."""
        argv = _unexpected(tmp_path, monkeypatch)

        result = cli_runner.invoke(app, [*argv, "--format", "json"])

        event = read_single_event(frozen_diagnostic_clock)
        assert (event["event_id"], event["level"], event["operation"], event["command"]) == (
            FROZEN_EVENT_ID,
            "error",
            "cli.run",
            "worktree",
        )
        assert (event["exception_type"], event["exception_message"]) == ("RuntimeError", "missing record.id")
        payload = cast(dict[str, object], json_events(result.stdout)[-1]["payload"])
        assert payload["errors"] == ["A fatal unexpected error occurred. Details: missing record.id"]
        assert payload["diagnostics"] == [{"event_id": FROZEN_EVENT_ID, "log_path": str(frozen_diagnostic_clock)}]

    def test_keyboard_interrupt_is_not_reported_as_an_error_event(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _interrupt(cls: type[CliContext], **kwargs: object) -> CliContext:
            raise KeyboardInterrupt

        monkeypatch.setattr(CliContext, "build", classmethod(_interrupt))

        result = cli_runner.invoke(app, ["-p", str(tmp_path), "worktree", "list", "--format", "json"])

        assert result.exit_code != 0
        assert json_events(result.stdout) == []


class RunCliTests:
    """Direct tests for run_cli(), the process entrypoint."""

    @pytest.fixture(autouse=True)
    def _restore_signal_handlers(self) -> Iterator[None]:
        """Restore the worker's SIGINT/SIGTERM handlers that run_cli() replaces."""
        saved = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}
        yield
        for signum, handler in saved.items():
            signal.signal(signum, handler)

    def test_run_cli_reports_a_lock_timeout_as_the_panel_and_exits_one(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(WorkspaceLock, "__enter__", raise_lock_timeout)
        monkeypatch.setattr(sys, "argv", ["dovo", "--path", str(git_repo), "init"])

        with pytest.raises(SystemExit) as exc_info:
            cli_module.run_cli()

        stdout = capsys.readouterr().out
        assert exc_info.value.code == 1
        assert "Workspace Lock Timeout" in stdout
        assert "Initialization Failed!" not in stdout

    @pytest.mark.parametrize(
        ("case", "expected"),
        [
            pytest.param("bare", 0, id="bare"),
            pytest.param("help", 0, id="help"),
            pytest.param("version", 0, id="version"),
            pytest.param("usage-error", 2, id="usage-error"),
            pytest.param("usage-error-json", 2, id="usage-error-json"),
            pytest.param("workspace-failure", 1, id="workspace-failure"),
        ],
    )
    def test_run_cli_passes_through_click_and_typer_exit_codes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        cli_workspace: Path,
        git_repo: Path,
        case: str,
        expected: int,
    ) -> None:
        """Exit with Click's exit code for usage errors, writing no error event even under --format json."""
        argv_for: dict[str, Callable[[], list[str]]] = {
            "bare": lambda: [],
            "help": lambda: ["--help"],
            "version": lambda: ["--version"],
            "usage-error": lambda: ["-p", str(cli_workspace), "worktree", "list", "--format", "nope"],
            "usage-error-json": lambda: ["-p", str(cli_workspace), "worktree", "list", "--format", "json", "--bogus"],
            "workspace-failure": lambda: ["-p", str(git_repo), "worktree", "list"],
        }
        monkeypatch.setattr(sys, "argv", ["dovo", *argv_for[case]()])

        with pytest.raises(SystemExit) as exc_info:
            cli_module.run_cli()

        assert exc_info.value.code == expected
        assert '"event_type": "error"' not in capsys.readouterr().out or case == "workspace-failure"


GUARD_MESSAGE_HEAD = "Workspace is not initialized: no valid project identity at"
GUARD_FIX = "Initialize the workspace."


class IdentityGuardCliTests:
    """Every command except init requires a project identity."""

    @pytest.mark.parametrize(
        "argv",
        [
            pytest.param(["worktree", "list"], id="worktree-list"),
            pytest.param(["session", "list"], id="session-list"),
            pytest.param(["blueprint", "list"], id="blueprint-list"),
            pytest.param(["step", "list"], id="step-list"),
            pytest.param(["artifacts", "list"], id="artifacts-list"),
            pytest.param(["status"], id="status"),
            pytest.param(["doctor"], id="doctor"),
            pytest.param(["config", "show"], id="config-show"),
        ],
    )
    def test_command_in_uninitialized_repo_prints_prompt_exits_one_and_creates_nothing(
        self, cli_runner: CliRunner, git_repo: Path, argv: list[str]
    ) -> None:
        result = cli_runner.invoke(app, ["-p", str(git_repo), *argv])

        assert result.exit_code == 1
        assert GUARD_MESSAGE_HEAD in result.stdout
        assert GUARD_FIX in result.stdout
        assert not (git_repo / ".dovo").exists()

    def test_legacy_repo_with_config_but_no_identity_fails_with_the_guard_message(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").without_identity().build()

        result = cli_runner.invoke(app, ["-p", str(workspace), "worktree", "list"])

        assert result.exit_code == 1
        assert GUARD_MESSAGE_HEAD in result.stdout
        assert "CONFIG_NOT_FOUND" not in result.stdout
        for runtime_dir in ("tmp", "logs", "sessions", "artifacts"):
            assert not (workspace / ".dovo" / runtime_dir).exists()

    def test_unusable_identity_adds_force_hint(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").build()
        (workspace / ".dovo" / "project.json").write_text("not json", encoding="utf-8")

        result = cli_runner.invoke(app, ["-p", str(workspace), "status"])

        assert result.exit_code == 1
        assert "Run 'dovo init --id <project-id> --force' to replace the unusable identity." in result.stdout

    def test_status_at_global_home_parent_ignores_stray_global_identity_and_config(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dispatch_spy: list[object]
    ) -> None:
        """Exit 1 for `-p <home> status` with the guard panel, even when <home>/.dovo holds a valid project.json and config.json."""
        home = WorkspaceBuilder(tmp_path / "home").build()
        global_home = home / ".dovo"
        monkeypatch.setenv("DOVO_HOME", str(global_home))

        result = cli_runner.invoke(app, ["-p", str(home), "status"])

        assert result.exit_code == 1
        panels = [event for event in dispatch_spy if isinstance(event, ErrorEvent)]
        assert len(panels) == 1
        assert f"'{global_home.resolve() / 'project.json'}'" in panels[0].errors[0]
        assert not any(isinstance(event, DovoStatusResult) for event in dispatch_spy)

    def test_status_under_global_home_names_start_directory_identity(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dispatch_spy: list[object]
    ) -> None:
        """Exit 1 for `-p <home>/scratch/sub status` with the guard panel naming <home>/scratch/sub/.dovo/project.json, not <home>/.dovo/project.json."""
        home = tmp_path / "home"
        start = home / "scratch" / "sub"
        start.mkdir(parents=True)
        (home / ".dovo").mkdir()
        (home / ".git").mkdir()
        monkeypatch.setenv("DOVO_HOME", str(home / ".dovo"))

        result = cli_runner.invoke(app, ["-p", str(start), "status"])

        assert result.exit_code == 1
        panels = [event for event in dispatch_spy if isinstance(event, ErrorEvent)]
        assert len(panels) == 1
        assert f"'{start.resolve() / '.dovo' / 'project.json'}'" in panels[0].errors[0]
        assert f"'{home.resolve() / '.dovo' / 'project.json'}'" not in panels[0].errors[0]

    def test_help_does_not_trip_the_guard_in_an_uninitialized_repo(self, cli_runner: CliRunner, git_repo: Path) -> None:
        result = cli_runner.invoke(app, ["-p", str(git_repo), "worktree", "list", "--help"])

        assert result.exit_code == 0
        assert GUARD_MESSAGE_HEAD not in result.stdout

    @pytest.mark.parametrize("subdirectory", [pytest.param(False, id="root"), pytest.param(True, id="subdirectory")])
    def test_init_works_from_root_and_subdirectory_without_a_context(
        self, cli_runner: CliRunner, git_repo: Path, monkeypatch: pytest.MonkeyPatch, subdirectory: bool
    ) -> None:
        start = git_repo / "nested" if subdirectory else git_repo
        start.mkdir(exist_ok=True)

        def _forbidden_build(cls: type[CliContext], **kwargs: object) -> CliContext:
            raise AssertionError("init must not build a CliContext")

        monkeypatch.setattr(CliContext, "build", classmethod(_forbidden_build))

        result = cli_runner.invoke(app, ["-p", str(start), "init"])

        assert result.exit_code == 0
        assert (git_repo / ".dovo" / "project.json").exists()
        assert (git_repo / ".dovo" / "config.json").exists()

    def test_init_then_next_command_succeeds(self, cli_runner: CliRunner, git_repo: Path) -> None:
        init_result = cli_runner.invoke(app, ["-p", str(git_repo), "init"])

        result = cli_runner.invoke(app, ["-p", str(git_repo), "worktree", "list"])

        assert init_result.exit_code == 0
        assert result.exit_code == 0
        assert GUARD_MESSAGE_HEAD not in result.stdout


class ConfigTolerantCommandsPostInitTests:
    """NON_STRICT_CONFIG_COMMANDS keep reporting once an identity exists."""

    @pytest.mark.parametrize(
        "config_text",
        [
            pytest.param(None, id="missing"),
            pytest.param("{not valid json", id="malformed-json"),
            pytest.param('{"version": 999}', id="schema-invalid"),
        ],
    )
    @pytest.mark.parametrize(
        ("argv", "dispatched_type"),
        [
            pytest.param(["status"], DovoStatusResult, id="status"),
            pytest.param(["doctor"], DiagnosticsReport, id="doctor"),
            pytest.param(["config", "show"], ConfigLoadResult, id="config-show"),
            pytest.param(["config", "validate"], ConfigValidationResult, id="config-validate"),
        ],
    )
    def test_tolerant_command_still_reports_with_a_valid_identity_and_broken_config(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
        dispatch_spy: list[Any],
        argv: list[str],
        dispatched_type: type[object],
        config_text: str | None,
    ) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_git().without_config().build()
        if config_text is not None:
            (workspace / ".dovo" / "config.json").write_text(config_text, encoding="utf-8")

        result = cli_runner.invoke(app, ["-p", str(workspace), *argv])

        assert GUARD_MESSAGE_HEAD not in result.stdout
        assert "Fatal Error" not in result.stdout
        assert any(isinstance(item, dispatched_type) for item in dispatch_spy)

    def test_config_set_with_a_valid_identity_and_a_missing_config_dispatches_its_result(
        self, cli_runner: CliRunner, tmp_path: Path, dispatch_spy: list[Any]
    ) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").without_config().build()

        result = cli_runner.invoke(
            app, ["-p", str(workspace), "config", "set", "agents.copilot.model", "qwen2.5-coder"]
        )

        set_results = [item for item in dispatch_spy if isinstance(item, ConfigSetResult)]
        assert GUARD_MESSAGE_HEAD not in result.stdout
        assert [item.error_code for item in set_results] == [ErrorCode.CONFIG_NOT_FOUND]

    def test_strict_command_with_broken_config_still_fails_with_the_config_panel(
        self, cli_runner: CliRunner, tmp_path: Path, dispatch_spy: list[object]
    ) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").without_config().build()
        (workspace / ".dovo" / "config.json").write_text("{not valid json", encoding="utf-8")

        result = cli_runner.invoke(app, ["-p", str(workspace), "worktree", "list"])

        assert result.exit_code == 1
        assert "Invalid Configuration" in result.stdout
        assert "Malformed config.json at" in result.stdout
        assert "Fix:" in result.stdout
        assert GUARD_MESSAGE_HEAD not in result.stdout
        assert not any(isinstance(item, ConfigLoadResult) for item in dispatch_spy)


class SessionCliTests:
    """Root wiring of the session group and its shortcuts."""

    @pytest.mark.parametrize(
        ("shortcut", "grouped"),
        [
            pytest.param(["logs", "sess-a"], ["session", "logs", "sess-a"], id="logs"),
            pytest.param(["diff", "sess-a"], ["session", "diff", "sess-a"], id="diff"),
            pytest.param(["resume", "ghost"], ["session", "resume", "ghost"], id="resume"),
        ],
    )
    def test_shortcut_and_session_form_produce_identical_output_and_exit_code(
        self, cli_runner: CliRunner, tmp_path: Path, shortcut: list[str], grouped: list[str]
    ) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_git().with_database().build()

        shortcut_result = cli_runner.invoke(app, ["-p", str(workspace), *shortcut])
        grouped_result = cli_runner.invoke(app, ["-p", str(workspace), *grouped])

        assert shortcut_result.exit_code == 1
        assert grouped_result.exit_code == shortcut_result.exit_code
        assert grouped_result.stdout == shortcut_result.stdout

    def test_shortcut_commands_run_the_session_command_of_the_same_name(self) -> None:
        root = typer.main.get_group(app)
        session_group = root.commands["session"]

        assert isinstance(session_group, TyperGroup)
        for name in ("logs", "diff", "resume"):
            assert root.commands[name] is session_group.commands[name]


_COMMANDS_WITH_FORMAT_ARGV = [
    ["artifacts", "list"],
    ["artifacts", "download", "s", "n", "--dest", "d"],
    ["artifacts", "prune"],
    ["blueprint", "list"],
    ["blueprint", "ls"],
    ["blueprint", "show", "x"],
    ["blueprint", "create", "--name", "x"],
    ["blueprint", "delete", "x"],
    ["blueprint", "validate", "x"],
    ["step", "list"],
    ["step", "ls"],
    ["step", "show", "x"],
    ["step", "create", "--name", "x"],
    ["step", "delete", "x"],
    ["step", "validate", "x"],
    ["config", "show"],
    ["config", "set", "k", "v"],
    ["config", "unset", "k"],
    ["config", "validate"],
    ["doctor"],
    ["init"],
    ["status"],
    ["session"],
    ["session", "list"],
    ["session", "ls"],
    ["session", "show"],
    ["session", "logs"],
    ["session", "diff"],
    ["session", "resume"],
    ["logs"],
    ["diff"],
    ["resume"],
    ["worktree", "create"],
    ["worktree", "list"],
    ["worktree", "show", "x"],
    ["worktree", "prune"],
    ["worktree", "delete", "x"],
    ["worktree", "apply", "x"],
    ["worktree", "diff", "x"],
    ["run", "x"],
]
COMMANDS_WITH_FORMAT = [pytest.param(command, id="-".join(command)) for command in _COMMANDS_WITH_FORMAT_ARGV]


class FormatOptionCliTests:
    """The shared --format option accepts only 'terminal' and 'json' on every command."""

    @pytest.mark.parametrize("command", COMMANDS_WITH_FORMAT)
    def test_help_lists_json_and_terminal_choices(
        self, cli_runner: CliRunner, cli_workspace: Path, command: list[str]
    ) -> None:
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), *command, "--help"])

        assert result.exit_code == 0
        assert "json|terminal" in click.unstyle(result.stdout)
        assert "Output format: 'terminal' or 'json'." in click.unstyle(result.stdout)

    @pytest.mark.parametrize("command", COMMANDS_WITH_FORMAT)
    def test_invalid_format_is_rejected_on_every_command(
        self, cli_runner: CliRunner, cli_workspace: Path, command: list[str]
    ) -> None:
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), *command, "--format", "yaml"])

        assert result.exit_code == 2
        assert result.stdout == ""
        assert "Invalid value for '--format': 'yaml' is not one of 'json', 'terminal'." in click.unstyle(result.stderr)

    @pytest.mark.parametrize("value", ["raw", "JSON", "yaml"])
    def test_invalid_format_values_are_rejected(self, cli_runner: CliRunner, cli_workspace: Path, value: str) -> None:
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "diff", "--format", value])

        assert result.exit_code == 2
        assert result.stdout == ""
        assert f"Invalid value for '--format': '{value}' is not one of 'json', 'terminal'." in click.unstyle(
            result.stderr
        )

    @pytest.mark.parametrize(
        ("argv", "expected"),
        [
            pytest.param(
                ["session", "diff", "--raw", "--format", "json"], "No such option: --raw", id="unknown-option-json"
            ),
            pytest.param(["worktree", "show"], "Missing argument", id="missing-argument-terminal"),
            pytest.param(["worktree", "show", "--format", "json"], "Missing argument", id="missing-argument-json"),
            pytest.param(
                ["run", "x", "--keep", "--discard", "--format", "json"], "mutually exclusive", id="bad-parameter-json"
            ),
        ],
    )
    def test_usage_errors_write_only_to_stderr_and_exit_two(
        self, cli_runner: CliRunner, cli_workspace: Path, argv: list[str], expected: str
    ) -> None:
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), *argv])

        assert result.exit_code == 2
        assert result.stdout == ""
        assert expected in click.unstyle(result.stderr)
