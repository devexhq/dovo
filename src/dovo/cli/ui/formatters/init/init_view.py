"""Presentation view model for workspace initialization results."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from dovo.cli.ui.formatters.base import ResultView
from dovo.core.bootstrap.models import BootstrapOutcome


class WorkspaceInitView(ResultView):
    """Semantic view of workspace initialization results."""

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
