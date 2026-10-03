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
        """[tier-1/unit] EnvBinariesCheck.execute: shutil.which('git') is None, config=None -> WARNING, error_code='DOCTOR_BINARY_MISSING', details={'missing_binaries': ['git']}."""
        monkeypatch.setattr("dovo.core.doctor.checks.env_binaries.shutil.which", lambda _name: None)
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

    def test_execute_config_none_defaults_to_local_provider(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] EnvBinariesCheck.execute: context.config=None, git present on PATH -> OK, details={'verified_binaries': ['git']}, error_code=None."""
        monkeypatch.setattr("dovo.core.doctor.checks.env_binaries.shutil.which", lambda _name: "/usr/bin/git")
        check = EnvBinariesCheck()
        context = DoctorContext(cwd=tmp_path, paths=workspace_paths_factory(tmp_path, None))

        result = check.execute(context)

        assert result.check_id == "env.binaries"
        assert result.category == CheckCategory.ENVIRONMENT
        assert result.status == CheckStatus.OK
        assert result.error_code is None
        assert result.details == {"verified_binaries": ["git"]}

    @pytest.mark.parametrize(
        "provider",
        [pytest.param("local", id="local"), pytest.param("ollama", id="ollama"), pytest.param("cursor", id="cursor")],
    )
    def test_execute_provider_without_required_binary_returns_ok(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        provider: AgentProvider,
        workspace_paths_factory: WorkspacePathsFactory,
    ) -> None:
        """[tier-1/unit] EnvBinariesCheck.execute: agent.provider has no PROVIDER_REQUIRED_BINARY entry, git present -> OK, details={'verified_binaries': ['git']}."""
        monkeypatch.setattr("dovo.core.doctor.checks.env_binaries.shutil.which", lambda _name: "/usr/bin/git")
        check = EnvBinariesCheck()
        context = _context_with_provider(tmp_path, provider, workspace_paths_factory)

        result = check.execute(context)

        assert result.check_id == "env.binaries"
        assert result.category == CheckCategory.ENVIRONMENT
        assert result.status == CheckStatus.OK
        assert result.error_code is None
        assert result.details == {"verified_binaries": ["git"]}

    @pytest.mark.parametrize(
        ("provider", "provider_binary"),
        [pytest.param("gemini", "gemini", id="gemini"), pytest.param("copilot", "gh", id="copilot")],
    )
    def test_execute_provider_binary_missing_returns_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        provider: AgentProvider,
        provider_binary: str,
        workspace_paths_factory: WorkspacePathsFactory,
    ) -> None:
        """[tier-1/unit] EnvBinariesCheck.execute: git present, provider CLI binary absent -> WARNING, error_code='DOCTOR_BINARY_MISSING', details={'missing_binaries': [provider_binary]}."""

        def _which(name: str) -> str | None:
            return "/usr/bin/git" if name == "git" else None

        monkeypatch.setattr("dovo.core.doctor.checks.env_binaries.shutil.which", _which)
        check = EnvBinariesCheck()
        context = _context_with_provider(tmp_path, provider, workspace_paths_factory)

        result = check.execute(context)

        message = f"1 required binary(s) not found on PATH: {provider_binary}."
        assert result.check_id == "env.binaries"
        assert result.category == CheckCategory.ENVIRONMENT
        assert result.status == CheckStatus.WARNING
        assert result.error_code == "DOCTOR_BINARY_MISSING"
        assert result.details == {"missing_binaries": [provider_binary]}
        assert result.warnings == [message]

    @pytest.mark.parametrize(
        ("provider", "provider_binary"),
        [pytest.param("gemini", "gemini", id="gemini"), pytest.param("copilot", "gh", id="copilot")],
    )
    def test_execute_provider_binary_present_returns_ok(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        provider: AgentProvider,
        provider_binary: str,
        workspace_paths_factory: WorkspacePathsFactory,
    ) -> None:
        """[tier-1/unit] EnvBinariesCheck.execute: git and provider CLI binary both present -> OK, details={'verified_binaries': ['git', provider_binary]}."""
        monkeypatch.setattr("dovo.core.doctor.checks.env_binaries.shutil.which", lambda _name: "/usr/bin/tool")
        check = EnvBinariesCheck()
        context = _context_with_provider(tmp_path, provider, workspace_paths_factory)

        result = check.execute(context)

        assert result.check_id == "env.binaries"
        assert result.category == CheckCategory.ENVIRONMENT
        assert result.status == CheckStatus.OK
        assert result.error_code is None
        assert result.details == {"verified_binaries": ["git", provider_binary]}
