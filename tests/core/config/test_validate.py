from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from dovo.common.filesystem import Filesystem
from dovo.core.agents.registry import PROVIDERS
from dovo.core.config.generator import build_default_config
from dovo.core.config.models import DovoConfig
from dovo.core.config.validate import (
    ConfigValidationStatus,
    validate_config_result,
)


class ConfigSemanticValidationTests:
    """Unit tests verifying semantic validation rules and warning generation."""

    def test_validate_config_warns_when_non_local_agent_has_no_model(self, tmp_path: Path) -> None:
        payload = build_default_config("demo")
        payload["agent"]["provider"] = "gemini"
        payload["agent"]["model"] = None
        config_path = tmp_path / "config.json"
        Filesystem.atomic_write_json(config_path, payload)

        result = validate_config_result(config_path=config_path)

        assert result.status == ConfigValidationStatus.VALID
        assert result.config_path == config_path
        assert result.raw == payload
        assert result.config == DovoConfig.model_validate(payload)
        assert result.errors == []
        assert result.warnings == [
            "agent.provider is not 'local' but agent.model is missing (CONFIG_WARN_AGENT_MODEL_MISSING)."
        ]
        assert result.fixes == ["Set agent.model or use provider=local"]

    def test_validate_config_warns_when_agent_endpoint_is_not_absolute_url(self, tmp_path: Path) -> None:
        payload = build_default_config("demo")
        payload["agent"]["endpoint"] = "ftp://example.com/api"
        config_path = tmp_path / "config.json"
        Filesystem.atomic_write_json(config_path, payload)

        result = validate_config_result(config_path=config_path)

        assert result.status == ConfigValidationStatus.VALID
        assert result.config_path == config_path
        assert result.raw == payload
        assert result.config == DovoConfig.model_validate(payload)
        assert result.errors == []
        assert result.warnings == [
            "agent.endpoint is not an absolute http(s) URL: 'ftp://example.com/api' (CONFIG_WARN_AGENT_ENDPOINT)."
        ]
        assert result.fixes == ["Set agent.endpoint to an absolute http:// or https:// URL, or null"]

    def test_validate_config_warns_when_worktree_limit_exceeds_threshold(self, tmp_path: Path) -> None:
        payload = build_default_config("demo")
        payload["worktree"]["max_active_worktrees"] = 11
        config_path = tmp_path / "config.json"
        Filesystem.atomic_write_json(config_path, payload)

        result = validate_config_result(config_path=config_path)

        assert result.status == ConfigValidationStatus.VALID
        assert result.config_path == config_path
        assert result.raw == payload
        assert result.config == DovoConfig.model_validate(payload)
        assert result.errors == []
        assert result.warnings == ["worktree.max_active_worktrees (11) exceeds 10 (CONFIG_WARN_WORKTREE_LIMIT)."]
        assert result.fixes == ["Lower worktree.max_active_worktrees to 10 or fewer"]


class ConfigProviderRegistryValidationTests:
    """[tier-1/unit] validate_config_result: agent.provider must be registered in PROVIDERS."""

    @staticmethod
    def _write_config(tmp_path: Path, provider: str) -> Path:
        payload = build_default_config("demo")
        payload["agent"]["provider"] = provider
        config_path = tmp_path / "config.json"
        Filesystem.atomic_write_json(config_path, payload)
        return config_path

    @pytest.mark.parametrize("token", ["openai", "anthropic", "azure_openai", "custom"])
    def test_schema_valid_unregistered_provider_is_semantically_invalid(self, tmp_path: Path, token: str) -> None:
        """[tier-1/unit] validate_config_result: agent.provider '<token>' returns status INVALID, ok False, the AGENT_PROVIDER_UNSUPPORTED error, and no warnings."""
        result = validate_config_result(self._write_config(tmp_path, token))

        assert result.status == ConfigValidationStatus.INVALID
        assert result.ok is False
        assert result.errors == [
            f"Unsupported agent provider '{token}' (AGENT_PROVIDER_UNSUPPORTED). "
            "Supported: copilot, cursor, gemini, local, ollama."
        ]
        assert result.warnings == []

    def test_registering_adapter_makes_its_token_pass(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """[tier-1/unit] validate_config_result: after registering 'custom' in PROVIDERS, agent.provider 'custom' returns status VALID with no errors."""
        monkeypatch.setitem(PROVIDERS, "custom", dataclasses.replace(PROVIDERS["local"], token="custom"))

        result = validate_config_result(self._write_config(tmp_path, "custom"))

        assert result.status == ConfigValidationStatus.VALID
        assert result.errors == []

    def test_schema_invalid_token_keeps_structural_failure(self, tmp_path: Path) -> None:
        """[tier-1/unit] validate_config_result: agent.provider 'bogus' returns INVALID with CONFIG_SCHEMA_INVALID first and no AGENT_PROVIDER_UNSUPPORTED error."""
        result = validate_config_result(self._write_config(tmp_path, "bogus"))

        assert result.status == ConfigValidationStatus.INVALID
        assert "CONFIG_SCHEMA_INVALID" in result.errors[0]
        assert not any("AGENT_PROVIDER_UNSUPPORTED" in error for error in result.errors)
