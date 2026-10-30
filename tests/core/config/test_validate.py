from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import Filesystem
from dovo.common.models import Remediation
from dovo.common.schema_validation import CONFIG_VALIDATOR
from dovo.core.agents.registry import PROVIDERS
from dovo.core.config.generator import build_default_config
from dovo.core.config.loader import ConfigLoadResult, ConfigLoadStatus, load_config_at
from dovo.core.config.models import DovoConfig
from dovo.core.config.validate import (
    ConfigValidationStatus,
    validate_config_result,
)
from tests.harness.diagnostic_log import diagnostic_record


def _deny_read(config_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Write a valid config, then make reading it raise PermissionError."""
    Filesystem.atomic_write_json(config_path, build_default_config("demo"))
    real_read_text = Path.read_text

    def _denied_read(
        self: Path, encoding: str | None = None, errors: str | None = None, newline: str | None = None
    ) -> str:
        if self == config_path:
            raise PermissionError("denied")
        return real_read_text(self, encoding=encoding, errors=errors, newline=newline)

    monkeypatch.setattr(Path, "read_text", _denied_read)


VALIDATE_LOAD_FAILURE_CASES = [
    pytest.param(lambda path, _: None, ConfigValidationStatus.NOT_FOUND, id="not_found"),
    pytest.param(lambda path, _: path.mkdir(), ConfigValidationStatus.PATH_IS_DIRECTORY, id="path_is_directory"),
    pytest.param(
        lambda path, _: path.write_text("{not valid json", encoding="utf-8"),
        ConfigValidationStatus.MALFORMED_JSON,
        id="malformed_json",
    ),
    pytest.param(
        lambda path, _: path.write_text("[]", encoding="utf-8"),
        ConfigValidationStatus.ROOT_NOT_OBJECT,
        id="root_not_object",
    ),
    pytest.param(
        lambda path, _: Filesystem.atomic_write_json(path, {"version": 1}),
        ConfigValidationStatus.INVALID,
        id="schema_invalid",
    ),
    pytest.param(_deny_read, ConfigValidationStatus.UNREADABLE, id="unreadable"),
]


class ConfigSemanticValidationTests:
    """Unit tests verifying semantic validation rules and warning generation."""

    def test_validate_config_warns_when_provider_requires_model_and_model_missing(
        self, tmp_path: Path, model_required_provider: None
    ) -> None:
        payload = build_default_config("demo")
        payload["agents"] = {"copilot": {}}
        config_path = tmp_path / "config.json"
        Filesystem.atomic_write_json(config_path, payload)

        result = validate_config_result(config_path=config_path)

        assert result.status == ConfigValidationStatus.VALID
        assert result.errors == []
        assert result.warnings == [
            "default_agent 'copilot' requires agents.copilot.model but it is missing (CONFIG_WARN_AGENT_MODEL_MISSING)."
        ]
        assert result.remediations == [Remediation(message="Set 'agents.copilot.model'")]

    def test_validate_does_not_warn_missing_model_for_non_default_provider(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Emit no warning when only claude, which requires a model, lacks one while default_agent is copilot."""
        monkeypatch.setitem(PROVIDERS, "claude", dataclasses.replace(PROVIDERS["claude"], requires_model=True))
        payload = build_default_config("demo")
        payload["agents"] = {"copilot": {"model": "m"}, "claude": {}}
        config_path = tmp_path / "config.json"
        Filesystem.atomic_write_json(config_path, payload)

        result = validate_config_result(config_path=config_path)

        assert result.status == ConfigValidationStatus.VALID
        assert result.warnings == []

    def test_validate_config_default_config_has_no_model_warning(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.json"
        Filesystem.atomic_write_json(config_path, build_default_config("demo"))

        result = validate_config_result(config_path=config_path)

        assert result.status == ConfigValidationStatus.VALID
        assert result.error_code is None
        assert result.warnings == []
        assert result.remediations == []

    @pytest.mark.parametrize(
        "provider", [pytest.param("copilot", id="default-agent"), pytest.param("claude", id="non-default-agent")]
    )
    def test_validate_warns_for_every_agents_block_with_non_absolute_endpoint(
        self, tmp_path: Path, provider: str
    ) -> None:
        """Warn for the block's ftp://x endpoint while default_agent stays copilot."""
        payload = build_default_config("demo")
        payload["agents"] = {provider: {"endpoint": "ftp://x"}}
        config_path = tmp_path / "config.json"
        Filesystem.atomic_write_json(config_path, payload)

        result = validate_config_result(config_path=config_path)

        assert result.status == ConfigValidationStatus.VALID
        assert result.config_path == config_path
        assert result.raw == payload
        assert result.config == DovoConfig.model_validate(payload)
        assert result.errors == []
        assert result.warnings == [
            f"agents.{provider}.endpoint is not an absolute http(s) URL: 'ftp://x' (CONFIG_WARN_AGENT_ENDPOINT)."
        ]
        assert result.remediations == [
            Remediation(message=f"Set 'agents.{provider}.endpoint' to an absolute http:// or https:// URL, or null")
        ]

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
        assert result.remediations == [Remediation(message="Lower 'worktree.max_active_worktrees' to 10 or fewer")]

    def test_validate_schema_violation_carries_load_raw_errors_and_remediations(self, tmp_path: Path) -> None:
        payload = {"version": 1}
        config_path = tmp_path / "config.json"
        Filesystem.atomic_write_json(config_path, payload)

        result = validate_config_result(config_path=config_path)

        assert result.status == ConfigValidationStatus.INVALID
        assert result.config_path == config_path
        assert result.raw == payload
        assert result.config is None
        assert result.errors == ["Config schema validation failed:\n- (root): 'project' is a required property"]
        assert result.warnings == []
        assert result.remediations == [
            Remediation(message="Run 'dovo config validate' for details", command="dovo config validate"),
            Remediation(
                message="Run 'dovo init --repair' to insert missing keys without overwriting values",
                command="dovo init --repair",
            ),
        ]

    @pytest.mark.parametrize(("write", "status"), VALIDATE_LOAD_FAILURE_CASES)
    def test_validate_copies_load_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        frozen_diagnostic_clock: Path,
        write: Callable[[Path, pytest.MonkeyPatch], object],
        status: ConfigValidationStatus,
    ) -> None:
        """Copy the load's error_code, errors, remediations and diagnostics, with the row's status."""
        config_path = tmp_path / "config.json"
        write(config_path, monkeypatch)

        result = validate_config_result(config_path)

        loaded = load_config_at(config_path, bypass_cache=True)
        assert result.status == status
        assert loaded.error_code is not None
        assert result.error_code == loaded.error_code
        assert result.errors == loaded.errors
        assert result.remediations == loaded.remediations
        unreadable = loaded.status == ConfigLoadStatus.UNREADABLE
        assert result.diagnostics == ([diagnostic_record(frozen_diagnostic_clock)] if unreadable else [])

    def test_validate_raises_when_ok_load_has_no_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Raise RuntimeError naming the path when an OK load carries no parsed config."""
        config_path = tmp_path / "config.json"
        monkeypatch.setattr(
            "dovo.core.config.loader.load_config_at",
            lambda path: ConfigLoadResult(status=ConfigLoadStatus.OK, config_path=path),
        )

        with pytest.raises(RuntimeError) as exc_info:
            validate_config_result(config_path)

        assert str(exc_info.value) == f"Configuration loaded from '{config_path}' but the parsed config is missing."


class ConfigProviderRegistryValidationTests:
    @staticmethod
    def _write_config(tmp_path: Path, provider: str) -> Path:
        payload = build_default_config("demo")
        payload["default_agent"] = provider
        config_path = tmp_path / "config.json"
        Filesystem.atomic_write_json(config_path, payload)
        return config_path

    def test_validate_rejects_unregistered_default_agent_naming_default_agent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(PROVIDERS, "fake", dataclasses.replace(PROVIDERS["copilot"], token="fake"))
        monkeypatch.delitem(PROVIDERS, "copilot")

        result = validate_config_result(self._write_config(tmp_path, "copilot"))

        assert result.status == ConfigValidationStatus.INVALID
        assert result.ok is False
        assert result.error_code == ErrorCode.AGENT_PROVIDER_UNSUPPORTED
        assert result.errors == ["default_agent: Unsupported agent provider 'copilot'. Supported: claude, fake."]
        assert result.remediations == [
            Remediation(message="Set 'default_agent' to one of the supported providers named in the error")
        ]
        assert result.warnings == []

    @pytest.mark.parametrize("token", list(PROVIDERS))
    def test_registered_provider_with_model_is_valid(self, token: str, tmp_path: Path) -> None:
        config_path = self._write_config(tmp_path, token)
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        payload["agents"] = {token: {"model": "m"}}
        Filesystem.atomic_write_json(config_path, payload)

        result = validate_config_result(config_path)

        assert result.status == ConfigValidationStatus.VALID
        assert result.ok is True
        assert result.errors == []
        assert result.warnings == []

    def test_schema_invalid_token_keeps_structural_failure(self, tmp_path: Path) -> None:
        result = validate_config_result(self._write_config(tmp_path, "bogus"))

        assert result.status == ConfigValidationStatus.INVALID
        assert result.errors[0].startswith("Config schema validation failed:")
        assert not any("Unsupported agent provider" in error for error in result.errors)


class AgentSettingsEnvTests:
    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({"env_mode": "bogus"}, id="bad-mode"),
            pytest.param({"env_passthrough": ["A*B"]}, id="bad-entry"),
            pytest.param({"env_passthrough": "DOCKER_*"}, id="not-a-list"),
        ],
    )
    def test_invalid_env_keys_fail_schema_and_model_validation(self, payload: dict[str, object]) -> None:
        document = build_default_config("demo")
        document["agents"] = {"copilot": payload}

        assert CONFIG_VALIDATOR.validate(document)
        with pytest.raises(ValidationError):
            DovoConfig.model_validate(document)
