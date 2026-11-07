"""Tests for the config probe through Status.collect()."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.common.models import Remediation
from dovo.core.git.runner import GitRunner
from dovo.core.status import Status, WorkspaceHealth
from tests.harness.diagnostic_log import diagnostic_record, read_single_event
from tests.harness.fakes import RecordingStepStateCloser
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
            closer=RecordingStepStateCloser(),
        ).collect()

        assert result.is_initialized is False
        assert result.git.branch == "feature-uninit"
        assert result.config.is_valid is False
        assert result.warnings == ["Dovo workspace is not initialized. Run 'dovo init' to configure."]
        assert result.config.error_code == ErrorCode.CONFIG_NOT_FOUND
        assert result.remediations == [
            Remediation(message="Run 'dovo init' to create '.dovo/config.json'", command="dovo init")
        ]

    def test_collect_malformed_config_reports_config_json_invalid(self, status_workspace: BuildStatusWorkspace) -> None:
        workspace = status_workspace(config="{invalid_json: true")

        result = Status(
            initialized_workspace_paths(workspace),
            git=GitRunner(),
            sessions_db=initialized_workspace_db(workspace).sessions,
            closer=RecordingStepStateCloser(),
        ).collect()

        assert result.config.error_code == ErrorCode.CONFIG_JSON_INVALID
        assert result.config.is_valid is False
        assert any(err.startswith("Malformed config.json at") for err in result.config.errors)
        assert result.config.remediations == [Remediation(message="Repair JSON syntax, or restore from backup")]
        assert any("Malformed config.json" in w for w in result.warnings)
        assert result.remediations == [Remediation(message="Repair JSON syntax, or restore from backup")]

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permission bits")
    def test_collect_unreadable_config_lists_the_config_read_record(
        self, status_workspace: BuildStatusWorkspace, frozen_diagnostic_clock: Path
    ) -> None:
        """With config.json at mode 0, report CONFIG_UNREADABLE and list the config.read record in diagnostics."""
        workspace = status_workspace()
        paths = initialized_workspace_paths(workspace)
        paths.config_file.chmod(0)

        try:
            result = Status(
                paths,
                git=GitRunner(),
                sessions_db=initialized_workspace_db(workspace).sessions,
                closer=RecordingStepStateCloser(),
            ).collect()
        finally:
            paths.config_file.chmod(0o600)

        record = diagnostic_record(frozen_diagnostic_clock)
        assert result.status is WorkspaceHealth.DEGRADED
        assert result.config.error_code == ErrorCode.CONFIG_UNREADABLE
        assert result.config.diagnostics == [record]
        assert result.diagnostics == [record]
        assert read_single_event(frozen_diagnostic_clock)["operation"] == "config.read"
