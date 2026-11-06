"""Tests for the catalog probe through Status.collect()."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dovo.common.filesystem import Filesystem
from dovo.core.git.runner import GitRunner
from dovo.core.status import Status, WorkspaceHealth
from tests.harness.diagnostic_log import diagnostic_record, read_single_event
from tests.harness.status_workspace import BuildStatusWorkspace
from tests.harness.workspace_paths import initialized_workspace_db, initialized_workspace_paths


class CatalogProbeTests:
    """Tests for catalog status collection."""

    def test_collect_missing_catalog_directory_reports_absent(self, status_workspace: BuildStatusWorkspace) -> None:
        workspace = status_workspace()

        result = Status(
            initialized_workspace_paths(workspace),
            git=GitRunner(),
            sessions_db=initialized_workspace_db(workspace).sessions,
        ).collect()

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

        result = Status(
            initialized_workspace_paths(workspace),
            git=GitRunner(),
            sessions_db=initialized_workspace_db(workspace).sessions,
        ).collect()

        assert result.catalog.exists is True
        assert result.catalog.total_items == 2
        assert result.catalog.invalid_items == 1
        assert result.catalog.item_names == ["bad", "valid-bp"]
        assert result.warnings == ["1 invalid blueprint file(s) detected in catalog."]

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permission bits")
    def test_collect_unreadable_catalog_file_records_the_read_failure(
        self, status_workspace: BuildStatusWorkspace, frozen_diagnostic_clock: Path
    ) -> None:
        """With a blueprint at mode 0, count it invalid, warn, and list its status.catalog record in diagnostics."""
        workspace = status_workspace()
        fs = Filesystem(workspace)
        unreadable = fs.catalog_blueprints_dir / "locked.yml"
        Filesystem.atomic_write_text(unreadable, "name: locked\n")
        unreadable.chmod(0)

        try:
            result = Status(
                initialized_workspace_paths(workspace),
                git=GitRunner(),
                sessions_db=initialized_workspace_db(workspace).sessions,
            ).collect()
        finally:
            unreadable.chmod(0o600)

        record = diagnostic_record(frozen_diagnostic_clock)
        event = read_single_event(frozen_diagnostic_clock)
        assert result.status is WorkspaceHealth.DEGRADED
        assert result.catalog.invalid_items == 1
        assert result.catalog.item_names == ["locked"]
        assert result.warnings == ["1 invalid blueprint file(s) detected in catalog."]
        assert result.catalog.diagnostics == [record]
        assert result.diagnostics == [record]
        assert event["operation"] == "status.catalog"
        assert event["context"] == {"path": str(unreadable)}

    def test_collect_unparsable_catalog_file_writes_no_record(
        self, status_workspace: BuildStatusWorkspace, frozen_diagnostic_clock: Path
    ) -> None:
        """A blueprint that reads but is not a YAML mapping is invalid content, not a caught failure, so diagnostics stays []."""
        workspace = status_workspace()
        fs = Filesystem(workspace)
        Filesystem.atomic_write_text(fs.catalog_blueprints_dir / "list.yml", "- one\n- two\n")

        result = Status(
            initialized_workspace_paths(workspace),
            git=GitRunner(),
            sessions_db=initialized_workspace_db(workspace).sessions,
        ).collect()

        assert result.catalog.invalid_items == 1
        assert result.catalog.diagnostics == []
        assert result.diagnostics == []
        assert not frozen_diagnostic_clock.exists()
