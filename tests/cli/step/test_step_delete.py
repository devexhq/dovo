"""CLI integration tests for dovo step delete."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.lock import LockTimeoutError, WorkspaceLock
from dovo.core.catalog import Catalog
from dovo.core.catalog.models import CatalogItemType
from tests.harness.lock import raise_lock_timeout
from tests.harness.workspace_paths import initialized_workspace_paths

pytestmark = pytest.mark.usefixtures("workspace_config")


class StepDeleteCliIntegrationTests:
    """Typer runner integration tests for dovo step delete."""

    def test_step_delete_cli_with_force_exits_zero(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.STEP, "del-step")

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "delete", "del-step", "--force"])

        assert result.exit_code == 0
        assert not (isolated_workspace / ".dovo" / "catalog" / "steps" / "del-step.yml").exists()

    def test_step_delete_cli_confirmation_declined_exits_one(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.STEP, "declined-step")

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "delete", "declined-step"], input="n\n")

        assert result.exit_code == 1
        assert "Deletion cancelled" in result.stdout

    def test_step_delete_cli_renders_json(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.STEP, "json-del-step")

        result = cli_runner.invoke(
            app,
            ["-p", str(isolated_workspace), "step", "delete", "json-del-step", "--force", "--format", "json"],
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["event_type"] == "CatalogDeleteResult"
        assert payload["payload"]["deleted"] is True

    def test_step_delete_cli_missing_exits_one(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "delete", "missing-step", "--force"])

        assert result.exit_code == 1
        assert "not found" in result.stdout

    def test_step_delete_cli_lock_timeout_propagates(
        self, cli_runner: CliRunner, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exit 1 with result.exception a LockTimeoutError for `delete demo --force` and no 'Catalog Delete Failed' in stdout."""
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.STEP, "demo")
        monkeypatch.setattr(WorkspaceLock, "__enter__", raise_lock_timeout)

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "delete", "demo", "--force"])

        assert result.exit_code == 1
        assert isinstance(result.exception, LockTimeoutError)
        assert "Catalog Delete Failed" not in result.stdout


class StepDeleteCliTests:
    """Typer runner tests for dovo step delete's declined confirmation."""

    def test_declined_confirmation_returns_cancelled_json(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        """Exit 1 with payload cancelled True, deleted False, errors ["Deletion cancelled."], status 'cancelled' and no error_code when stdin answers 'n'."""
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.STEP, "kept-step")

        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "step", "delete", "kept-step", "--format", "json"], input="n\n"
        )

        assert result.exit_code == 1
        payload = json.loads(result.stdout.splitlines()[-1])["payload"]
        assert (payload["cancelled"], payload["deleted"]) == (True, False)
        assert payload["errors"] == ["Deletion cancelled."]
        assert payload["status"] == "cancelled"
        assert payload["error_code"] is None
        assert (isolated_workspace / ".dovo" / "catalog" / "steps" / "kept-step.yml").is_file()
