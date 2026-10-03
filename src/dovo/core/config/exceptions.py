"""Config domain exceptions."""

from __future__ import annotations

from pathlib import Path

from dovo.core.config.loader import ConfigLoadResult
from dovo.core.config.models import ConfigTier


class ConfigLoadError(RuntimeError):
    """Raised by Config accessor properties when config.json cannot be loaded."""

    def __init__(self, message: str, result: ConfigLoadResult) -> None:
        """Retain the structured, status-attributed load result alongside the formatted message."""
        super().__init__(message)
        self.result = result


class ConfigTierValidationError(Exception):
    """Raised when a config tier file is unreadable, malformed, or fails DovoConfig validation."""

    def __init__(self, tier: ConfigTier, path: Path, details: str) -> None:
        """Format a tier-attributed validation message and retain its parts for callers."""
        super().__init__(f"Invalid configuration in {tier} layer ({path}): {details}")
        self.tier = tier
        self.path = path
        self.details = details
