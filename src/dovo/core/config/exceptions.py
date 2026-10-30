"""Config domain exceptions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dovo.common.diagnostic_log import DiagnosticRecord
from dovo.common.exceptions import DomainError, FailureData


class ConfigError(DomainError):
    """Business failure generating, loading or repairing the configuration."""


@dataclass(frozen=True, slots=True)
class ConfigTierSource(FailureData):
    """The tier file a tier-loading failure names."""

    path: Path


@dataclass(frozen=True, slots=True)
class ConfigLoadDiagnostics(FailureData):
    """The diagnostic records the failed config load wrote, carried to the caller that reports it."""

    records: tuple[DiagnosticRecord, ...]
