from __future__ import annotations

import dataclasses
import json
from importlib.resources import files
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from dovo.common.filesystem import Filesystem
from dovo.common.schema_validation import CONFIG_VALIDATOR
from dovo.core.agents.registry import PROVIDERS
from dovo.core.config.generator import build_default_config
from dovo.core.config.models import AgentProvider, DovoConfig
from dovo.core.config.validate import (
    ConfigValidationStatus,
    validate_config_result,
)


class ConfigSemanticValidationTests:
    """Unit tests verifying semantic validation rules and warning generation."""

    def test_validate_config_warns_when_provider_requires_model_and_model_missing(
        self, tmp_path: Path, model_required_provider: None
    ) -> None:
        payload = build_default_config("demo")
        payload["agent"]["model"] = None
        config_path = tmp_path / "config.json"
        Filesystem.atomic_write_json(config_path, payload)

        result = validate_config_result(config_path=config_path)

        assert result.status == ConfigValidationStatus.VALID
        assert result.errors == []
        assert result.warnings == [
            "agent.provider 'copilot' requires agent.model but it is missing (CONFIG_WARN_AGENT_MODEL_MISSING)."
        ]
        assert result.fixes == ["Set agent.model"]

    def test_validate_config_default_config_has_no_model_warning(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.json"
        Filesystem.atomic_write_json(config_path, build_default_config("demo"))

        result = validate_config_result(config_path=config_path)

        assert result.status == ConfigValidationStatus.VALID
        assert result.warnings == []
        assert result.fixes == []

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

    def test_schema_valid_unregistered_provider_is_semantically_invalid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] validate_config_result: with 'copilot' removed from PROVIDERS and a fake token registered, agent.provider 'copilot' returns INVALID, ok False, the AGENT_PROVIDER_UNSUPPORTED error naming the fake token, and no warnings."""
        monkeypatch.setitem(PROVIDERS, "fake", dataclasses.replace(PROVIDERS["copilot"], token="fake"))
        monkeypatch.delitem(PROVIDERS, "copilot")

        result = validate_config_result(self._write_config(tmp_path, "copilot"))

        assert result.status == ConfigValidationStatus.INVALID
        assert result.ok is False
        assert result.errors == ["Unsupported agent provider 'copilot' (AGENT_PROVIDER_UNSUPPORTED). Supported: fake."]
        assert result.warnings == []

    def test_schema_invalid_token_keeps_structural_failure(self, tmp_path: Path) -> None:
        """[tier-1/unit] validate_config_result: agent.provider 'bogus' returns INVALID with CONFIG_SCHEMA_INVALID first and no AGENT_PROVIDER_UNSUPPORTED error."""
        result = validate_config_result(self._write_config(tmp_path, "bogus"))

        assert result.status == ConfigValidationStatus.INVALID
        assert "CONFIG_SCHEMA_INVALID" in result.errors[0]
        assert not any("AGENT_PROVIDER_UNSUPPORTED" in error for error in result.errors)


class ProviderVocabularyAgreementTests:
    def test_registry_model_and_schema_agree_on_provider_set(self) -> None:
        """[tier-1/unit] PROVIDERS vs AgentProvider vs schemas/v1/config.json agent.provider enum: all three token sets equal {'copilot'} and the schema default is 'copilot'."""
        schema = json.loads(files("dovo.schemas.v1").joinpath("config.json").read_text(encoding="utf-8"))
        provider_schema = schema["properties"]["agent"]["properties"]["provider"]

        assert set(PROVIDERS) == set(get_args(AgentProvider)) == set(provider_schema["enum"]) == {"copilot"}
        assert provider_schema["default"] == "copilot"


class AgentConfigEnvTests:
    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({"env_mode": "bogus"}, id="bad-mode"),
            pytest.param({"env_passthrough": ["A*B"]}, id="bad-entry"),
            pytest.param({"env_passthrough": "DOCKER_*"}, id="not-a-list"),
        ],
    )
    def test_invalid_env_keys_fail_schema_and_model_validation(self, payload: dict[str, object]) -> None:
        """[tier-1/unit] CONFIG_VALIDATOR and DovoConfig: each invalid agent env payload is rejected by both."""
        document = build_default_config("demo")
        document["agent"].update(payload)

        assert not CONFIG_VALIDATOR.validate(document).ok
        with pytest.raises(ValidationError):
            DovoConfig.model_validate(document)
