"""Map Copilot stdout events to provider-agnostic agent progress items; masking and truncation belong to the reporter."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Final

from dovo.core.agents.models import AgentProgress, AgentProgressKind

COPILOT_TOOL_KINDS: Final[Mapping[str, AgentProgressKind]] = MappingProxyType(
    {
        "view": AgentProgressKind.READ,
        "rg": AgentProgressKind.SEARCH,
        "grep": AgentProgressKind.SEARCH,
        "glob": AgentProgressKind.SEARCH,
        "create": AgentProgressKind.EDIT,
        "edit": AgentProgressKind.EDIT,
        "apply_patch": AgentProgressKind.EDIT,
        "bash": AgentProgressKind.SHELL,
    }
)

COPILOT_TARGET_FIELDS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "view": "path",
        "create": "path",
        "edit": "path",
        "rg": "pattern",
        "grep": "pattern",
        "glob": "pattern",
        "bash": "command",
    }
)

_PATCH_FILE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+?)\s*$", re.MULTILINE)
_PATH_KINDS: Final[frozenset[AgentProgressKind]] = frozenset({AgentProgressKind.READ, AgentProgressKind.EDIT})


def map_copilot_line(line: str, *, worktree_path: Path) -> tuple[AgentProgress, ...]:
    """Map one Copilot stdout JSONL line to its progress items; an empty tuple means the line is skipped."""
    parsed = _parse_tool_start(line)
    if parsed is None:
        return ()

    targets = _targets_for_tool(*parsed)
    if targets is None:
        return ()

    kind, raw_targets = targets

    return tuple(
        AgentProgress(kind=kind, target=_display_target(kind, raw_target, worktree_path)) for raw_target in raw_targets
    )


def _parse_tool_start(line: str) -> tuple[str, object] | None:
    """Return (toolName, arguments) for a tool.execution_start event, or None for any other line."""
    try:
        event = json.loads(line)
    except ValueError:
        return None

    if not isinstance(event, dict) or event.get("type") != "tool.execution_start":
        return None

    data = event.get("data")
    if not isinstance(data, dict):
        return None

    tool_name = data.get("toolName")
    if not isinstance(tool_name, str):
        return None

    return tool_name, data.get("arguments")


def _targets_for_tool(tool_name: str, arguments: object) -> tuple[AgentProgressKind, list[str]] | None:
    """Return the progress kind and the raw targets a mapped tool names, or None when unmapped or a target field is missing."""
    kind = COPILOT_TOOL_KINDS.get(tool_name)
    if kind is None:
        return None

    if tool_name == "apply_patch":
        paths = _patch_paths(arguments) if isinstance(arguments, str) else []

        return (kind, paths) if paths else None

    field_name = COPILOT_TARGET_FIELDS[tool_name]
    value = arguments.get(field_name) if isinstance(arguments, dict) else None
    if not isinstance(value, str) or not value.strip():
        return None

    return kind, [value]


def _patch_paths(patch_text: str) -> list[str]:
    """Return each Add, Update or Delete File path in an apply_patch argument string, in order."""
    return _PATCH_FILE_PATTERN.findall(patch_text)


def _display_target(kind: AgentProgressKind, raw_target: str, worktree_path: Path) -> str:
    """Make a path relative to the worktree or collapse command whitespace, per kind."""
    if kind not in _PATH_KINDS:
        return " ".join(raw_target.split())

    try:
        return str(Path(raw_target).relative_to(worktree_path))
    except ValueError:
        return raw_target
