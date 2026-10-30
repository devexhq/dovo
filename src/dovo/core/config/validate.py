"""Non-raising config validation engine for structured error/warning lists."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dovo.common.models import BaseResult, Remediation
from dovo.core.agents.registry import PROVIDERS, unsupported_provider_message
from dovo.core.config.agent_settings import agent_settings
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

    Raises:
        RuntimeError: When a successful load carries no parsed config, a broken invariant.
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
            error_code=loaded.error_code,
            errors=list(loaded.errors),
            warnings=[],
            remediations=list(loaded.remediations),
            diagnostics=list(loaded.diagnostics),
        )

    if loaded.config is None:
        raise RuntimeError(f"Configuration loaded from '{loaded.config_path}' but the parsed config is missing.")

    provider_error = _unregistered_provider_error(loaded.config)
    if provider_error is not None:
        return ConfigValidationResult(
            status=ConfigValidationStatus.INVALID,
            config_path=loaded.config_path,
            raw=loaded.raw,
            config=None,
            errors=[provider_error],
            warnings=[],
            remediations=[
                Remediation(message="Set 'default_agent' to one of the supported providers named in the error")
            ],
        )

    warnings, warning_remediations = _semantic_warnings(loaded.config)

    return ConfigValidationResult(
        status=ConfigValidationStatus.VALID,
        config_path=loaded.config_path,
        raw=loaded.raw,
        config=loaded.config,
        errors=[],
        warnings=warnings,
        remediations=warning_remediations,
    )


def _unregistered_provider_error(config: DovoConfig) -> str | None:
    """Return the unsupported-provider message for an unregistered default_agent."""
    if config.default_agent in PROVIDERS:
        return None
    return f"default_agent: {unsupported_provider_message(config.default_agent)}"


def _provider_requires_model(provider: str) -> bool:
    """Return True when the registered provider descriptor requires agents.<provider>.model."""
    spec = PROVIDERS.get(provider)

    return spec is not None and spec.requires_model


def _model_warnings(config: DovoConfig) -> tuple[list[str], list[Remediation]]:
    """Return the missing-model warning and remediation for the default_agent block."""
    provider = config.default_agent
    if not _provider_requires_model(provider) or agent_settings(config).model is not None:
        return [], []

    return (
        [
            f"default_agent '{provider}' requires agents.{provider}.model but it is missing "
            f"(CONFIG_WARN_AGENT_MODEL_MISSING)."
        ],
        [Remediation(message=f"Set 'agents.{provider}.model'")],
    )


def _endpoint_warnings(config: DovoConfig) -> tuple[list[str], list[Remediation]]:
    """Return one non-absolute-endpoint warning and remediation per agents block, in config order."""
    warnings: list[str] = []
    remediations: list[Remediation] = []

    for provider, settings in config.agents.items():
        endpoint = settings.endpoint
        if endpoint is None or _is_absolute_http_url(endpoint):
            continue
        warnings.append(
            f"agents.{provider}.endpoint is not an absolute http(s) URL: '{endpoint}' (CONFIG_WARN_AGENT_ENDPOINT)."
        )
        remediations.append(
            Remediation(message=f"Set 'agents.{provider}.endpoint' to an absolute http:// or https:// URL, or null")
        )

    return warnings, remediations


def _worktree_warnings(config: DovoConfig) -> tuple[list[str], list[Remediation]]:
    """Return the worktree-limit warning and remediation for a max_active_worktrees above 10."""
    max_active = config.worktree.max_active_worktrees
    if max_active <= 10:
        return [], []

    return (
        [f"worktree.max_active_worktrees ({max_active}) exceeds 10 (CONFIG_WARN_WORKTREE_LIMIT)."],
        [Remediation(message="Lower 'worktree.max_active_worktrees' to 10 or fewer")],
    )


def _semantic_warnings(config: DovoConfig) -> tuple[list[str], list[Remediation]]:
    """Return semantic warnings and remediations in FR-7 rule order."""
    warnings: list[str] = []
    remediations: list[Remediation] = []

    for rule_warnings, rule_remediations in (
        _model_warnings(config),
        _endpoint_warnings(config),
        _worktree_warnings(config),
    ):
        warnings.extend(rule_warnings)
        remediations.extend(rule_remediations)

    return warnings, remediations


def _is_absolute_http_url(value: str) -> bool:
    """Return True when ``value`` is an absolute http:// or https:// URL."""
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)
