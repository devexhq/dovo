"""CLI integration tests for dovo blueprint list."""

from __future__ import annotations

import errno
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.filesystem import Filesystem
from dovo.core.catalog import Catalog
from dovo.core.catalog.models import CatalogItemType, CatalogListResult
from tests.harness.exit_codes import OUTPUT_FORMATS
from tests.harness.workspace_paths import initialized_workspace_paths

pytestmark = pytest.mark.usefixtures("workspace_config")


def _writable_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leave catalog index writes untouched."""


def _full_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every atomic JSON write fail as it would on a full disk."""

    def _no_space(*args: object, **kwargs: object) -> NoReturn:
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

    monkeypatch.setattr(Filesystem, "atomic_write_json", _no_space)


class BlueprintListCliIntegrationTests:
    """Typer runner integration tests for dovo blueprint list."""

    @pytest.mark.parametrize("output_format", OUTPUT_FORMATS)
    @pytest.mark.parametrize(
        ("arrange", "exit_code", "error_code"),
        [
            pytest.param(_writable_disk, 0, None, id="writable_catalog"),
            pytest.param(_full_disk, 1, "CATALOG_WRITE_FAILED", id="unwritable_catalog_index"),
        ],
    )
    def test_exit_code_follows_result(
        self,
        cli_runner: CliRunner,
        isolated_workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        dispatch_spy: list[object],
        output_format: str,
        arrange: Callable[[pytest.MonkeyPatch], None],
        exit_code: int,
        error_code: str | None,
    ) -> None:
        arrange(monkeypatch)

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "blueprint", "list", "--format", output_format])

        assert result.exit_code == exit_code
        result_dto = dispatch_spy[-1]
        assert isinstance(result_dto, CatalogListResult)
        assert result_dto.error_code == error_code

    def test_blueprint_list_cli_renders_terminal_table(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.BLUEPRINT, "listed-blueprint")

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "blueprint", "list"])

        assert result.exit_code == 0
        assert "listed-blueprint" in result.stdout
        assert "repo" in result.stdout
        assert "Blueprints:" in result.stdout

    def test_blueprint_list_cli_renders_json(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.BLUEPRINT, "json-blueprint")

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "blueprint", "list", "--format", "json"])

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["event_type"] == "blueprint.list"
        assert payload["payload"]["items"][0]["tier"] == "repo"
        assert payload["payload"]["item_type"] == "blueprint"

    def test_blueprint_list_cli_only_returns_blueprint_items(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        """Exclude steps from the blueprint listing; the packaged default.yml blueprint still appears per tier."""
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.BLUEPRINT, "only-blueprint")
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.STEP, "only-step")

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "blueprint", "list", "--format", "json"])

        payload = json.loads(result.stdout)
        names = [item["name"] for item in payload["payload"]["items"]]
        assert names == ["only-blueprint", "default"]
        assert all(item["item_type"] == "blueprint" for item in payload["payload"]["items"])

    def test_blueprint_ls_alias_cli_matches_list_output(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.BLUEPRINT, "alias-blueprint")

        list_result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "blueprint", "list"])
        ls_result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "blueprint", "ls"])

        assert list_result.exit_code == 0
        assert ls_result.exit_code == 0
        assert list_result.stdout == ls_result.stdout

    def test_blueprint_ls_alias_cli_renders_item_type_json(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        """Exit 0 with payload item_type 'blueprint' for `blueprint ls --format json`."""
        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "blueprint", "ls", "--format", "json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout)["payload"]["item_type"] == "blueprint"
