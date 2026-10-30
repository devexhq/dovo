"""Config domain exceptions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dovo.common.exceptions import DomainError, FailureData
from dovo.core.config.loader import ConfigLoadResult


class ConfigError(DomainError):
    """Business failure generating, loading or repairing the configuration."""


@dataclass(frozen=True, slots=True)
class ConfigTierSource(FailureData):
    """The tier file a tier-loading failure names."""

    path: Path


class ConfigLoadError(RuntimeError):
    """Raised by Config accessor properties when config.json cannot be loaded."""

    def __init__(self, message: str, result: ConfigLoadResult) -> None:
        """Retain the structured, status-attributed load result alongside the formatted message."""
        super().__init__(message)
        self.result = result
