"""Models for the bootstrap and workspace initialization domain."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, StrEnum
from pathlib import Path
from typing import Self

from pydantic import model_validator

from dovo.common.models import BaseResult
from dovo.core.catalog.models import SeedSummary
from dovo.core.config.models import ConfigGenerationSummary
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


@dataclass(frozen=True, slots=True)
class BootstrapSummary:
    """What bootstrapping the `.dovo/` directory tree created, found and repaired."""

    root_path: Path
    outcome: BootstrapOutcome
    root_created: bool
    dirs_created: tuple[Path, ...]
    dirs_existing: tuple[Path, ...]
    repaired: bool
    gitignore_created: bool


class WorkspaceInitResult(BaseResult):
    """Structured outcome of initializing a project workspace; a failure keeps the summaries of the steps before it."""

    bootstrap: BootstrapSummary | None = None
    project_identity: ProjectIdentity | None = None
    identity_path: Path | None = None
    identity_outcome: ProjectIdentityOutcome | None = None
    config_generation: ConfigGenerationSummary | None = None
    seed: SeedSummary | None = None

    @property
    def ok(self) -> bool:
        """True exactly when error_code is null."""
        return self.error_code is None

    @model_validator(mode="after")
    def _require_every_step_on_success(self) -> Self:
        """Reject a success that lacks a step: every failure sets error_code."""
        steps = (self.bootstrap, self.project_identity, self.config_generation, self.seed)
        if self.error_code is None and any(step is None for step in steps):
            raise ValueError("A WorkspaceInitResult without error_code must hold every initialization step.")
        return self


@dataclass(frozen=True)
class DovoTreeSnapshot:
    """The project's .dovo tree as it was before an automatic initialization attempt."""

    dovo_dir: Path
    existed: bool
    paths: frozenset[Path]  # relative to dovo_dir
    file_bytes: dict[Path, bytes]  # relative path -> bytes, for RESTORED_FILES that existed
