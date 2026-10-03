"""Unit tests for dovo.core.doctor.checks.env_binaries."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.common.filesystem import WorkspacePaths
from dovo.core.config.models import AgentConfig, AgentProvider, DovoConfig, ProjectConfig
from dovo.core.doctor.checks.env_binaries import EnvBinariesCheck
from dovo.core.doctor.models import CheckCategory, CheckStatus, DoctorContext

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]


def _context_with_provider(
    cwd: Path, provider: AgentProvider, workspace_paths_factory: WorkspacePathsFactory
) -> DoctorContext:
    config = DovoConfig(version=1, project=ProjectConfig(name="demo"), agent=AgentConfig(provider=provider))
    return DoctorContext(cwd=cwd, config=config, paths=workspace_paths_factory(cwd, None))


class EnvBinariesCheckTests:
    """Unit tests for EnvBinariesCheck diagnostic outcomes."""

    def test_execute_missing_git_binary_returns_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] EnvBinariesCheck.execute: shutil.which('git') is None and 'gh' present, config=None -> WARNING, error_code='DOCTOR_BINARY_MISSING', details={'missing_binaries': ['git']}."""

        def _which(name: str) -> str | None:
            return "/usr/bin/gh" if name == "gh" else None

        monkeypatch.setattr("dovo.core.doctor.checks.env_binaries.shutil.which", _which)
        check = EnvBinariesCheck()
        context = DoctorContext(cwd=tmp_path, paths=workspace_paths_factory(tmp_path, None))

        result = check.execute(context)

        message = "1 required binary(s) not found on PATH: git."
        assert result.check_id == "env.binaries"
        assert result.category == CheckCategory.ENVIRONMENT
        assert result.status == CheckStatus.WARNING
        assert result.error_code == "DOCTOR_BINARY_MISSING"
        assert result.details == {"missing_binaries": ["git"]}
        assert result.warnings == [message]

    def test_execute_config_none_defaults_to_copilot_requires_git_and_gh(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] EnvBinariesCheck.execute: context.config=None -> required binaries == ['git', 'gh'], both present -> OK, details={'verified_binaries': ['git', 'gh']}, error_code=None."""
        monkeypatch.setattr("dovo.core.doctor.checks.env_binaries.shutil.which", lambda _name: "/usr/bin/tool")
        check = EnvBinariesCheck()
        context = DoctorContext(cwd=tmp_path, paths=workspace_paths_factory(tmp_path, None))

        result = check.execute(context)

        assert result.check_id == "env.binaries"
        assert result.category == CheckCategory.ENVIRONMENT
        assert result.status == CheckStatus.OK
        assert result.error_code is None
        assert result.details == {"verified_binaries": ["git", "gh"]}

    def test_execute_provider_binary_missing_returns_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] EnvBinariesCheck.execute: git present, copilot CLI binary 'gh' absent -> WARNING, error_code='DOCTOR_BINARY_MISSING', details={'missing_binaries': ['gh']}."""

        def _which(name: str) -> str | None:
            return "/usr/bin/git" if name == "git" else None

        monkeypatch.setattr("dovo.core.doctor.checks.env_binaries.shutil.which", _which)
        check = EnvBinariesCheck()
        context = _context_with_provider(tmp_path, "copilot", workspace_paths_factory)

        result = check.execute(context)

        message = "1 required binary(s) not found on PATH: gh."
        assert result.check_id == "env.binaries"
        assert result.category == CheckCategory.ENVIRONMENT
        assert result.status == CheckStatus.WARNING
        assert result.error_code == "DOCTOR_BINARY_MISSING"
        assert result.details == {"missing_binaries": ["gh"]}
        assert result.warnings == [message]
