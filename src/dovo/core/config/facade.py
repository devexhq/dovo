"""Config domain facade."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dovo.common.filesystem import WorkspacePaths
from dovo.core.config.exceptions import ConfigLoadError
from dovo.core.config.generator import ConfigGenerationResult, generate_default_config
from dovo.core.config.loader import ConfigLoadResult
from dovo.core.config.models import (
    AgentConfig,
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
from dovo.core.config.parser import parse_config_value
from dovo.core.config.serialize import as_json, serialize_config
from dovo.core.config.services.resolve import resolve_effective_config
from dovo.core.config.validate import (
    ConfigValidationResult,
    validate_config_result,
)


def _require_config(result: ConfigLoadResult, root_dir: Path) -> DovoConfig:
    """Return result.config or raise ConfigLoadError with its errors/fixes formatted into the message."""
    if not result.ok or result.config is None:
        parts = list(result.errors)
        if result.fixes:
            parts.append("Fix:\n" + "\n".join(f"- {f}" for f in result.fixes))
        errors = "; ".join(parts) if parts else "unknown error"
        raise ConfigLoadError(f"Failed to load config at '{root_dir}': {errors}", result)
    return result.config


class Config:
    """Unified entrypoint for workspace configuration loading, validation, mutation, and serialization."""

    def __init__(self, paths: WorkspacePaths) -> None:
        """Bind this Config instance to a resolved WorkspacePaths snapshot."""
        self._paths = paths
        self._cached_config: DovoConfig | None = None

    @classmethod
    def load_required(cls, paths: WorkspacePaths) -> DovoConfig:
        """Load and validate the effective config for paths, raising ConfigLoadError on failure."""
        return _require_config(resolve_effective_config(paths), paths.root_dir)

    def load(self) -> ConfigLoadResult:
        """Load the repo tier, merge Global/User tier overrides, and return a structured result."""
        return resolve_effective_config(self._paths)

    def validate(self, *, config_path: Path | None = None) -> ConfigValidationResult:
        """Validate ``config.json`` against schema constraints and return structured report."""
        target_cfg = config_path if config_path is not None else self._paths.config_file
        return validate_config_result(target_cfg)

    def set(self, key: str, value: Any) -> ConfigSetResult:
        """Set a dot-path configuration key and persist to disk."""
        parsed_value = self.parse_value(value) if isinstance(value, str) else value
        result = set_config_value_result(key, parsed_value, config_path=self._paths.config_file)
        if result.ok:
            self._cached_config = None
        return result

    def unset(self, key: str) -> ConfigUnsetResult:
        """Remove a dot-path configuration key and persist to disk."""
        result = unset_config_value_result(key, config_path=self._paths.config_file)
        if result.ok:
            self._cached_config = None
        return result

    def generate(
        self,
        *,
        overwrite: bool = False,
        repair: bool = False,
        project_name: str | None = None,
    ) -> ConfigGenerationResult:
        """Generate a default ``config.json`` file in workspace."""
        _project_name = project_name or self._paths.root_dir.name
        cfg_path = self._paths.config_file
        result = generate_default_config(cfg_path, _project_name, overwrite=overwrite, repair=repair)
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
    def agent(self) -> AgentConfig:
        """Return the agent provider section of the loaded config."""
        return self._loaded_config.agent

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

    # ------------------------------------------------------------------ #
    # Static and class-method helpers                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def parse_value(value: str) -> Any:
        """Parse string CLI token into typed Python object (bool, int, float, list, dict, str)."""
        return parse_config_value(value)

    @staticmethod
    def dump(config: DovoConfig) -> str:
        """Serialize DovoConfig instance into formatted JSON string."""
        return as_json(config)

    @staticmethod
    def serialize(config: DovoConfig) -> dict[str, Any]:
        """Serialize DovoConfig instance into JSON-ready dictionary."""
        return serialize_config(config)
