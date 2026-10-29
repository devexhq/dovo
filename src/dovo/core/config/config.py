"""Config domain entrypoint."""

from __future__ import annotations

from pathlib import Path

from dovo.common.filesystem import WorkspacePaths
from dovo.common.formatters import format_remediations
from dovo.core.config.exceptions import ConfigLoadError
from dovo.core.config.loader import ConfigLoadResult, ConfigLoadStatus, load_config
from dovo.core.config.models import (
    ConcurrencyConfig,
    DoctorConfig,
    DovoConfig,
    EnvironmentConfig,
    HistoryConfig,
    ProjectConfig,
    PruneConfig,
    TelemetryConfig,
    WorktreeConfig,
)
from dovo.core.config.mutate import (
    ConfigSetResult,
    ConfigUnsetResult,
    set_config_value_result,
    unset_config_value_result,
)
from dovo.core.config.tiers import load_tiered_config
from dovo.core.config.validate import (
    ConfigValidationResult,
    validate_config_result,
)


def _require_config(result: ConfigLoadResult, root_dir: Path) -> DovoConfig:
    """Return result.config or raise ConfigLoadError with its errors and Fix: block formatted into the message."""
    if not result.ok or result.config is None:
        parts = list(result.errors)
        if result.remediations:
            parts.append(format_remediations(result.remediations))
        errors = "; ".join(parts) if parts else "unknown error"
        raise ConfigLoadError(f"Failed to load config at '{root_dir}': {errors}", result)
    return result.config


class Config:
    """Unified entrypoint for workspace configuration loading, validation and mutation."""

    def __init__(self, paths: WorkspacePaths) -> None:
        """Bind this Config instance to a resolved WorkspacePaths snapshot."""
        self._paths = paths
        self._cached_config: DovoConfig | None = None

    @classmethod
    def load_required(cls, paths: WorkspacePaths) -> DovoConfig:
        """Load and validate the effective config for paths, raising ConfigLoadError on failure."""
        return _require_config(cls(paths).load(), paths.root_dir)

    def load(self) -> ConfigLoadResult:
        """Load the repo config, then merge the Packaged, Global, User and Repo tiers; never raises."""
        repo_result = load_config(self._paths)
        if not repo_result.ok:
            return repo_result

        tiered = load_tiered_config(self._paths)
        if not tiered.ok or tiered.config is None:
            return ConfigLoadResult(
                status=ConfigLoadStatus.TIER_INVALID,
                config_path=tiered.path or repo_result.config_path,
                errors=list(tiered.errors),
            )

        return ConfigLoadResult(
            status=ConfigLoadStatus.OK,
            config_path=repo_result.config_path,
            raw=tiered.config.model_dump(mode="json"),
            config=tiered.config,
            errors=[],
        )

    def validate(self, *, config_path: Path | None = None) -> ConfigValidationResult:
        """Validate ``config.json`` against schema constraints and return structured report."""
        target_cfg = config_path if config_path is not None else self._paths.config_file
        return validate_config_result(target_cfg)

    def set(self, key: str, raw_value: str) -> ConfigSetResult:
        """Set a dot-path configuration key from the raw CLI token and persist to disk."""
        result = set_config_value_result(key, raw_value, config_path=self._paths.config_file)
        if result.ok:
            self._cached_config = None
        return result

    def unset(self, key: str) -> ConfigUnsetResult:
        """Remove a dot-path configuration key and persist to disk."""
        result = unset_config_value_result(key, config_path=self._paths.config_file)
        if result.ok:
            self._cached_config = None
        return result

    # ------------------------------------------------------------------ #
    # Accessor properties (load-once, raise on failure)                  #
    # ------------------------------------------------------------------ #

    @property
    def _loaded_config(self) -> DovoConfig:
        """Load and cache the DovoConfig, raising ConfigLoadError on failure."""
        if self._cached_config is None:
            self._cached_config = _require_config(self.load(), self._paths.root_dir)
        return self._cached_config

    @property
    def version(self) -> int:
        """Return the config schema version."""
        return self._loaded_config.version

    @property
    def project(self) -> ProjectConfig:
        """Return the project identity section of the loaded config."""
        return self._loaded_config.project

    @property
    def worktree(self) -> WorktreeConfig:
        """Return the worktree lifecycle section of the loaded config."""
        return self._loaded_config.worktree

    @property
    def history(self) -> HistoryConfig:
        """Return the session history retention section of the loaded config."""
        return self._loaded_config.history

    @property
    def doctor(self) -> DoctorConfig:
        """Return the doctor check toggles section of the loaded config."""
        return self._loaded_config.doctor

    @property
    def prune(self) -> PruneConfig:
        """Return the prune cleanup toggles section of the loaded config."""
        return self._loaded_config.prune

    @property
    def telemetry(self) -> TelemetryConfig:
        """Return the optional telemetry section of the loaded config."""
        return self._loaded_config.telemetry

    @property
    def concurrency(self) -> ConcurrencyConfig:
        """Return the concurrency and locking section of the loaded config."""
        return self._loaded_config.concurrency

    @property
    def environment(self) -> EnvironmentConfig:
        """Return the environment section of the loaded config."""
        return self._loaded_config.environment
