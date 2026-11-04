"""CLI integration tests for dovo step create."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.common.lock import WorkspaceLock
from dovo.core.catalog import Catalog
from dovo.core.catalog.models import CatalogItemType
from tests.harness.exit_codes import OUTPUT_FORMATS, assert_exit_follows_result
from tests.harness.lock import lock_timeout_reported, raise_lock_timeout
from tests.harness.workspace_paths import initialized_workspace_paths

pytestmark = pytest.mark.usefixtures("workspace_config")


class StepCreateCliIntegrationTests:
    """Typer runner integration tests for dovo step create."""

    @pytest.mark.parametrize("output_format", OUTPUT_FORMATS)
    @pytest.mark.parametrize(
        ("name", "exit_code", "error_code"),
        [
            pytest.param("new-step", 0, None, id="new_name"),
            pytest.param("dup-step", 1, "CATALOG_ALREADY_EXISTS", id="colliding_name"),
        ],
    )
    def test_exit_code_follows_result(
        self,
        cli_runner: CliRunner,
        isolated_workspace: Path,
        output_format: str,
        name: str,
        exit_code: int,
        error_code: str | None,
    ) -> None:
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.STEP, "dup-step")

        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "step", "create", "--name", name, "--format", output_format]
        )

        assert_exit_follows_result(result, output_format, exit_code, error_code)

    def test_step_create_cli_creates_item_terminal(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "create", "--name", "new-step"])

        assert result.exit_code == 0
        assert "Created step 'new-step' (tier: repo)" in result.stdout
        target_file = isolated_workspace / ".dovo" / "catalog" / "steps" / "new-step.yml"
        assert target_file.is_file()

    def test_step_create_cli_renders_json(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "step", "create", "--name", "json-step", "--format", "json"]
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["event_type"] == "CatalogCreateResult"
        assert payload["payload"]["item"]["tier"] == "repo"
        assert payload["payload"]["item"]["item_type"] == "step"
        assert payload["payload"]["item_type"] == "step"

    @pytest.mark.parametrize(
        ("tier", "catalog_dir_attr"),
        [
            pytest.param("user", "user_catalog_dir", id="user"),
            pytest.param("global", "global_catalog_dir", id="global"),
        ],
    )
    def test_step_create_cli_tier_flag_writes_tier_file(
        self, cli_runner: CliRunner, isolated_workspace: Path, tier: str, catalog_dir_attr: str
    ) -> None:
        result = cli_runner.invoke(
            app,
            ["-p", str(isolated_workspace), "step", "create", "--name", "tier-step", f"--{tier}", "--format", "json"],
        )

        assert result.exit_code == 0
        assert json.loads(result.stdout)["payload"]["item"]["tier"] == tier
        catalog_dir: Path = getattr(resolve_global_paths(None), catalog_dir_attr)
        assert (catalog_dir / "steps" / "tier-step.yml").is_file()

    def test_step_create_cli_lock_timeout_propagates(
        self, cli_runner: CliRunner, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exit 1 with the lock timeout reported and no 'Catalog Creation Failed' in stdout."""
        monkeypatch.setattr(WorkspaceLock, "__enter__", raise_lock_timeout)

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "create", "--name", "demo"])

        assert result.exit_code == 1
        assert lock_timeout_reported(result.stdout)
        assert "Catalog Creation Failed" not in result.stdout


class StepCreateCliTests:
    """Typer runner tests for dovo step create's classified failures."""

    def test_user_and_global_together_is_tier_conflict_json(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        """Exit 1 with the CATALOG_TIER_CONFLICT JSON naming item_type 'step' and no both-step.yml in any tier."""
        result = cli_runner.invoke(
            app,
            [
                "-p",
                str(isolated_workspace),
                "step",
                "create",
                "--name",
                "both-step",
                "--user",
                "--global",
                "--format",
                "json",
            ],
        )

        assert result.exit_code == 1
        assert json.loads(result.stdout)["payload"] == {
            "errors": ["--user and --global are mutually exclusive."],
            "warnings": [],
            "remediations": [{"message": "Pass at most one of --user and --global.", "command": None}],
            "diagnostics": [],
            "error_code": "CATALOG_TIER_CONFLICT",
            "item_type": "step",
            "item": None,
            "resolved_path": None,
        }
        global_paths = resolve_global_paths(None)
        assert not (isolated_workspace / ".dovo" / "catalog" / "steps" / "both-step.yml").exists()
        assert not (global_paths.user_catalog_dir / "steps" / "both-step.yml").exists()
        assert not (global_paths.global_catalog_dir / "steps" / "both-step.yml").exists()

    def test_collision_json_has_already_exists_code(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """Exit 1 with error_code CATALOG_ALREADY_EXISTS and the collision message."""
        cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "create", "--name", "dup-step"])

        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "step", "create", "--name", "dup-step", "--format", "json"]
        )

        assert result.exit_code == 1
        payload = json.loads(result.stdout)["payload"]
        assert payload["error_code"] == "CATALOG_ALREADY_EXISTS"
        assert payload["errors"] == ["Catalog blueprint collision at path 'steps/dup-step.yml'"]
