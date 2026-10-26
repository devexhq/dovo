"""Tests for the config probe through Status.collect()."""

from __future__ import annotations

from dovo.common.models import Remediation
from dovo.core.config.loader import ConfigLoadStatus
from dovo.core.git.runner import GitRunner
from dovo.core.status import Status
from tests.harness.status_workspace import BuildStatusWorkspace
from tests.harness.workspace_paths import initialized_workspace_db, initialized_workspace_paths


class ConfigProbeTests:
    """Tests for configuration status collection."""

    def test_collect_uninitialized_workspace_reports_not_initialized(
        self, status_workspace: BuildStatusWorkspace
    ) -> None:
        workspace = status_workspace(branch="feature-uninit", config=None)

        result = Status(
            initialized_workspace_paths(workspace),
            git=GitRunner(),
            sessions_db=initialized_workspace_db(workspace).sessions,
        ).collect()

        assert result.is_initialized is False
        assert result.git.branch == "feature-uninit"
        assert result.config.is_valid is False
        assert result.warnings == ["Dovo workspace is not initialized. Run 'dovo init' to configure."]
        assert result.remediations == [
            Remediation(message="Run 'dovo init' to initialize Dovo in this repository.", command="dovo init")
        ]

    def test_collect_malformed_config_reports_malformed_json(self, status_workspace: BuildStatusWorkspace) -> None:
        workspace = status_workspace(config="{invalid_json: true")

        result = Status(
            initialized_workspace_paths(workspace),
            git=GitRunner(),
            sessions_db=initialized_workspace_db(workspace).sessions,
        ).collect()

        assert result.config.status == ConfigLoadStatus.MALFORMED_JSON
        assert result.config.is_valid is False
        assert any("CONFIG_MALFORMED_JSON" in err for err in result.config.errors)
        assert result.config.remediations == [Remediation(message="Repair JSON syntax, or restore from backup")]
        assert any("Malformed config.json" in w for w in result.warnings)
        assert result.remediations == [
            Remediation(message="Repair JSON syntax in '.dovo/config.json' or restore from backup.")
        ]
