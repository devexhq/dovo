"""Unit tests for dovo.core.doctor.checks.agent_setup."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.common.filesystem import WorkspacePaths
from dovo.core.config.models import AgentConfig, AgentProvider, DovoConfig, ProjectConfig
from dovo.core.doctor.checks.agent_setup import PROVIDER_CREDENTIAL_RESOLVERS, AgentSetupCheck
from dovo.core.doctor.models import CheckCategory, CheckStatus, DoctorContext

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]


def _context_with_agent(cwd: Path, agent: AgentConfig, workspace_paths_factory: WorkspacePathsFactory) -> DoctorContext:
    config = DovoConfig(version=1, project=ProjectConfig(name="demo"), agent=agent)
    return DoctorContext(cwd=cwd, config=config, paths=workspace_paths_factory(cwd, None))


class AgentSetupCheckTests:
    """Unit tests for AgentSetupCheck diagnostic outcomes."""

    def test_execute_config_none_defaults_to_local_provider_no_model(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] AgentSetupCheck.execute: context.config=None -> AgentConfig() default (provider='local', model=None) -> WARNING, error_code='DOCTOR_AGENT_NO_MODEL', details={'provider': 'local'}."""
        check = AgentSetupCheck()
        context = DoctorContext(cwd=tmp_path, paths=workspace_paths_factory(tmp_path, None))

        result = check.execute(context)

        assert result.check_id == "agent.setup"
        assert result.category == CheckCategory.AGENT
        assert result.status == CheckStatus.WARNING
        assert result.error_code == "DOCTOR_AGENT_NO_MODEL"
        assert result.details == {"provider": "local"}
        assert "Agent provider 'local' has no model configured." in result.message
        assert result.warnings == [result.message]

    def test_execute_local_provider_with_model_returns_ok(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] AgentSetupCheck.execute: agent.provider='local', agent.model='dovo-local-agent' -> OK, error_code=None, details={'provider': 'local', 'model': 'dovo-local-agent'}."""
        check = AgentSetupCheck()
        context = _context_with_agent(
            tmp_path, AgentConfig(provider="local", model="dovo-local-agent"), workspace_paths_factory
        )

        result = check.execute(context)

        assert result.check_id == "agent.setup"
        assert result.category == CheckCategory.AGENT
        assert result.status == CheckStatus.OK
        assert result.error_code is None
        assert result.details == {"provider": "local", "model": "dovo-local-agent"}

    def test_execute_ollama_provider_no_resolver_missing_model_returns_warning(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] AgentSetupCheck.execute: agent.provider='ollama', agent.model=None -> WARNING, error_code='DOCTOR_AGENT_NO_MODEL' (never FAILED, ollama has no credential resolver)."""
        check = AgentSetupCheck()
        context = _context_with_agent(tmp_path, AgentConfig(provider="ollama"), workspace_paths_factory)

        result = check.execute(context)

        assert result.check_id == "agent.setup"
        assert result.category == CheckCategory.AGENT
        assert result.status == CheckStatus.WARNING
        assert result.error_code == "DOCTOR_AGENT_NO_MODEL"
        assert result.details == {"provider": "ollama"}
        assert "Agent provider 'ollama' has no model configured." in result.message
        assert result.warnings == [result.message]

    @pytest.mark.parametrize(
        ("provider", "expected_env"),
        [
            pytest.param("cursor", "CURSOR_API_KEY", id="cursor"),
            pytest.param("gemini", "GEMINI_API_KEY", id="gemini"),
            pytest.param("copilot", "GH_TOKEN or GITHUB_TOKEN", id="copilot"),
        ],
    )
    def test_execute_missing_credential_returns_failed_key_missing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        provider: AgentProvider,
        expected_env: str,
        workspace_paths_factory: WorkspacePathsFactory,
    ) -> None:
        """[tier-1/unit] AgentSetupCheck.execute: provider's resolver entry monkeypatched to return None -> FAILED, error_code='DOCTOR_AGENT_KEY_MISSING', details={'provider': provider, 'missing_env_var': expected_env}."""
        monkeypatch.setitem(PROVIDER_CREDENTIAL_RESOLVERS, provider, (lambda: None, expected_env))
        check = AgentSetupCheck()
        context = _context_with_agent(
            tmp_path, AgentConfig(provider=provider, model="some-model"), workspace_paths_factory
        )

        result = check.execute(context)

        message = f"Agent provider '{provider}' is missing required credential '{expected_env}'."
        assert result.check_id == "agent.setup"
        assert result.category == CheckCategory.AGENT
        assert result.status == CheckStatus.FAILED
        assert result.error_code == "DOCTOR_AGENT_KEY_MISSING"
        assert result.details == {"provider": provider, "missing_env_var": expected_env}
        assert result.errors == [message]

    @pytest.mark.parametrize(
        "provider",
        [
            pytest.param("cursor", id="cursor"),
            pytest.param("gemini", id="gemini"),
            pytest.param("copilot", id="copilot"),
        ],
    )
    def test_execute_credential_present_and_model_set_returns_ok(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        provider: AgentProvider,
        workspace_paths_factory: WorkspacePathsFactory,
    ) -> None:
        """[tier-1/unit] AgentSetupCheck.execute: provider's resolver entry monkeypatched to return 'fake-key', agent.model='claude-fake' -> OK, error_code=None, details={'provider': provider, 'model': 'claude-fake'}."""
        _, expected_env = PROVIDER_CREDENTIAL_RESOLVERS[provider]
        monkeypatch.setitem(PROVIDER_CREDENTIAL_RESOLVERS, provider, (lambda: "fake-key", expected_env))
        check = AgentSetupCheck()
        context = _context_with_agent(
            tmp_path, AgentConfig(provider=provider, model="claude-fake"), workspace_paths_factory
        )

        result = check.execute(context)

        assert result.check_id == "agent.setup"
        assert result.category == CheckCategory.AGENT
        assert result.status == CheckStatus.OK
        assert result.error_code is None
        assert result.details == {"provider": provider, "model": "claude-fake"}
