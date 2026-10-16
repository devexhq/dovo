"""Unit tests for dovo.core.diagnostics.checks.agent_setup."""

from __future__ import annotations

import dataclasses
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

import pytest

from dovo.common.filesystem import WorkspacePaths
from dovo.core.agents.registry import PROVIDERS
from dovo.core.config.models import DEFAULT_AGENT, AgentProvider, AgentSettings, DovoConfig, ProjectConfig
from dovo.core.diagnostics.checks.agent_setup import AgentSetupCheck
from dovo.core.diagnostics.models import CheckCategory, CheckStatus, DiagnosticsContext
from tests.harness import patch_claude_probe

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]

CREDENTIALED_TOKENS = sorted(token for token, spec in PROVIDERS.items() if spec.credential_envs)
DEFAULT_TOKEN = DEFAULT_AGENT
_EXPECTED_AUTH_DETAILS = {"claude": {"auth": "ANTHROPIC_API_KEY"}}
_CLAUDE_AUTH_FIX = (
    "run `claude auth login`, export CLAUDE_CODE_OAUTH_TOKEN=... (from `claude setup-token`) "
    "or export ANTHROPIC_API_KEY=..."
)


def _context_with_agent(
    cwd: Path, provider: str, model: str, workspace_paths_factory: WorkspacePathsFactory
) -> DiagnosticsContext:
    token = cast(AgentProvider, provider)
    config = DovoConfig(
        version=1,
        project=ProjectConfig(name="demo"),
        default_agent=token,
        agents={token: AgentSettings(model=model)},
    )
    return DiagnosticsContext(cwd=cwd, config=config, paths=workspace_paths_factory(cwd, None))


def _stub_logged_in(monkeypatch: pytest.MonkeyPatch, *, logged_in: bool) -> None:
    """Make the Claude login probe report the given login state."""

    def _run(argv: Sequence[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        stdout = b'{"loggedIn": true}' if logged_in else b'{"loggedIn": false}'

        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr=b"")

    patch_claude_probe(monkeypatch, _run)


def _clear_credential_envs(monkeypatch: pytest.MonkeyPatch, token: str) -> None:
    for env_name in PROVIDERS[token].credential_envs:
        monkeypatch.delenv(env_name, raising=False)


class AgentSetupCheckTests:
    """Unit tests for AgentSetupCheck diagnostic outcomes."""

    def test_execute_config_none_defaults_to_default_provider_no_model(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
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
        _clear_credential_envs(monkeypatch, token)
        joined = " or ".join(PROVIDERS[token].credential_envs)
        context = _context_with_agent(tmp_path, token, "some-model", workspace_paths_factory)

        result = AgentSetupCheck().execute(context)

        assert result.check_id == "agent.setup"
        assert result.category == CheckCategory.AGENT
        assert result.status == CheckStatus.FAILED
        assert result.error_code == "DOCTOR_AGENT_KEY_MISSING"
        assert result.details == {
            "provider": token,
            "missing_env_var": joined,
            "credential_envs": list(PROVIDERS[token].credential_envs),
        }
        assert result.errors == [f"Agent provider '{token}' is missing required credential '{joined}'."]

    @pytest.mark.parametrize("token", CREDENTIALED_TOKENS)
    def test_execute_one_credential_set_returns_ok(
        self,
        token: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        workspace_paths_factory: WorkspacePathsFactory,
    ) -> None:
        _clear_credential_envs(monkeypatch, token)
        monkeypatch.setenv(PROVIDERS[token].credential_envs[-1], "x")
        context = _context_with_agent(tmp_path, token, "test-model", workspace_paths_factory)

        result = AgentSetupCheck().execute(context)

        assert result.check_id == "agent.setup"
        assert result.category == CheckCategory.AGENT
        assert result.status == CheckStatus.OK
        assert result.error_code is None
        assert result.details == {"provider": token, "model": "test-model", **_EXPECTED_AUTH_DETAILS.get(token, {})}

    def test_execute_blank_credential_returns_failed_key_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        for env_name in PROVIDERS[DEFAULT_TOKEN].credential_envs:
            monkeypatch.setenv(env_name, "   ")
        context = _context_with_agent(tmp_path, DEFAULT_TOKEN, "some-model", workspace_paths_factory)

        result = AgentSetupCheck().execute(context)

        assert result.status == CheckStatus.FAILED
        assert result.error_code == "DOCTOR_AGENT_KEY_MISSING"

    def test_execute_empty_credential_envs_skips_credential_step(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        _clear_credential_envs(monkeypatch, DEFAULT_TOKEN)
        monkeypatch.setitem(PROVIDERS, DEFAULT_TOKEN, dataclasses.replace(PROVIDERS[DEFAULT_TOKEN], credential_envs=()))
        context = _context_with_agent(tmp_path, DEFAULT_TOKEN, "some-model", workspace_paths_factory)

        result = AgentSetupCheck().execute(context)

        assert result.status == CheckStatus.OK
        assert result.error_code is None
        assert result.details == {"provider": DEFAULT_TOKEN, "model": "some-model"}

    def test_execute_provider_absent_from_registry_skips_credential_step(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        _clear_credential_envs(monkeypatch, DEFAULT_TOKEN)
        monkeypatch.delitem(PROVIDERS, DEFAULT_TOKEN)
        context = _context_with_agent(tmp_path, DEFAULT_TOKEN, "some-model", workspace_paths_factory)

        result = AgentSetupCheck().execute(context)

        assert result.status == CheckStatus.OK
        assert result.error_code is None

    def test_execute_multiple_credential_envs_join_with_or(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        monkeypatch.delenv("ALPHA_KEY", raising=False)
        monkeypatch.delenv("BETA_KEY", raising=False)
        spec = dataclasses.replace(PROVIDERS[DEFAULT_TOKEN], credential_envs=("ALPHA_KEY", "BETA_KEY"))
        monkeypatch.setitem(PROVIDERS, DEFAULT_TOKEN, spec)
        context = _context_with_agent(tmp_path, DEFAULT_TOKEN, "some-model", workspace_paths_factory)

        result = AgentSetupCheck().execute(context)

        assert result.status == CheckStatus.FAILED
        assert result.details["missing_env_var"] == "ALPHA_KEY or BETA_KEY"

    def test_execute_probe_true_with_no_credential_env_is_not_failed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """Return OK with auth 'cli session' when the probe reports loggedIn true and no credential env is set."""
        _clear_credential_envs(monkeypatch, "claude")
        _stub_logged_in(monkeypatch, logged_in=True)
        context = _context_with_agent(tmp_path, "claude", "m", workspace_paths_factory)

        result = AgentSetupCheck().execute(context)

        assert result.status == CheckStatus.OK
        assert result.details == {"provider": "claude", "model": "m", "auth": "cli session"}

    @pytest.mark.parametrize(
        ("env", "logged_in", "label"),
        [
            pytest.param(
                {"CLAUDE_CODE_OAUTH_TOKEN": "t", "ANTHROPIC_API_KEY": "k"}, True, "CLAUDE_CODE_OAUTH_TOKEN", id="token"
            ),
            pytest.param({"ANTHROPIC_API_KEY": "k"}, True, "cli session", id="login-with-key"),
            pytest.param({"ANTHROPIC_API_KEY": "k"}, False, "ANTHROPIC_API_KEY", id="key-alone"),
        ],
    )
    def test_execute_names_the_mode_in_use(
        self,
        env: dict[str, str],
        logged_in: bool,
        label: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        workspace_paths_factory: WorkspacePathsFactory,
    ) -> None:
        """Return details['auth'] naming the token, the CLI session or the key, in that precedence."""
        _clear_credential_envs(monkeypatch, "claude")
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        _stub_logged_in(monkeypatch, logged_in=logged_in)
        context = _context_with_agent(tmp_path, "claude", "m", workspace_paths_factory)

        result = AgentSetupCheck().execute(context)

        assert (result.status, result.details["auth"]) == (CheckStatus.OK, label)
        assert result.message == f"Agent provider 'claude' is configured with model 'm' (authentication: {label})."

    def test_execute_nothing_to_authenticate_returns_failed_with_fix_text(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """Return FAILED DOCTOR_AGENT_KEY_MISSING with fixes == [the Claude authentication fix] for a logged-out CLI."""
        _clear_credential_envs(monkeypatch, "claude")
        _stub_logged_in(monkeypatch, logged_in=False)
        context = _context_with_agent(tmp_path, "claude", "m", workspace_paths_factory)

        result = AgentSetupCheck().execute(context)

        assert (result.status, result.error_code) == (CheckStatus.FAILED, "DOCTOR_AGENT_KEY_MISSING")
        assert result.fixes == [_CLAUDE_AUTH_FIX]

    def test_execute_provider_without_probe_is_unchanged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """Return FAILED with empty fixes and no auth detail for a credentialed spec without auth_label or auth_fix."""
        _clear_credential_envs(monkeypatch, DEFAULT_TOKEN)
        spec = dataclasses.replace(PROVIDERS[DEFAULT_TOKEN], auth_label=None, auth_fix=None)
        monkeypatch.setitem(PROVIDERS, DEFAULT_TOKEN, spec)
        context = _context_with_agent(tmp_path, DEFAULT_TOKEN, "m", workspace_paths_factory)

        result = AgentSetupCheck().execute(context)

        assert (result.status, result.fixes) == (CheckStatus.FAILED, [])
        assert "auth" not in result.details

    def test_execute_reads_default_agent_block_model(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """Report claude's model "claude-model" for default_agent "claude" while the copilot block names another."""
        _clear_credential_envs(monkeypatch, "claude")
        _stub_logged_in(monkeypatch, logged_in=True)
        config = DovoConfig(
            version=1,
            project=ProjectConfig(name="demo"),
            default_agent="claude",
            agents={"copilot": AgentSettings(model="copilot-model"), "claude": AgentSettings(model="claude-model")},
        )
        context = DiagnosticsContext(cwd=tmp_path, config=config, paths=workspace_paths_factory(tmp_path, None))

        result = AgentSetupCheck().execute(context)

        assert result.status == CheckStatus.OK
        assert result.details == {"provider": "claude", "model": "claude-model", "auth": "cli session"}
