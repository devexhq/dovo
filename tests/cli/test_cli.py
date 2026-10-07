"""Single-tier CLI smoke and crash-protection tests for the dovo entrypoint."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import dovo.cli.cli as cli_module
from dovo.cli import app
from dovo.cli.context import CliContext
from dovo.common.lock import LockTimeoutError
from dovo.core.config.generator import build_default_config
from dovo.core.config.loader import ConfigLoadResult
from dovo.core.config.models import DovoConfig
from dovo.core.config.mutate import ConfigSetResult, ConfigSetStatus
from dovo.core.config.validate import ConfigValidationResult
from dovo.core.diagnostics.models import DiagnosticsReport
from dovo.core.status.models import DovoStatusResult
from tests.harness.builders import WorkspaceBuilder


class CliSmokeTests:
    """Smoke tests for top-level dovo CLI behavior."""

    def test_cli_bare_invocation_prints_banner_and_help_exits_zero(self, cli_runner: CliRunner) -> None:
        """dovo (bare invocation): exit 0, 'Dovo CLI' and 'init' in stdout."""
        result = cli_runner.invoke(app, [])

        assert result.exit_code == 0
        assert "Dovo CLI" in result.stdout
        assert "init" in result.stdout


class CliContextBuildTests:
    """[tier-3/integration] Command-scoped path snapshots."""

    def test_build_returns_paths_for_load_config_false(self, tmp_path: Path) -> None:
        """Non-strict context construction returns paths without requiring config loading."""
        workspace = WorkspaceBuilder(tmp_path / "workspace").without_config().build()

        context = CliContext.build(path=workspace, load_config=False)

        assert context.paths.root_dir == workspace.resolve()
        assert context.config is None

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
        """[tier-1/unit] CliContext.sensitive_variables: config.environment.sensitive_variables ["A","B"] returns ("A","B"); config=None returns ()."""
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
        """Two roots and global homes produce independent immutable snapshots."""
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
    """[tier-3/integration] Callback paths all receive the shared context shape."""

    def test_config_doctor_status_build_identical_paths_shape(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """All non-strict commands use CliContext.build rather than app-local reconstruction."""
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

    def test_run_cli_lock_timeout_renders_error_panel_and_exits_one(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """run_cli(): app() raising LockTimeoutError renders a 'Workspace Lock Timeout' panel and exits via SystemExit(1)."""

        def _raise_lock_timeout() -> None:
            raise LockTimeoutError("lock held by another process")

        monkeypatch.setattr(cli_module, "app", _raise_lock_timeout)

        with pytest.raises(SystemExit) as exc_info:
            cli_module.run_cli()

        assert exc_info.value.code == 1
        assert "Workspace Lock Timeout" in capsys.readouterr().out

    def test_run_cli_unexpected_exception_renders_fatal_panel_and_exits_one(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """run_cli(): app() raising a bare Exception renders a 'Fatal Error' panel and exits via SystemExit(1)."""

        def _raise_unexpected() -> None:
            raise RuntimeError("missing record.id")

        monkeypatch.setattr(cli_module, "app", _raise_unexpected)

        with pytest.raises(SystemExit) as exc_info:
            cli_module.run_cli()

        assert exc_info.value.code == 1
        assert "Fatal Error" in capsys.readouterr().out


GUARD_MESSAGE_HEAD = "Workspace is not initialized: no valid project identity at"
GUARD_FIX = "Run `dovo init` to initialize this workspace."


class IdentityGuardCliTests:
    """[tier-3/integration] Every command except init requires a project identity."""

    @pytest.mark.parametrize(
        "argv",
        [
            pytest.param(["worktree", "list"], id="worktree-list"),
            pytest.param(["history", "list"], id="history-list"),
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
        """dovo <command>: git repo with no .dovo/ exits 1, prints the guard message and fix, and creates no .dovo/."""
        result = cli_runner.invoke(app, ["-p", str(git_repo), *argv])

        assert result.exit_code == 1
        assert GUARD_MESSAGE_HEAD in result.stdout
        assert GUARD_FIX in result.stdout
        assert not (git_repo / ".dovo").exists()

    def test_legacy_repo_with_config_but_no_identity_fails_with_the_guard_message(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """dovo worktree list: a workspace with config.json and no project.json exits 1 with the guard message and creates no runtime directories under .dovo/."""
        workspace = WorkspaceBuilder(tmp_path / "workspace").without_identity().build()

        result = cli_runner.invoke(app, ["-p", str(workspace), "worktree", "list"])

        assert result.exit_code == 1
        assert GUARD_MESSAGE_HEAD in result.stdout
        assert "CONFIG_NOT_FOUND" not in result.stdout
        for runtime_dir in ("tmp", "logs", "sessions", "artifacts"):
            assert not (workspace / ".dovo" / runtime_dir).exists()

    def test_unusable_identity_adds_force_hint(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """dovo status: a workspace with an invalid project.json exits 1 and mentions 'dovo init --id <project-id> --force'."""
        workspace = WorkspaceBuilder(tmp_path / "workspace").build()
        (workspace / ".dovo" / "project.json").write_text("not json", encoding="utf-8")

        result = cli_runner.invoke(app, ["-p", str(workspace), "status"])

        assert result.exit_code == 1
        assert "dovo init --id <project-id> --force" in result.stdout

    def test_help_does_not_trip_the_guard_in_an_uninitialized_repo(self, cli_runner: CliRunner, git_repo: Path) -> None:
        """dovo worktree list --help: git repo with no .dovo/ exits 0 and prints no guard message."""
        result = cli_runner.invoke(app, ["-p", str(git_repo), "worktree", "list", "--help"])

        assert result.exit_code == 0
        assert GUARD_MESSAGE_HEAD not in result.stdout

    @pytest.mark.parametrize("subdirectory", [pytest.param(False, id="root"), pytest.param(True, id="subdirectory")])
    def test_init_works_from_root_and_subdirectory_without_a_context(
        self, cli_runner: CliRunner, git_repo: Path, monkeypatch: pytest.MonkeyPatch, subdirectory: bool
    ) -> None:
        """dovo init: from the repo root or a subdirectory of an uninitialized repo writes project.json and config.json and never calls CliContext.build."""
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
        """dovo init followed by dovo worktree list: the second command exits 0 and prints no guard message."""
        init_result = cli_runner.invoke(app, ["-p", str(git_repo), "init"])

        result = cli_runner.invoke(app, ["-p", str(git_repo), "worktree", "list"])

        assert init_result.exit_code == 0
        assert result.exit_code == 0
        assert GUARD_MESSAGE_HEAD not in result.stdout


class ConfigTolerantCommandsPostInitTests:
    """[tier-3/integration] NON_STRICT_CONFIG_COMMANDS keep reporting once an identity exists."""

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
        """dovo status|doctor|config show|config validate: valid identity plus missing, malformed, or schema-invalid config.json dispatches the command's normal result and never prints the guard message."""
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_git().without_config().build()
        if config_text is not None:
            (workspace / ".dovo" / "config.json").write_text(config_text, encoding="utf-8")

        result = cli_runner.invoke(app, ["-p", str(workspace), *argv])

        assert "WORKSPACE_NOT_INITIALIZED" not in result.stdout
        assert "Fatal Error" not in result.stdout
        assert any(isinstance(item, dispatched_type) for item in dispatch_spy)

    def test_config_set_with_a_valid_identity_and_a_missing_config_dispatches_its_result(
        self, cli_runner: CliRunner, tmp_path: Path, dispatch_spy: list[Any]
    ) -> None:
        """dovo config set: valid identity and no config.json dispatches the normal ConfigSetResult and never prints the guard message."""
        workspace = WorkspaceBuilder(tmp_path / "workspace").without_config().build()

        result = cli_runner.invoke(app, ["-p", str(workspace), "config", "set", "agent.model", "qwen2.5-coder"])

        set_results = [item for item in dispatch_spy if isinstance(item, ConfigSetResult)]
        assert "WORKSPACE_NOT_INITIALIZED" not in result.stdout
        assert GUARD_MESSAGE_HEAD not in result.stdout
        assert [item.status for item in set_results] == [ConfigSetStatus.NOT_FOUND]

    def test_strict_command_with_broken_config_still_fails_with_the_config_panel(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """dovo worktree list: valid identity and malformed config.json exits 1 with a config error and not the guard message."""
        workspace = WorkspaceBuilder(tmp_path / "workspace").without_config().build()
        (workspace / ".dovo" / "config.json").write_text("{not valid json", encoding="utf-8")

        result = cli_runner.invoke(app, ["-p", str(workspace), "worktree", "list"])

        assert result.exit_code == 1
        assert "Config Error" in result.stdout
        assert GUARD_MESSAGE_HEAD not in result.stdout
