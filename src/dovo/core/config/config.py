"""Config domain entrypoint."""

from __future__ import annotations

from pathlib import Path

from pydantic import JsonValue

from dovo.common.exceptions import Failure, RecordedDiagnostics, catch_failure
from dovo.common.filesystem import WorkspacePaths
from dovo.core.config.exceptions import ConfigError, ConfigRawObject, ConfigTierSource
from dovo.core.config.loader import ConfigLoadResult, load_config, load_config_at, read_config_object
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
    ConfigRemoval,
    ConfigSetResult,
    ConfigUnsetResult,
    with_value_set,
    without_value,
    write_mutated_config,
)
from dovo.core.config.parser import parse_config_value
from dovo.core.config.tiers import load_tiered_config
from dovo.core.config.validate import ConfigValidationResult, validate_config


def _failed_raw(failure: Failure) -> dict[str, object] | None:
    """Return the parsed JSON object a schema failure carries, or None for any other failure."""
    return failure.data.raw if isinstance(failure.data, ConfigRawObject) else None


def _require_config(result: ConfigLoadResult, root_dir: Path) -> DovoConfig:
    """Return result.config, or raise ConfigError with the failed load's code, errors, remediations and records."""
    if result.ok and result.config is not None:
        return result.config
    if result.error_code is None:
        raise RuntimeError(f"Config load at '{root_dir}' failed without an error_code")
    raise ConfigError(
        result.error_code,
        f"Failed to load config at '{root_dir}': {'; '.join(result.errors)}",
        result.remediations,
        data=RecordedDiagnostics(tuple(result.diagnostics)),
    )


class Config:
    """Unified entrypoint for workspace configuration loading, validation and mutation."""

    def __init__(self, paths: WorkspacePaths) -> None:
        """Bind this Config instance to a resolved WorkspacePaths snapshot."""
        self._paths = paths
        self._cached_config: DovoConfig | None = None

    @classmethod
    def load_required(cls, paths: WorkspacePaths) -> DovoConfig:
        """Load and validate the effective config for paths.

        Raises:
            ConfigError: If the configuration fails to load; data is RecordedDiagnostics.
        """
        return _require_config(cls(paths).load(), paths.root_dir)

    def load(self) -> ConfigLoadResult:
        """Load the repo config, then merge the Packaged, Global, User and Repo tiers; never raises."""
        config_path = self._paths.config_file
        with catch_failure("config.read", path=str(config_path)) as caught:
            load_config(self._paths)
        if caught.failure is not None:
            return ConfigLoadResult(
                config_path=config_path, raw=_failed_raw(caught.failure), **caught.failure.result_fields()
            )

        with catch_failure("config.load", root=str(self._paths.root_dir)) as caught:
            config = load_tiered_config(self._paths)
        if caught.failure is not None:
            source = caught.failure.data
            return ConfigLoadResult(
                config_path=source.path if isinstance(source, ConfigTierSource) else config_path,
                **caught.failure.result_fields(),
            )

        return ConfigLoadResult(config_path=config_path, raw=config.model_dump(mode="json"), config=config)

    def validate(self, *, config_path: Path | None = None) -> ConfigValidationResult:
        """Validate ``config.json`` against schema and semantic rules; never raises."""
        target_cfg = config_path if config_path is not None else self._paths.config_file
        with catch_failure("config.validate", path=str(target_cfg)) as caught:
            validated = validate_config(target_cfg)
        if caught.failure is not None:
            return ConfigValidationResult(
                config_path=target_cfg, raw=_failed_raw(caught.failure), **caught.failure.result_fields()
            )

        return ConfigValidationResult(
            config_path=validated.loaded.config_path,
            raw=validated.loaded.raw,
            config=validated.loaded.config,
            warnings=validated.warnings,
            remediations=validated.remediations,
        )

    def set(self, key: str, raw_value: str) -> ConfigSetResult:
        """Coerce raw_value once, set it at the dot-path key in ``config.json`` and persist it; never raises."""
        config_path = self._paths.config_file
        value: JsonValue = None
        with catch_failure("config.set", path=str(config_path)) as caught:
            loaded = read_config_object(config_path)
            value = parse_config_value(raw_value)
            write_mutated_config(config_path, with_value_set(loaded, key, value))
        if caught.failure is not None:
            return ConfigSetResult(config_path=config_path, key=key, value=value, **caught.failure.result_fields())

        self._cached_config = None
        return ConfigSetResult(config_path=config_path, key=key, value=value)

    def unset(self, key: str) -> ConfigUnsetResult:
        """Remove the dot-path key from a ``config.json`` that loads and persist it, writing nothing when absent.

        Never raises: a file that cannot be loaded, including one failing schema validation, is reported in the
        result whether or not the key is present.
        """
        config_path = self._paths.config_file
        removal: ConfigRemoval | None = None
        with catch_failure("config.unset", path=str(config_path)) as caught:
            removal = without_value(load_config_at(config_path, bypass_cache=True).raw, key)
            if removal is not None:
                write_mutated_config(config_path, removal.updated)
        existed = removal is not None
        previous_value = removal.previous_value if removal is not None else None
        if caught.failure is not None:
            return ConfigUnsetResult(
                config_path=config_path,
                key=key,
                existed=existed,
                previous_value=previous_value,
                **caught.failure.result_fields(),
            )

        self._cached_config = None
        return ConfigUnsetResult(config_path=config_path, key=key, existed=existed, previous_value=previous_value)

    # ------------------------------------------------------------------ #
    # Accessor properties (load-once, raise on failure)                  #
    # ------------------------------------------------------------------ #

    @property
    def _loaded_config(self) -> DovoConfig:
        """Load and cache the DovoConfig.

        Raises:
            ConfigError: If the configuration fails to load; data is RecordedDiagnostics.
        """
        if self._cached_config is None:
            self._cached_config = _require_config(self.load(), self._paths.root_dir)
        return self._cached_config

    @property
    def version(self) -> int:
        """Return the config schema version.

        Raises:
            ConfigError: If the configuration fails to load; data is RecordedDiagnostics.
        """
        return self._loaded_config.version

    @property
    def project(self) -> ProjectConfig:
        """Return the project identity section of the loaded config.

        Raises:
            ConfigError: If the configuration fails to load; data is RecordedDiagnostics.
        """
        return self._loaded_config.project

    @property
    def worktree(self) -> WorktreeConfig:
        """Return the worktree lifecycle section of the loaded config.

        Raises:
            ConfigError: If the configuration fails to load; data is RecordedDiagnostics.
        """
        return self._loaded_config.worktree

    @property
    def history(self) -> HistoryConfig:
        """Return the session history retention section of the loaded config.

        Raises:
            ConfigError: If the configuration fails to load; data is RecordedDiagnostics.
        """
        return self._loaded_config.history

    @property
    def doctor(self) -> DoctorConfig:
        """Return the doctor check toggles section of the loaded config.

        Raises:
            ConfigError: If the configuration fails to load; data is RecordedDiagnostics.
        """
        return self._loaded_config.doctor

    @property
    def prune(self) -> PruneConfig:
        """Return the prune cleanup toggles section of the loaded config.

        Raises:
            ConfigError: If the configuration fails to load; data is RecordedDiagnostics.
        """
        return self._loaded_config.prune

    @property
    def telemetry(self) -> TelemetryConfig:
        """Return the optional telemetry section of the loaded config.

        Raises:
            ConfigError: If the configuration fails to load; data is RecordedDiagnostics.
        """
        return self._loaded_config.telemetry

    @property
    def concurrency(self) -> ConcurrencyConfig:
        """Return the concurrency and locking section of the loaded config.

        Raises:
            ConfigError: If the configuration fails to load; data is RecordedDiagnostics.
        """
        return self._loaded_config.concurrency

    @property
    def environment(self) -> EnvironmentConfig:
        """Return the environment section of the loaded config.

        Raises:
            ConfigError: If the configuration fails to load; data is RecordedDiagnostics.
        """
        return self._loaded_config.environment
