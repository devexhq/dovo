"""Workspace builder helpers for status collection tests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dovo.core.config.models import AgentSettings, DovoConfig, ProjectConfig, WorktreeConfig


def status_config_payload(*, model: str | None = "gpt-4o", max_active_worktrees: int = 5) -> dict[str, object]:
    """Return a valid config.json payload for the status test workspace."""
    return DovoConfig(
        version=1,
        project=ProjectConfig(name="status-ws"),
        agents={"copilot": AgentSettings(model=model)} if model else {},
        worktree=WorktreeConfig(max_active_worktrees=max_active_worktrees),
    ).model_dump(mode="json")


DEFAULT_STATUS_CONFIG = status_config_payload()

type BuildStatusWorkspace = Callable[..., Path]
