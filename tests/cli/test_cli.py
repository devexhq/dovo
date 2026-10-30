"""Single-tier CLI smoke and crash-protection tests for the dovo entrypoint."""

from __future__ import annotations

import dataclasses
import signal
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import click
import pytest
import typer
from typer.core import TyperGroup
from typer.testing import CliRunner

import dovo.cli.cli as cli_module
from dovo.cli import app
from dovo.cli.context import CliContext
from dovo.cli.ui import ErrorPanelEvent
from dovo.common.clock import utc_now
from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord
from dovo.common.error_codes import ErrorCode
from dovo.common.lock import LockTimeoutError, WorkspaceLock
from dovo.common.models import Remediation
from dovo.common.session_id import new_session_id
from dovo.core.config import ConfigError, ConfigLoadDiagnostics
from dovo.core.config.generator import build_default_config
from dovo.core.config.loader import ConfigLoadResult
from dovo.core.config.models import DovoConfig
from dovo.core.config.mutate import ConfigSetResult, ConfigSetStatus
from dovo.core.config.validate import ConfigValidationResult
from dovo.core.diagnostics.models import DiagnosticsReport
from dovo.core.git.runner import GitRunner
from dovo.core.status.models import DovoStatusResult
from tests.harness.builders import WorkspaceBuilder
from tests.harness.diagnostic_log import FROZEN_EVENT_ID, diagnostic_hint, read_single_event
from tests.harness.lock import raise_lock_timeout

_CONFIG_ERROR = ConfigError(
    ErrorCode.CONFIG_NOT_FOUND, "Failed to load config at '/r': missing", [Remediation(message="Run 'dovo init'")]
)


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


class RunCliCrashProtectionTests:
    """Direct tests for run_cli()'s top-level exception-to-panel mapping."""

    @pytest.fixture(autouse=True)
    def _restore_signal_handlers(self) -> Iterator[None]:
        """Restore the worker's SIGINT/SIGTERM handlers that run_cli() replaces."""
        saved = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}
        yield
        for signum, handler in saved.items():
            signal.signal(signum, handler)

    def test_run_cli_lock_timeout_renders_error_panel_and_exits_one(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:

        def _raise_lock_timeout() -> None:
            raise LockTimeoutError("lock held by another process")

        monkeypatch.setattr(cli_module, "app", _raise_lock_timeout)

        with pytest.raises(SystemExit) as exc_info:
            cli_module.run_cli()

        assert exc_info.value.code == 1
        assert "Workspace Lock Timeout" in capsys.readouterr().out

    @pytest.mark.parametrize(
        "command",
        [
            pytest.param(["init"], id="init"),
            pytest.param(["init", "--format", "json"], id="init-json"),
            pytest.param(["run", "tests"], id="run-auto-init"),
        ],
    )
    def test_run_cli_identity_lock_timeout_renders_lock_timeout_panel_and_exits_one(
        self,
        git_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        command: list[str],
    ) -> None:
        """Print the Workspace Lock Timeout panel with the lock message and no init result, and exit 1."""
        monkeypatch.setattr(WorkspaceLock, "__enter__", raise_lock_timeout)
        monkeypatch.setattr(sys, "argv", ["dovo", "--path", str(git_repo), *command])

        with pytest.raises(SystemExit) as exc_info:
            cli_module.run_cli()

        stdout = capsys.readouterr().out
        assert exc_info.value.code == 1
        assert "Workspace Lock Timeout" in stdout
        assert "lock held" in stdout
        assert "WorkspaceInitResult" not in stdout
        assert "Initialization Failed!" not in stdout

    def test_run_cli_unexpected_exception_renders_fatal_panel_and_exits_one(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:

        def _raise_unexpected() -> None:
            raise RuntimeError("missing record.id")

        monkeypatch.setattr(cli_module, "app", _raise_unexpected)

        with pytest.raises(SystemExit) as exc_info:
            cli_module.run_cli()

        assert exc_info.value.code == 1
        assert "Fatal Error" in capsys.readouterr().out

    def test_run_cli_unexpected_exception_panel_lists_exception_notes(
        self, monkeypatch: pytest.MonkeyPatch, dispatch_spy: list[object], frozen_diagnostic_clock: Path
    ) -> None:
        """Append each note added to the exception to the Fatal Error panel's details, one per line."""

        def _raise_with_note() -> None:
            exc = PermissionError("unwritable data dir")
            exc.add_note("Remove it, then rerun dovo run.")
            raise exc

        monkeypatch.setattr(cli_module, "app", _raise_with_note)

        with pytest.raises(SystemExit) as exc_info:
            cli_module.run_cli()

        assert exc_info.value.code == 1
        assert dispatch_spy == [
            ErrorPanelEvent(
                title="Fatal Error",
                message=(
                    "A fatal unexpected error occurred.\nDetails: unwritable data dir\nRemove it, then rerun dovo run."
                    + diagnostic_hint(frozen_diagnostic_clock)
                ),
                border_style="red",
            )
        ]

    def test_run_cli_unexpected_exception_records_cli_run_event(
        self, monkeypatch: pytest.MonkeyPatch, frozen_diagnostic_clock: Path
    ) -> None:
        """Write one error-level cli.run event with a null command and the exception's type, message and traceback, and exit 1."""

        def _raise_unexpected() -> None:
            raise RuntimeError("missing record.id")

        monkeypatch.setattr(cli_module, "app", _raise_unexpected)

        with pytest.raises(SystemExit) as exc_info:
            cli_module.run_cli()

        assert exc_info.value.code == 1
        event = read_single_event(frozen_diagnostic_clock)
        assert event["event_id"] == FROZEN_EVENT_ID
        assert event["level"] == "error"
        assert event["operation"] == "cli.run"
        assert event["command"] is None
        assert event["exception_type"] == "RuntimeError"
        assert event["exception_message"] == "missing record.id"
        assert "RuntimeError: missing record.id" in str(event["formatted_traceback"])

    def test_run_cli_config_error_renders_invalid_configuration_panel_and_exits_one(
        self, monkeypatch: pytest.MonkeyPatch, dispatch_spy: list[object]
    ) -> None:
        """Dispatch an Invalid Configuration panel with the error's message and Fix: block, and exit 1."""

        def _raise_config_error() -> None:
            raise _CONFIG_ERROR

        monkeypatch.setattr(cli_module, "app", _raise_config_error)

        with pytest.raises(SystemExit) as exc_info:
            cli_module.run_cli()

        assert exc_info.value.code == 1
        assert dispatch_spy == [
            ErrorPanelEvent(
                title="Invalid Configuration",
                message="Failed to load config at '/r': missing\nFix:\n- Run 'dovo init'",
                border_style="red",
            )
        ]

    def test_run_cli_config_error_lists_carried_diagnostic_records(
        self, monkeypatch: pytest.MonkeyPatch, dispatch_spy: list[object]
    ) -> None:
        """Append a Details logged: line for the record the ConfigError carries."""
        record = DiagnosticRecord(
            event_id=FROZEN_EVENT_ID, log_path=Path("/logs/2026-10-19/event.json"), level=DiagnosticLevel.ERROR
        )

        def _raise_config_error() -> None:
            raise ConfigError(
                _CONFIG_ERROR.error_code,
                _CONFIG_ERROR.message,
                _CONFIG_ERROR.remediations,
                data=ConfigLoadDiagnostics((record,)),
            )

        monkeypatch.setattr(cli_module, "app", _raise_config_error)

        with pytest.raises(SystemExit) as exc_info:
            cli_module.run_cli()

        assert exc_info.value.code == 1
        assert dispatch_spy == [
            ErrorPanelEvent(
                title="Invalid Configuration",
                message=(
                    "Failed to load config at '/r': missing\nFix:\n- Run 'dovo init'"
                    f"\nDetails logged: /logs/2026-10-19/event.json (event {FROZEN_EVENT_ID})"
                ),
                border_style="red",
            )
        ]

    @pytest.mark.parametrize(
        ("case", "expected"),
        [
            pytest.param("bare", 0, id="bare"),
            pytest.param("help", 0, id="help"),
            pytest.param("version", 0, id="version"),
            pytest.param("usage-error", 2, id="usage-error"),
            pytest.param("typer-exit", 1, id="typer-exit"),
        ],
    )
    def test_run_cli_passes_through_click_and_typer_exit_codes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        cli_workspace: Path,
        git_repo: Path,
        case: str,
        expected: int,
    ) -> None:
        """Exit with Click's or the command's own exit code when the argv never reaches a run_cli handler."""
        argv_for: dict[str, Callable[[], list[str]]] = {
            "bare": lambda: [],
            "help": lambda: ["--help"],
            "version": lambda: ["--version"],
            "usage-error": lambda: ["-p", str(cli_workspace), "worktree", "list", "--format", "nope"],
            "typer-exit": lambda: ["-p", str(git_repo), "worktree", "list"],
        }
        monkeypatch.setattr(sys, "argv", ["dovo", *argv_for[case]()])

        with pytest.raises(SystemExit) as exc_info:
            cli_module.run_cli()

        assert exc_info.value.code == expected


GUARD_MESSAGE_HEAD = "Workspace is not initialized: no valid project identity at"
GUARD_FIX = "Run `dovo init` to initialize this workspace."


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
        assert "dovo init --id <project-id> --force" in result.stdout

    def test_status_at_global_home_parent_ignores_stray_global_identity_and_config(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dispatch_spy: list[object]
    ) -> None:
        """Exit 1 for `-p <home> status` with the guard panel, even when <home>/.dovo holds a valid project.json and config.json."""
        home = WorkspaceBuilder(tmp_path / "home").build()
        global_home = home / ".dovo"
        monkeypatch.setenv("DOVO_HOME", str(global_home))

        result = cli_runner.invoke(app, ["-p", str(home), "status"])

        assert result.exit_code == 1
        panels = [event for event in dispatch_spy if isinstance(event, ErrorPanelEvent)]
        assert len(panels) == 1
        assert f"'{global_home.resolve() / 'project.json'}'" in panels[0].message
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
        panels = [event for event in dispatch_spy if isinstance(event, ErrorPanelEvent)]
        assert len(panels) == 1
        assert f"'{start.resolve() / '.dovo' / 'project.json'}'" in panels[0].message
        assert f"'{home.resolve() / '.dovo' / 'project.json'}'" not in panels[0].message

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
        assert [item.status for item in set_results] == [ConfigSetStatus.NOT_FOUND]

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
