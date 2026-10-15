"""Unit tests for dovo.core.diagnostics.checks.env_binaries."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from dovo.common.filesystem import WorkspacePaths
from dovo.core.agents.registry import PROVIDERS
from dovo.core.config.models import AgentConfig, AgentProvider, DovoConfig, ProjectConfig
from dovo.core.diagnostics.checks.env_binaries import EnvBinariesCheck
from dovo.core.diagnostics.models import CheckCategory, CheckStatus, DiagnosticsContext

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]

BINARY_TOKENS = sorted(token for token, spec in PROVIDERS.items() if spec.binary is not None)
DEFAULT_TOKEN = AgentConfig().provider


def _context_with_provider(
    cwd: Path, provider: str, workspace_paths_factory: WorkspacePathsFactory
) -> DiagnosticsContext:
    agent = AgentConfig(provider=cast(AgentProvider, provider))
    config = DovoConfig(version=1, project=ProjectConfig(name="demo"), agent=agent)
    return DiagnosticsContext(cwd=cwd, config=config, paths=workspace_paths_factory(cwd, None))


def _which_only(*present: str) -> Callable[[str], str | None]:
    return lambda name: f"/usr/bin/{name}" if name in present else None


class EnvBinariesCheckTests:
    """Unit tests for EnvBinariesCheck diagnostic outcomes."""

    def test_execute_missing_git_binary_returns_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        provider_binary = PROVIDERS[DEFAULT_TOKEN].binary
        assert provider_binary is not None
        monkeypatch.setattr("dovo.core.diagnostics.checks.env_binaries.shutil.which", _which_only(provider_binary))
        context = DiagnosticsContext(cwd=tmp_path, paths=workspace_paths_factory(tmp_path, None))

        result = EnvBinariesCheck().execute(context)

        assert result.check_id == "env.binaries"
        assert result.category == CheckCategory.ENVIRONMENT
        assert result.status == CheckStatus.WARNING
        assert result.error_code == "DOCTOR_BINARY_MISSING"
        assert result.details == {"missing_binaries": ["git"]}
        assert result.warnings == ["1 required binary(s) not found on PATH: git."]

    @pytest.mark.parametrize("token", sorted(PROVIDERS))
    def test_execute_provider_binary_present_returns_ok(
        self,
        token: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        workspace_paths_factory: WorkspacePathsFactory,
    ) -> None:
        monkeypatch.setattr("dovo.core.diagnostics.checks.env_binaries.shutil.which", lambda _name: "/usr/bin/tool")
        binary = PROVIDERS[token].binary
        context = _context_with_provider(tmp_path, token, workspace_paths_factory)

        result = EnvBinariesCheck().execute(context)

        assert result.check_id == "env.binaries"
        assert result.category == CheckCategory.ENVIRONMENT
        assert result.status == CheckStatus.OK
        assert result.error_code is None
        assert result.details == {"verified_binaries": ["git"] + ([binary] if binary else [])}

    @pytest.mark.parametrize("token", BINARY_TOKENS)
    def test_execute_provider_binary_missing_returns_warning(
        self,
        token: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        workspace_paths_factory: WorkspacePathsFactory,
    ) -> None:
        binary = PROVIDERS[token].binary
        monkeypatch.setattr("dovo.core.diagnostics.checks.env_binaries.shutil.which", _which_only("git"))
        context = _context_with_provider(tmp_path, token, workspace_paths_factory)

        result = EnvBinariesCheck().execute(context)

        assert result.check_id == "env.binaries"
        assert result.category == CheckCategory.ENVIRONMENT
        assert result.status == CheckStatus.WARNING
        assert result.error_code == "DOCTOR_BINARY_MISSING"
        assert result.details == {"missing_binaries": [binary]}
        assert result.warnings == [f"1 required binary(s) not found on PATH: {binary}."]

    def test_execute_provider_without_binary_requires_only_git(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        monkeypatch.setitem(PROVIDERS, DEFAULT_TOKEN, dataclasses.replace(PROVIDERS[DEFAULT_TOKEN], binary=None))
        monkeypatch.setattr("dovo.core.diagnostics.checks.env_binaries.shutil.which", lambda _name: "/usr/bin/tool")
        context = _context_with_provider(tmp_path, DEFAULT_TOKEN, workspace_paths_factory)

        result = EnvBinariesCheck().execute(context)

        assert result.status == CheckStatus.OK
        assert result.details == {"verified_binaries": ["git"]}

    def test_execute_provider_absent_from_registry_requires_only_git(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        monkeypatch.delitem(PROVIDERS, DEFAULT_TOKEN)
        monkeypatch.setattr("dovo.core.diagnostics.checks.env_binaries.shutil.which", lambda _name: "/usr/bin/tool")
        context = _context_with_provider(tmp_path, DEFAULT_TOKEN, workspace_paths_factory)

        result = EnvBinariesCheck().execute(context)

        assert result.status == CheckStatus.OK
        assert result.details == {"verified_binaries": ["git"]}
