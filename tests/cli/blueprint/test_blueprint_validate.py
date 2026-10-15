"""CLI integration tests for dovo blueprint validate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.catalog import Catalog
from dovo.core.catalog.models import CatalogItemType
from tests.harness.ask import approval_gate_blueprint, review_triage_blueprint, revise_loop_blueprint
from tests.harness.workspace_paths import initialized_workspace_paths


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return initialized_workspace_paths(root)


class BlueprintValidateCliIntegrationTests:
    """Typer runner integration tests for dovo blueprint validate."""

    def test_blueprint_validate_cli_valid_blueprint_exits_zero(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        Catalog(_paths_for(isolated_workspace)).create(CatalogItemType.BLUEPRINT, "valid-blueprint")

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "blueprint", "validate", "valid-blueprint"])

        assert result.exit_code == 0
        assert "PASSED" in result.stdout

    def test_blueprint_validate_cli_renders_json(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        Catalog(_paths_for(isolated_workspace)).create(CatalogItemType.BLUEPRINT, "json-valid-blueprint")

        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "blueprint", "validate", "json-valid-blueprint", "--format", "json"]
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["event_type"] == "CatalogValidateResult"
        assert payload["payload"]["valid"] is True

    def test_blueprint_validate_cli_missing_exits_two(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "blueprint", "validate", "missing-blueprint"])

        assert result.exit_code == 2
        assert "not found" in result.stdout


class BlueprintValidateAskCliIntegrationTests:
    """Typer runner integration tests for dovo blueprint validate over ask blueprints."""

    @pytest.mark.parametrize(
        "document",
        [
            pytest.param(approval_gate_blueprint(), id="approval-gate"),
            pytest.param(revise_loop_blueprint(), id="revise-loop"),
            pytest.param(review_triage_blueprint(), id="review-triage"),
        ],
    )
    def test_worked_ask_blueprints_validate_with_exit_zero(
        self, cli_runner: CliRunner, isolated_workspace: Path, document: dict[str, object]
    ) -> None:
        target = isolated_workspace / "worked.yml"
        target.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "blueprint", "validate", str(target)])

        assert result.exit_code == 0, result.stdout
