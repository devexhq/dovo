"""CLI integration tests for dovo step list."""

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
from tests.harness.diagnostic_log import FROZEN_EVENT_ID
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


class StepListCliIntegrationTests:
    """Typer runner integration tests for dovo step list."""

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

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "list", "--format", output_format])

        assert result.exit_code == exit_code
        result_dto = dispatch_spy[-1]
        assert isinstance(result_dto, CatalogListResult)
        assert result_dto.error_code == error_code

    def test_step_list_cli_renders_terminal_table(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.STEP, "listed-step")

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "list"])

        assert result.exit_code == 0
        assert "listed-step" in result.stdout
        assert "repo" in result.stdout
        assert "Steps:" in result.stdout
        assert "Blueprints:" not in result.stdout

    def test_step_list_cli_renders_json(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.STEP, "json-step")

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "list", "--format", "json"])

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["event_type"] == "step.list"
        assert payload["payload"]["items"][0]["tier"] == "repo"
        assert payload["payload"]["item_type"] == "step"

    def test_step_list_cli_only_returns_step_items(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """Exclude blueprints from the step listing; the packaged default.yml step still appears per tier."""
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.STEP, "only-step")
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.BLUEPRINT, "only-blueprint")

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "list", "--format", "json"])

        payload = json.loads(result.stdout)
        names = [item["name"] for item in payload["payload"]["items"]]
        assert names == ["only-step", "default"]
        assert all(item["item_type"] == "step" for item in payload["payload"]["items"])

    def test_step_ls_alias_cli_matches_list_output(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.STEP, "alias-step")

        list_result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "list"])
        ls_result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "ls"])

        assert list_result.exit_code == 0
        assert ls_result.exit_code == 0
        assert list_result.stdout == ls_result.stdout

    def test_step_ls_alias_cli_renders_item_type_json(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """Exit 0 with payload item_type 'step' for `step ls --format json`."""
        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "ls", "--format", "json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout)["payload"]["item_type"] == "step"

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permission bits")
    def test_unreadable_step_json_warns_and_lists_its_record(
        self, cli_runner: CliRunner, isolated_workspace: Path, frozen_diagnostic_clock: Path
    ) -> None:
        """Exit 0 with ok true, the unreadable file's read warning naming a step, and its record in diagnostics."""
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.STEP, "locked")
        locked = isolated_workspace / ".dovo" / "catalog" / "steps" / "locked.yml"
        locked.chmod(0o000)
        try:
            result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "list", "--format", "json"])
        finally:
            locked.chmod(0o644)

        assert result.exit_code == 0
        payload = json.loads(result.stdout)["payload"]
        assert payload["ok"] is True
        assert payload["warnings"] == [
            f"Failed to read catalog step '{locked}': [Errno 13] Permission denied: '{locked}'"
        ]
        assert payload["diagnostics"] == [{"event_id": FROZEN_EVENT_ID, "log_path": str(frozen_diagnostic_clock)}]
