"""Non-raising config validation engine for structured error/warning lists."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dovo.common.models import BaseResult
from dovo.core.agents.registry import PROVIDERS, unsupported_provider_message
from dovo.core.config.loader import ConfigLoadStatus
from dovo.core.config.models import DovoConfig


class ConfigValidationStatus(StrEnum):
    """Classified outcomes for validating `.dovo/config.json`."""

    VALID = "valid"
    INVALID = "invalid"
    NOT_FOUND = "not_found"
    MALFORMED_JSON = "malformed_json"
    ROOT_NOT_OBJECT = "root_not_object"
    PATH_IS_DIRECTORY = "path_is_directory"
    UNREADABLE = "unreadable"


class ConfigValidationResult(BaseResult):
    """Non-raising result of structural + semantic config validation."""

    status: ConfigValidationStatus
    config_path: Path
    raw: dict[str, Any] | None = None
    config: DovoConfig | None = None

    @property
    def ok(self) -> bool:
        """Return True when config is structurally and semantically valid."""
        return self.status == ConfigValidationStatus.VALID


_LOAD_STATUS_TO_VALIDATION: dict[ConfigLoadStatus, ConfigValidationStatus] = {
    ConfigLoadStatus.OK: ConfigValidationStatus.VALID,
    ConfigLoadStatus.NOT_FOUND: ConfigValidationStatus.NOT_FOUND,
    ConfigLoadStatus.MALFORMED_JSON: ConfigValidationStatus.MALFORMED_JSON,
    ConfigLoadStatus.ROOT_NOT_OBJECT: ConfigValidationStatus.ROOT_NOT_OBJECT,
    ConfigLoadStatus.SCHEMA_INVALID: ConfigValidationStatus.INVALID,
    ConfigLoadStatus.PATH_IS_DIRECTORY: ConfigValidationStatus.PATH_IS_DIRECTORY,
    ConfigLoadStatus.UNREADABLE: ConfigValidationStatus.UNREADABLE,
}


def validate_config_result(config_path: Path) -> ConfigValidationResult:
    """Validate config without raising.

    Primary validation surface for ``dovo config validate``. Does not print,
    exit, create, or mutate config files.

    Args:
        config_path: Absolute path to the config.json file to validate.

    Returns:
        Classified ``ConfigValidationResult`` with absolute ``config_path``.
    """
    from dovo.core.config.loader import load_config_at

    loaded = load_config_at(config_path)
    status = _LOAD_STATUS_TO_VALIDATION[loaded.status]

    if loaded.status != ConfigLoadStatus.OK:
        return ConfigValidationResult(
            status=status,
            config_path=loaded.config_path,
            raw=loaded.raw,
            config=None,
            errors=list(loaded.errors),
            warnings=[],
            fixes=list(loaded.fixes),
        )

    if loaded.config is None:
        return ConfigValidationResult(
            status=ConfigValidationStatus.INVALID,
            config_path=loaded.config_path,
            raw=loaded.raw,
            config=None,
            errors=[
                f"Configuration loaded from '{loaded.config_path}' but the parsed config is missing "
                f"(CONFIG_INTERNAL_INVARIANT)."
            ],
            warnings=[],
            fixes=[],
        )

    provider_error = _unregistered_provider_error(loaded.config)
    if provider_error is not None:
        return ConfigValidationResult(
            status=ConfigValidationStatus.INVALID,
            config_path=loaded.config_path,
            raw=loaded.raw,
            config=None,
            errors=[provider_error],
            warnings=[],
            fixes=["Set agent.provider to one of the supported providers named in the error"],
        )

    warnings, warning_fixes = _semantic_warnings(loaded.config)

    return ConfigValidationResult(
        status=ConfigValidationStatus.VALID,
        config_path=loaded.config_path,
        raw=loaded.raw,
        config=loaded.config,
        errors=[],
        warnings=warnings,
        fixes=warning_fixes,
    )


def _unregistered_provider_error(config: DovoConfig) -> str | None:
    """Return the unsupported-provider message if the configured provider isn't registered, else None."""
    if config.agent.provider in PROVIDERS:
        return None
    return unsupported_provider_message(config.agent.provider)


def _semantic_warnings(config: DovoConfig) -> tuple[list[str], list[str]]:
    """Return semantic warnings and fixes in FR-7 rule order."""
    warnings: list[str] = []
    fixes: list[str] = []

    if config.agent.provider != "local" and config.agent.model is None:
        warnings.append("agent.provider is not 'local' but agent.model is missing (CONFIG_WARN_AGENT_MODEL_MISSING).")
        fixes.append("Set agent.model or use provider=local")

    endpoint = config.agent.endpoint
    if endpoint is not None and not _is_absolute_http_url(endpoint):
        warnings.append(f"agent.endpoint is not an absolute http(s) URL: '{endpoint}' (CONFIG_WARN_AGENT_ENDPOINT).")
        fixes.append("Set agent.endpoint to an absolute http:// or https:// URL, or null")

    max_active = config.sandbox.max_active_sandboxes
    if max_active > 10:
        warnings.append(f"sandbox.max_active_sandboxes ({max_active}) exceeds 10 (CONFIG_WARN_SANDBOX_LIMIT).")
        fixes.append("Lower sandbox.max_active_sandboxes to 10 or fewer")

    return warnings, fixes


def _is_absolute_http_url(value: str) -> bool:
    """Return True when ``value`` is an absolute http:// or https:// URL."""
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)
