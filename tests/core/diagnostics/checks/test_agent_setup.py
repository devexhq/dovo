"""Unit tests for dovo.core.diagnostics.checks.agent_setup."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.common.filesystem import WorkspacePaths
from dovo.core.config.models import AgentConfig, DovoConfig, ProjectConfig
from dovo.core.diagnostics.checks.agent_setup import PROVIDER_CREDENTIAL_RESOLVERS, AgentSetupCheck
from dovo.core.diagnostics.models import CheckCategory, CheckStatus, DiagnosticsContext

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]


def _context_with_agent(
    cwd: Path, agent: AgentConfig, workspace_paths_factory: WorkspacePathsFactory
) -> DiagnosticsContext:
    config = DovoConfig(version=1, project=ProjectConfig(name="demo"), agent=agent)
    return DiagnosticsContext(cwd=cwd, config=config, paths=workspace_paths_factory(cwd, None))


class AgentSetupCheckTests:
    """Unit tests for AgentSetupCheck diagnostic outcomes."""

    def test_execute_config_none_defaults_to_copilot_provider_no_model(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] AgentSetupCheck.execute: context.config=None -> AgentConfig() (provider 'copilot', model None) with the copilot resolver returning a token -> WARNING, error_code 'DOCTOR_AGENT_NO_MODEL', details {'provider': 'copilot'}."""
        monkeypatch.setitem(
            PROVIDER_CREDENTIAL_RESOLVERS, "copilot", (lambda: "fake-token", "GH_TOKEN or GITHUB_TOKEN")
        )
        check = AgentSetupCheck()
        context = DiagnosticsContext(cwd=tmp_path, paths=workspace_paths_factory(tmp_path, None))

        result = check.execute(context)

        assert result.check_id == "agent.setup"
        assert result.category == CheckCategory.AGENT
        assert result.status == CheckStatus.WARNING
        assert result.error_code == "DOCTOR_AGENT_NO_MODEL"
        assert result.details == {"provider": "copilot"}
        assert "Agent provider 'copilot' has no model configured." in result.message
        assert result.warnings == [result.message]

    def test_execute_missing_credential_returns_failed_key_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] AgentSetupCheck.execute: copilot resolver entry monkeypatched to return None -> FAILED, error_code='DOCTOR_AGENT_KEY_MISSING', details={'provider': 'copilot', 'missing_env_var': 'GH_TOKEN or GITHUB_TOKEN'}."""
        expected_env = "GH_TOKEN or GITHUB_TOKEN"
        monkeypatch.setitem(PROVIDER_CREDENTIAL_RESOLVERS, "copilot", (lambda: None, expected_env))
        check = AgentSetupCheck()
        context = _context_with_agent(
            tmp_path, AgentConfig(provider="copilot", model="some-model"), workspace_paths_factory
        )

        result = check.execute(context)

        message = f"Agent provider 'copilot' is missing required credential '{expected_env}'."
        assert result.check_id == "agent.setup"
        assert result.category == CheckCategory.AGENT
        assert result.status == CheckStatus.FAILED
        assert result.error_code == "DOCTOR_AGENT_KEY_MISSING"
        assert result.details == {"provider": "copilot", "missing_env_var": expected_env}
        assert result.errors == [message]

    def test_execute_credential_present_and_model_set_returns_ok(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] AgentSetupCheck.execute: copilot resolver entry monkeypatched to return 'fake-key', agent.model='test-model' -> OK, error_code=None, details={'provider': 'copilot', 'model': 'test-model'}."""
        _, expected_env = PROVIDER_CREDENTIAL_RESOLVERS["copilot"]
        monkeypatch.setitem(PROVIDER_CREDENTIAL_RESOLVERS, "copilot", (lambda: "fake-key", expected_env))
        check = AgentSetupCheck()
        context = _context_with_agent(
            tmp_path, AgentConfig(provider="copilot", model="test-model"), workspace_paths_factory
        )

        result = check.execute(context)

        assert result.check_id == "agent.setup"
        assert result.category == CheckCategory.AGENT
        assert result.status == CheckStatus.OK
        assert result.error_code is None
        assert result.details == {"provider": "copilot", "model": "test-model"}
