"""Tests for the catalog probe through Status.collect()."""

from __future__ import annotations

from dovo.common.filesystem import Filesystem
from dovo.core.git.runner import GitRunner
from dovo.core.status import Status
from tests.harness.status_workspace import BuildStatusWorkspace
from tests.harness.workspace_paths import initialized_workspace_paths


class CatalogProbeTests:
    """Tests for catalog status collection."""

    def test_collect_missing_catalog_directory_reports_absent(self, status_workspace: BuildStatusWorkspace) -> None:
        workspace = status_workspace()

        result = Status(initialized_workspace_paths(workspace), git=GitRunner()).collect()

        assert result.is_initialized is True
        assert result.catalog.exists is False
        assert result.catalog.total_items == 0

    def test_collect_invalid_catalog_blueprint_counts_invalid_item(
        self, status_workspace: BuildStatusWorkspace
    ) -> None:
        workspace = status_workspace()
        fs = Filesystem(workspace)

        Filesystem.atomic_write_text(fs.catalog_blueprints_dir / "valid-bp.yml", "name: valid-bp\n")
        Filesystem.atomic_write_text(fs.catalog_blueprints_dir / "bad.yml", "invalid: [yaml: broken\n")

        result = Status(initialized_workspace_paths(workspace), git=GitRunner()).collect()

        assert result.catalog.exists is True
        assert result.catalog.total_items == 2
        assert result.catalog.invalid_items == 1
        assert result.catalog.item_names == ["bad", "valid-bp"]
        assert result.warnings == ["1 invalid blueprint file(s) detected in catalog."]
