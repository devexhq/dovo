"""Config validation: structural load, default_agent check and semantic warnings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dovo.common.models import BaseResult, Remediation
from dovo.core.agents.exceptions import AgentError
from dovo.core.agents.registry import PROVIDERS, require_provider
from dovo.core.config.agent_settings import agent_settings
from dovo.core.config.exceptions import ConfigError, ConfigRawObject
from dovo.core.config.loader import LoadedConfig, load_config_at
from dovo.core.config.models import DovoConfig


class ConfigValidationResult(BaseResult):
    """Result of `Config.validate`: the valid config and its warnings, or the failure's error code and errors."""

    config_path: Path
    raw: dict[str, Any] | None = None
    config: DovoConfig | None = None

    @property
    def ok(self) -> bool:
        """Return True exactly when error_code is None."""
        return self.error_code is None


@dataclass(frozen=True, slots=True)
class ValidatedConfig:
    """A config.json that passed structural and semantic validation, with its semantic warnings."""

    loaded: LoadedConfig
    warnings: list[str]
    remediations: list[Remediation]


def validate_config(config_path: Path) -> ValidatedConfig:
    """Load config.json at config_path, check its default_agent and collect its semantic warnings.

    Does not print, exit, create, or mutate config files.

    Args:
        config_path: Absolute path to the config.json file to validate.

    Returns:
        The validated config with its warnings and their remediations.

    Raises:
        ConfigError: When the config fails to load or names an unsupported default_agent.
    """
    loaded = load_config_at(config_path)

    try:
        require_provider(loaded.config.default_agent)
    except AgentError as exc:
        raise ConfigError(
            exc.error_code,
            f"default_agent: {exc.message}",
            [Remediation(message="Set 'default_agent' to one of the supported providers named in the error")],
            data=ConfigRawObject(loaded.raw),
        ) from exc

    warnings, remediations = _semantic_warnings(loaded.config)
    return ValidatedConfig(loaded=loaded, warnings=warnings, remediations=remediations)


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
