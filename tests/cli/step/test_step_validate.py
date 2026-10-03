"""CLI integration tests for dovo step validate."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.filesystem.models import RepositoryPaths, WorkspacePaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.catalog import Catalog
from dovo.core.catalog.models import CatalogItemType
from dovo.core.project.services.storage import resolve_workspace_paths


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(None))


class StepValidateCliIntegrationTests:
    """Typer runner integration tests for dovo step validate."""

    def test_step_validate_cli_valid_step_exits_zero(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """dovo step validate <name>: a step with a valid id/run schema passes validation; exit 0."""
        Catalog(_paths_for(isolated_workspace)).save(
            "valid-step", {"id": "valid-step", "run": "echo hi"}, item_type=CatalogItemType.STEP
        )

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "validate", "valid-step"])

        assert result.exit_code == 0
        assert "PASSED" in result.stdout

    def test_step_validate_cli_renders_json(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """dovo step validate <name> --format json: envelope reports valid=true."""
        Catalog(_paths_for(isolated_workspace)).save(
            "json-valid-step", {"id": "json-valid-step", "run": "echo hi"}, item_type=CatalogItemType.STEP
        )

        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "step", "validate", "json-valid-step", "--format", "json"]
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["event_type"] == "CatalogValidateResult"
        assert payload["payload"]["valid"] is True

    def test_step_validate_cli_invalid_step_schema_exits_one(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        """dovo step validate <name>: a default-scaffolded step (missing required 'id') fails schema validation; exit 1."""
        Catalog(_paths_for(isolated_workspace)).create(CatalogItemType.STEP, "invalid-step")

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "validate", "invalid-step"])

        assert result.exit_code == 1
        assert "FAILED" in result.stdout

    def test_step_validate_cli_missing_exits_two(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """dovo step validate missing-step: exits 2 (CATALOG_ITEM_NOT_FOUND)."""
        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "validate", "missing-step"])

        assert result.exit_code == 2
        assert "not found" in result.stdout
