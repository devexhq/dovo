"""Models for the bootstrap and workspace initialization domain."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, StrEnum
from pathlib import Path
from typing import Self

from pydantic import Field, model_validator

from dovo.common.models import BaseResult
from dovo.core.catalog.models import SeedResult
from dovo.core.config.generator import ConfigGenerationResult
from dovo.core.project.models import ProjectIdentity, ProjectIdentityOutcome


class DirEnsureOutcome(Enum):
    """Result of attempting to ensure a directory exists."""

    CREATED = "created"
    EXISTING = "existing"


class BootstrapOutcome(StrEnum):
    """Classified outcome of bootstrapping the .dovo/ directory structure."""

    INITIALIZED = "initialized"
    REPAIRED = "repaired"
    ALREADY_INITIALIZED = "already_initialized"
    FAILED = "failed"


class InitFailureMode(StrEnum):
    """Failure mode classification for workspace initialization."""

    PREFLIGHT = "preflight"
    BOOTSTRAP = "bootstrap"
    CONFIG_GENERATION = "config_generation"
    INVALID_PROJECT_ID = "invalid_project_id"


class BootstrapResult(BaseResult):
    """Outcome of bootstrapping the `.dovo/` directory tree."""

    root_path: Path
    outcome: BootstrapOutcome = BootstrapOutcome.INITIALIZED
    root_created: bool = False
    dirs_created: list[Path] = Field(default_factory=list)
    dirs_existing: list[Path] = Field(default_factory=list)
    repaired: bool = False
    gitignore_created: bool = False
    seed_result: SeedResult = Field(default_factory=SeedResult)

    @property
    def ok(self) -> bool:
        """True when bootstrap completed without errors."""
        return not self.errors

    @model_validator(mode="after")
    def _resolve_outcome(self) -> Self:
        """Derive outcome from state booleans if not explicitly specified."""
        if self.errors:
            self.outcome = BootstrapOutcome.FAILED
        elif "outcome" not in self.model_fields_set:
            if self.repaired:
                self.outcome = BootstrapOutcome.REPAIRED
            elif self.root_created or self.dirs_created:
                self.outcome = BootstrapOutcome.INITIALIZED
            else:
                self.outcome = BootstrapOutcome.ALREADY_INITIALIZED
        return self


def _classify_init_failure_mode(
    *,
    bootstrap_result: BootstrapResult | None,
    config_result: ConfigGenerationResult | None,
    has_top_level_errors: bool,
) -> InitFailureMode | None:
    """Classify a WorkspaceInitResult's failure mode from its child results."""
    if bootstrap_result is None:
        return InitFailureMode.PREFLIGHT if has_top_level_errors else None
    if not bootstrap_result.ok:
        return InitFailureMode.BOOTSTRAP
    if config_result is not None and not config_result.ok:
        return InitFailureMode.CONFIG_GENERATION
    return None


class WorkspaceInitResult(BaseResult):
    """Structured outcome of initializing a project workspace."""

    bootstrap_result: BootstrapResult | None = None
    project_identity: ProjectIdentity | None = None
    identity_path: Path | None = None
    identity_outcome: ProjectIdentityOutcome | None = None
    config_result: ConfigGenerationResult | None = None
    seed_result: SeedResult | None = None
    failure_mode: InitFailureMode | None = None

    @property
    def ok(self) -> bool:
        """True when bootstrap, identity, config, and catalog seeding all succeed with no errors."""
        return (
            not self.errors
            and self.bootstrap_result is not None
            and self.bootstrap_result.ok
            and self.project_identity is not None
            and self.config_result is not None
            and self.config_result.ok
            and self.seed_result is not None
            and self.seed_result.ok
        )

    @model_validator(mode="after")
    def _resolve_failure_mode(self) -> Self:
        """Derive failure mode from child results when not explicitly provided."""
        if "failure_mode" not in self.model_fields_set:
            self.failure_mode = _classify_init_failure_mode(
                bootstrap_result=self.bootstrap_result,
                config_result=self.config_result,
                has_top_level_errors=bool(self.errors),
            )
        return self


@dataclass(frozen=True)
class DovoTreeSnapshot:
    """The project's .dovo tree as it was before an automatic initialization attempt."""

    dovo_dir: Path
    existed: bool
    paths: frozenset[Path]  # relative to dovo_dir
    file_bytes: dict[Path, bytes]  # relative path -> bytes, for RESTORED_FILES that existed
