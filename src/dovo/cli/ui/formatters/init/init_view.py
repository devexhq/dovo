"""Presentation view model for workspace initialization results."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from dovo.common.diagnostic_log import DiagnosticRecord
from dovo.common.models import Remediation
from dovo.core.bootstrap.models import BootstrapOutcome


class WorkspaceInitView(BaseModel):
    """Semantic view of workspace initialization results."""

    model_config = {"extra": "forbid", "strict": True}

    ok: bool
    root_path: Path | None = None
    root_path_relative: str | None = None
    bootstrap_outcome: BootstrapOutcome | None = None
    dirs_created: list[str] = Field(default_factory=list)
    project_id: str | None = None
    identity_path_relative: str | None = None
    identity_preserved: bool = False
    gitignore_path_relative: str | None = None
    gitignore_tracked_entries: list[str] = Field(default_factory=list)
    config_created: bool = False
    config_overwritten: bool = False
    config_repaired: bool = False
    config_skipped_existing: bool = False
    config_path_relative: str | None = None
    inserted_keys: list[str] = Field(default_factory=list)
    seeded_files: list[str] = Field(default_factory=list)
    skipped_seed_files: list[str] = Field(default_factory=list)
    overwritten_seed_files: list[str] = Field(default_factory=list)
    error_code: str | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    remediations: list[Remediation] = Field(default_factory=list)
    diagnostics: list[DiagnosticRecord] = Field(default_factory=list)
