"""Unit tests for dovo.core.diagnostics.checks.agent_setup."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from dovo.common.filesystem import WorkspacePaths
from dovo.core.agents.registry import PROVIDERS
from dovo.core.config.models import AgentConfig, AgentProvider, DovoConfig, ProjectConfig
from dovo.core.diagnostics.checks.agent_setup import AgentSetupCheck
from dovo.core.diagnostics.models import CheckCategory, CheckStatus, DiagnosticsContext

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]

CREDENTIALED_TOKENS = sorted(token for token, spec in PROVIDERS.items() if spec.credential_envs)
DEFAULT_TOKEN = AgentConfig().provider


def _context_with_agent(
    cwd: Path, agent: AgentConfig, workspace_paths_factory: WorkspacePathsFactory
) -> DiagnosticsContext:
    config = DovoConfig(version=1, project=ProjectConfig(name="demo"), agent=agent)
    return DiagnosticsContext(cwd=cwd, config=config, paths=workspace_paths_factory(cwd, None))


def _clear_credential_envs(monkeypatch: pytest.MonkeyPatch, token: str) -> None:
    for env_name in PROVIDERS[token].credential_envs:
        monkeypatch.delenv(env_name, raising=False)


class AgentSetupCheckTests:
    """Unit tests for AgentSetupCheck diagnostic outcomes."""

    def test_execute_config_none_defaults_to_default_provider_no_model(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] AgentSetupCheck.execute: context.config=None, default provider's credentials set -> WARNING, error_code 'DOCTOR_AGENT_NO_MODEL', details {'provider': AgentConfig().provider}."""
        _clear_credential_envs(monkeypatch, DEFAULT_TOKEN)
        monkeypatch.setenv(PROVIDERS[DEFAULT_TOKEN].credential_envs[0], "fake-token")
        check = AgentSetupCheck()
        context = DiagnosticsContext(cwd=tmp_path, paths=workspace_paths_factory(tmp_path, None))

        result = check.execute(context)

        assert result.check_id == "agent.setup"
        assert result.category == CheckCategory.AGENT
        assert result.status == CheckStatus.WARNING
        assert result.error_code == "DOCTOR_AGENT_NO_MODEL"
        assert result.details == {"provider": DEFAULT_TOKEN}
        assert result.warnings == [f"Agent provider '{DEFAULT_TOKEN}' has no model configured."]

    @pytest.mark.parametrize("token", CREDENTIALED_TOKENS)
    def test_execute_credentials_unset_returns_failed_key_missing(
        self,
        token: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        workspace_paths_factory: WorkspacePathsFactory,
    ) -> None:
        """[tier-1/unit] AgentSetupCheck.execute: provider=token with every PROVIDERS[token].credential_envs unset (when non-empty) -> FAILED, error_code 'DOCTOR_AGENT_KEY_MISSING', details {'provider': token, 'missing_env_var': ' or '.join(credential_envs)}, errors [f"Agent provider '{token}' is missing required credential '{joined}'."]."""
        _clear_credential_envs(monkeypatch, token)
        joined = " or ".join(PROVIDERS[token].credential_envs)
        agent = AgentConfig(provider=cast(AgentProvider, token), model="some-model")
        context = _context_with_agent(tmp_path, agent, workspace_paths_factory)

        result = AgentSetupCheck().execute(context)

        assert result.check_id == "agent.setup"
        assert result.category == CheckCategory.AGENT
        assert result.status == CheckStatus.FAILED
        assert result.error_code == "DOCTOR_AGENT_KEY_MISSING"
        assert result.details == {"provider": token, "missing_env_var": joined}
        assert result.errors == [f"Agent provider '{token}' is missing required credential '{joined}'."]

    @pytest.mark.parametrize("token", CREDENTIALED_TOKENS)
    def test_execute_one_credential_set_returns_ok(
        self,
        token: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        workspace_paths_factory: WorkspacePathsFactory,
    ) -> None:
        """[tier-1/unit] AgentSetupCheck.execute: provider=token with only the last PROVIDERS[token].credential_envs entry set to 'x' and model 'test-model' -> OK, error_code None, details {'provider': token, 'model': 'test-model'}."""
        _clear_credential_envs(monkeypatch, token)
        monkeypatch.setenv(PROVIDERS[token].credential_envs[-1], "x")
        agent = AgentConfig(provider=cast(AgentProvider, token), model="test-model")
        context = _context_with_agent(tmp_path, agent, workspace_paths_factory)

        result = AgentSetupCheck().execute(context)

        assert result.check_id == "agent.setup"
        assert result.category == CheckCategory.AGENT
        assert result.status == CheckStatus.OK
        assert result.error_code is None
        assert result.details == {"provider": token, "model": "test-model"}

    def test_execute_blank_credential_returns_failed_key_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] AgentSetupCheck.execute: default provider with every credential env set to '   ' -> FAILED, error_code 'DOCTOR_AGENT_KEY_MISSING'."""
        for env_name in PROVIDERS[DEFAULT_TOKEN].credential_envs:
            monkeypatch.setenv(env_name, "   ")
        context = _context_with_agent(tmp_path, AgentConfig(model="some-model"), workspace_paths_factory)

        result = AgentSetupCheck().execute(context)

        assert result.status == CheckStatus.FAILED
        assert result.error_code == "DOCTOR_AGENT_KEY_MISSING"

    def test_execute_empty_credential_envs_skips_credential_step(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] AgentSetupCheck.execute: PROVIDERS[default] replaced via dataclasses.replace(credential_envs=()) and model set -> OK, error_code None, details {'provider': token, 'model': model}."""
        _clear_credential_envs(monkeypatch, DEFAULT_TOKEN)
        monkeypatch.setitem(PROVIDERS, DEFAULT_TOKEN, dataclasses.replace(PROVIDERS[DEFAULT_TOKEN], credential_envs=()))
        context = _context_with_agent(tmp_path, AgentConfig(model="some-model"), workspace_paths_factory)

        result = AgentSetupCheck().execute(context)

        assert result.status == CheckStatus.OK
        assert result.error_code is None
        assert result.details == {"provider": DEFAULT_TOKEN, "model": "some-model"}

    def test_execute_provider_absent_from_registry_skips_credential_step(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] AgentSetupCheck.execute: default provider token deleted from PROVIDERS via monkeypatch.delitem, model set -> OK, error_code None."""
        _clear_credential_envs(monkeypatch, DEFAULT_TOKEN)
        monkeypatch.delitem(PROVIDERS, DEFAULT_TOKEN)
        context = _context_with_agent(tmp_path, AgentConfig(model="some-model"), workspace_paths_factory)

        result = AgentSetupCheck().execute(context)

        assert result.status == CheckStatus.OK
        assert result.error_code is None

    def test_execute_multiple_credential_envs_join_with_or(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] AgentSetupCheck.execute: PROVIDERS[default] replaced with credential_envs ('ALPHA_KEY', 'BETA_KEY'), both unset -> FAILED, details['missing_env_var'] == 'ALPHA_KEY or BETA_KEY'."""
        monkeypatch.delenv("ALPHA_KEY", raising=False)
        monkeypatch.delenv("BETA_KEY", raising=False)
        spec = dataclasses.replace(PROVIDERS[DEFAULT_TOKEN], credential_envs=("ALPHA_KEY", "BETA_KEY"))
        monkeypatch.setitem(PROVIDERS, DEFAULT_TOKEN, spec)
        context = _context_with_agent(tmp_path, AgentConfig(model="some-model"), workspace_paths_factory)

        result = AgentSetupCheck().execute(context)

        assert result.status == CheckStatus.FAILED
        assert result.details["missing_env_var"] == "ALPHA_KEY or BETA_KEY"
