"""Single-tier CLI integration tests for dovo init."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.constants import DOVO_GITIGNORE_TRACKED_ENTRIES
from dovo.common.filesystem import Filesystem
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.common.lock import LockTimeoutError, WorkspaceLock
from dovo.core.bootstrap.models import BootstrapOutcome
from dovo.core.project.models import PROJECT_ID_REGEX, ProjectIdentity
from tests.harness.builders import init_git_repository
from tests.harness.diagnostic_log import FROZEN_EVENT_ID
from tests.harness.lock import raise_lock_timeout


def _dovo_dir(workspace: Path) -> Path:
    return workspace / ".dovo"


class InitCliIntegrationTests:
    """Typer runner integration tests for dovo init."""

    def test_init_cli_fresh_git_repo_creates_workspace_exits_zero(
        self, cli_runner: CliRunner, tmp_path: Path, dispatch_spy: list[Any]
    ) -> None:
        init_git_repository(tmp_path)

        result = cli_runner.invoke(app, ["-p", str(tmp_path), "init"])

        assert result.exit_code == 0
        dovo_dir = _dovo_dir(tmp_path)
        assert (dovo_dir / "config.json").exists()
        assert (resolve_global_paths(None).data_dir / "dovo.db").is_file()
        assert len(dispatch_spy) == 1
        res = dispatch_spy[0]
        assert res.bootstrap_result.root_path == dovo_dir
        assert res.bootstrap_result.outcome == BootstrapOutcome.INITIALIZED
        assert res.config_result.created is True

    def test_init_cli_rerun_without_flags_skips_existing_exits_zero(
        self, cli_runner: CliRunner, tmp_path: Path, dispatch_spy: list[Any]
    ) -> None:
        init_git_repository(tmp_path)
        cli_runner.invoke(app, ["-p", str(tmp_path), "init"])
        dispatch_spy.clear()

        result = cli_runner.invoke(app, ["-p", str(tmp_path), "init"])

        assert result.exit_code == 0
        assert len(dispatch_spy) == 1
        res = dispatch_spy[0]
        assert res.bootstrap_result.root_path == _dovo_dir(tmp_path)
        assert res.bootstrap_result.outcome == BootstrapOutcome.ALREADY_INITIALIZED
        assert res.config_result.skipped_existing is True

    def test_init_cli_overwrite_flag_regenerates_default_config_exits_zero(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        init_git_repository(tmp_path)
        cli_runner.invoke(app, ["-p", str(tmp_path), "init"])
        config_path = tmp_path / ".dovo" / "config.json"
        config_data = json.loads(config_path.read_text(encoding="utf-8"))
        config_data["project"]["name"] = "mutated-name"
        config_path.write_text(json.dumps(config_data), encoding="utf-8")

        result = cli_runner.invoke(app, ["-p", str(tmp_path), "init", "--overwrite"])

        assert result.exit_code == 0
        persisted = json.loads(config_path.read_text(encoding="utf-8"))
        assert persisted["project"]["name"] == tmp_path.name

    def test_init_cli_outside_git_repo_exits_one(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(app, ["-p", str(tmp_path), "init"])

        assert result.exit_code == 1
        assert "not a valid Git repository" in result.stdout
        assert not (tmp_path / ".dovo").exists()

    def test_init_cli_json_format_emits_result_event(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        init_git_repository(tmp_path)

        result = cli_runner.invoke(app, ["-p", str(tmp_path), "init", "--id", "test-project", "--format", "json"])

        assert result.exit_code == 0
        event = json.loads(result.stdout)
        assert event["event_type"] == "WorkspaceInitResult"
        assert event["payload"]["ok"] is True
        assert event["payload"]["project_id"] == "test-project"

    def test_init_cli_in_global_home_parent_exits_one_with_json_refusal(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exit 1 for `-p <home> init --format json` with payload ok False, failure_mode preflight and the global-home refusal."""
        home = tmp_path / "home"
        global_home = home / ".dovo"
        global_home.mkdir(parents=True)
        init_git_repository(home)
        monkeypatch.setenv("DOVO_HOME", str(global_home))

        result = cli_runner.invoke(app, ["-p", str(home), "init", "--format", "json"])

        assert result.exit_code == 1
        payload = json.loads(result.stdout)["payload"]
        assert payload["ok"] is False
        assert payload["failure_mode"] == "preflight"
        assert len(payload["errors"]) == 1
        assert "its .dovo directory is the global Dovo home" in payload["errors"][0]

    def test_init_cli_with_id_flag_creates_project_json_with_explicit_id(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        init_git_repository(tmp_path)

        result = cli_runner.invoke(app, ["-p", str(tmp_path), "init", "--id", "custom-id"])

        assert result.exit_code == 0
        project_json = json.loads((_dovo_dir(tmp_path) / "project.json").read_text(encoding="utf-8"))
        assert project_json["id"] == "custom-id"

    def test_init_cli_with_invalid_id_flag_exits_two(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        init_git_repository(tmp_path)

        result = cli_runner.invoke(app, ["-p", str(tmp_path), "init", "--id", "Bad Id!"])

        assert result.exit_code == 2
        assert "Invalid project ID: must match" in result.stdout
        assert "Pass a valid '--id' matching" in result.stdout
        assert "Run 'dovo init' without '--id' to keep the existing project ID or generate a new one." in result.stdout
        assert not (_dovo_dir(tmp_path) / "project.json").exists()

    def test_init_cli_with_invalid_id_json_payload_exits_two(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """Exit 2 with failure_mode invalid_project_id, the identity path, the invalid-id error and remediation."""
        init_git_repository(tmp_path)

        result = cli_runner.invoke(app, ["-p", str(tmp_path), "init", "--id", "Bad Id!", "--format", "json"])

        assert result.exit_code == 2
        payload = json.loads(result.stdout)["payload"]
        assert payload["failure_mode"] == "invalid_project_id"
        assert payload["identity_path_relative"] == ".dovo/project.json"
        assert payload["errors"] == [f"Invalid project ID: must match {PROJECT_ID_REGEX}"]
        assert payload["remediations"] == [
            {"message": f"Pass a valid '--id' matching '{PROJECT_ID_REGEX}'.", "command": None},
            {
                "message": "Run 'dovo init' without '--id' to keep the existing project ID or generate a new one.",
                "command": "dovo init",
            },
        ]
        assert not (_dovo_dir(tmp_path) / "project.json").exists()

    def test_init_cli_invalid_identity_schema_exits_one_with_json_failure(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """Exit 1 with failure_mode bootstrap, the invalid-schema error without a Details logged line, and no config.json."""
        init_git_repository(tmp_path)
        identity_path = Filesystem(tmp_path).dovo_dir / "project.json"
        identity_path.parent.mkdir()
        identity_path.write_text("{not-json", encoding="utf-8")
        with pytest.raises(ValidationError) as validation:
            ProjectIdentity.model_validate_json("{not-json")

        result = cli_runner.invoke(app, ["-p", str(tmp_path), "init", "--format", "json"])

        assert result.exit_code == 1
        payload = json.loads(result.stdout)["payload"]
        assert payload["failure_mode"] == "bootstrap"
        assert payload["errors"] == [f"Project identity at '{identity_path}' is invalid: {validation.value}."]
        assert payload["remediations"] == [
            {"message": "Resolve the path conflict above, then rerun 'dovo init'", "command": None}
        ]
        assert not (_dovo_dir(tmp_path) / "config.json").exists()

    def test_init_cli_unexpected_provisioning_error_exits_one_with_json_failure(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_diagnostic_clock: Path
    ) -> None:
        """Exit 1 with failure_mode bootstrap, the unexpected error alone, its record in diagnostics, and no traceback."""
        init_git_repository(tmp_path)

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise TypeError("boom")

        monkeypatch.setattr("dovo.core.project.services.identity.generate_project_identity", _boom)

        result = cli_runner.invoke(app, ["-p", str(tmp_path), "init", "--format", "json"])

        assert result.exit_code == 1
        payload = json.loads(result.stdout)["payload"]
        assert payload["failure_mode"] == "bootstrap"
        assert payload["errors"] == ["Unexpected error during bootstrap.initialize: boom"]
        assert payload["diagnostics"] == [{"event_id": FROZEN_EVENT_ID, "log_path": str(frozen_diagnostic_clock)}]
        assert "Traceback" not in result.stdout

    @pytest.mark.parametrize(
        "format_args", [pytest.param([], id="terminal"), pytest.param(["--format", "json"], id="json")]
    )
    def test_init_cli_identity_lock_timeout_raises_and_dispatches_no_result(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        dispatch_spy: list[object],
        format_args: list[str],
    ) -> None:
        """Exit 1 with result.exception the LockTimeoutError, no WorkspaceInitResult dispatched, and no project.json."""
        init_git_repository(tmp_path)
        monkeypatch.setattr(WorkspaceLock, "__enter__", raise_lock_timeout)

        result = cli_runner.invoke(app, ["-p", str(tmp_path), "init", *format_args])

        assert result.exit_code == 1
        assert isinstance(result.exception, LockTimeoutError)
        assert str(result.exception) == "lock held"
        assert dispatch_spy == []
        assert not (_dovo_dir(tmp_path) / "project.json").exists()

    def test_init_cli_overwrite_repair_rerun_orders_warnings(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """Exit 0 for a second `init --overwrite --repair --format json` with the preserve warning first."""
        init_git_repository(tmp_path)
        cli_runner.invoke(app, ["-p", str(tmp_path), "init", "--id", "first-id"])

        result = cli_runner.invoke(app, ["-p", str(tmp_path), "init", "--overwrite", "--repair", "--format", "json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout)["payload"]["warnings"] == [
            "Project identity already exists (id=first-id); preserving existing identity. "
            "Rerun with --id <new-id> --force to replace it.",
            "repair ignored when overwrite=True",
        ]

    def test_init_cli_with_id_and_display_name_writes_display_name(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        init_git_repository(tmp_path)

        result = cli_runner.invoke(
            app, ["-p", str(tmp_path), "init", "--id", "custom-id", "--display-name", "My Project"]
        )

        assert result.exit_code == 0
        project_json = json.loads((_dovo_dir(tmp_path) / "project.json").read_text(encoding="utf-8"))
        assert project_json["id"] == "custom-id"
        assert project_json["display_name"] == "My Project"

    def test_init_cli_force_with_id_overwrites_identity_only(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        init_git_repository(tmp_path)
        cli_runner.invoke(app, ["-p", str(tmp_path), "init"])
        config_path = _dovo_dir(tmp_path) / "config.json"
        config_data = json.loads(config_path.read_text(encoding="utf-8"))
        config_data["project"]["name"] = "mutated-name"
        config_path.write_text(json.dumps(config_data), encoding="utf-8")

        result = cli_runner.invoke(app, ["-p", str(tmp_path), "init", "--id", "new-id", "--force"])

        assert result.exit_code == 0
        project_json = json.loads((_dovo_dir(tmp_path) / "project.json").read_text(encoding="utf-8"))
        assert project_json["id"] == "new-id"
        assert json.loads(config_path.read_text(encoding="utf-8"))["project"]["name"] == "mutated-name"

    def test_init_cli_fresh_git_repo_reports_gitignore_tracked_entries(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        init_git_repository(tmp_path)

        result = cli_runner.invoke(app, ["-p", str(tmp_path), "init"])

        assert result.exit_code == 0
        assert "tracking only" in result.stdout
        for entry in DOVO_GITIGNORE_TRACKED_ENTRIES:
            assert entry in result.stdout
