from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from worktree.core.config.models import WorktreeConfig
from worktree.core.config.mutate import ConfigSetStatus
from worktree.core.config.validate import ConfigValidationStatus


class ConfigValidationView(BaseModel):
    """Semantic view of configuration validation results."""

    model_config = {"extra": "forbid", "strict": True}

    status: ConfigValidationStatus
    config_path: Path
    status_label: str
    raw: dict[str, Any] | None = None
    config: WorktreeConfig | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    fixes: list[str] = Field(default_factory=list)


class ConfigSetView(BaseModel):
    """Semantic view of configuration mutation results."""

    model_config = {"extra": "forbid", "strict": True}

    status: ConfigSetStatus
    config_path: Path
    key: str
    value: Any
    value_str: str
    value_type: str
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    fixes: list[str] = Field(default_factory=list)
