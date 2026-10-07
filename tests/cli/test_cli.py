"""Single-tier CLI smoke and crash-protection tests for the dovo entrypoint."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from typer.testing import CliRunner

import dovo.cli.cli as cli_module
from dovo.cli import app
from dovo.cli.context import CliContext
from dovo.common.lock import LockTimeoutError
from dovo.core.config.generator import build_default_config
from dovo.core.config.models import DovoConfig
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

    def test_config_doctor_status_init_build_identical_paths_shape(
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
        commands = (["config", "show"], ["doctor"], ["status"], ["init"])
        for command in commands:
            cli_runner.invoke(app, ["--path", str(workspace), *command])

        assert len(captured) == 4
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
