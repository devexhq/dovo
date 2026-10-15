"""Map Claude Code stream-json events to provider-agnostic agent progress items."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Final

from dovo.core.agents.models import AgentProgress, AgentProgressKind
from dovo.core.agents.progress_target import display_progress_target

CLAUDE_TOOL_KINDS: Final[Mapping[str, AgentProgressKind]] = MappingProxyType(
    {
        "Read": AgentProgressKind.READ,
        "Grep": AgentProgressKind.SEARCH,
        "Glob": AgentProgressKind.SEARCH,
        "Edit": AgentProgressKind.EDIT,
        "Write": AgentProgressKind.EDIT,
        "NotebookEdit": AgentProgressKind.EDIT,
        "Bash": AgentProgressKind.SHELL,
    }
)

CLAUDE_TARGET_FIELDS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "Read": "file_path",
        "Grep": "pattern",
        "Glob": "pattern",
        "Edit": "file_path",
        "Write": "file_path",
        "NotebookEdit": "notebook_path",
        "Bash": "command",
    }
)

_BLOCK_TYPES_BY_EVENT: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {"assistant": frozenset({"tool_use", "text"}), "user": frozenset({"tool_result"})}
)


def map_claude_line(line: str, *, worktree_path: Path) -> tuple[AgentProgress, ...]:
    """Map one Claude Code stdout JSONL line to its progress items; an empty tuple means the line is skipped."""
    progress_items = (_progress_for_block(block, worktree_path) for block in _progress_blocks(line))

    return tuple(item for item in progress_items if item is not None)


def _progress_blocks(line: str) -> list[dict[str, object]]:
    """Return the tool_use and text blocks of an assistant event or the tool_result blocks of a user event, in order."""
    try:
        event = json.loads(line)
    except ValueError:
        return []

    if not isinstance(event, dict):
        return []

    block_types = _BLOCK_TYPES_BY_EVENT.get(str(event.get("type")))
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if block_types is None or not isinstance(content, list):
        return []

    return [block for block in content if isinstance(block, dict) and block.get("type") in block_types]


def _progress_for_block(block: dict[str, object], worktree_path: Path) -> AgentProgress | None:
    """Return the progress item a text, tool_use or tool_result block yields, or None when it names nothing usable."""
    if block.get("type") == "text":
        return _note_for_block(block, worktree_path)

    if block.get("type") == "tool_result":
        return _result_for_block(block, worktree_path)

    tool_name = block.get("name")
    if not isinstance(tool_name, str) or tool_name not in CLAUDE_TOOL_KINDS:
        return None

    kind = CLAUDE_TOOL_KINDS[tool_name]

    tool_input = block.get("input")
    raw_target = tool_input.get(CLAUDE_TARGET_FIELDS[tool_name]) if isinstance(tool_input, dict) else None
    if not isinstance(raw_target, str) or not raw_target.strip():
        return None

    call_id = block.get("id")

    return AgentProgress(
        kind=kind,
        target=display_progress_target(kind, raw_target, worktree_path),
        call_id=call_id if isinstance(call_id, str) else None,
    )


def _result_for_block(block: dict[str, object], worktree_path: Path) -> AgentProgress | None:
    """Return a RESULT item holding the block's text with worktree paths made relative, or None without a call id or text."""
    call_id = block.get("tool_use_id")
    text = _result_text(block.get("content"))
    if not isinstance(call_id, str) or text is None:
        return None

    return AgentProgress(
        kind=AgentProgressKind.RESULT,
        target=text.replace(f"{worktree_path}/", ""),
        call_id=call_id,
        is_error=block.get("is_error") is True,
    )


def _result_text(content: object) -> str | None:
    """Return a tool_result's text, joining the text blocks of a list, or None when it holds no non-blank text."""
    if isinstance(content, list):
        content = "\n".join(
            str(part["text"]) for part in content if isinstance(part, dict) and isinstance(part.get("text"), str)
        )

    if not isinstance(content, str) or not content.strip():
        return None

    return content


def _note_for_block(block: dict[str, object], worktree_path: Path) -> AgentProgress | None:
    """Return a note carrying the first non-blank line of a text block, or None when the text is blank or not a string."""
    text = block.get("text")
    if not isinstance(text, str):
        return None

    first_line = next((stripped for stripped in map(str.strip, text.splitlines()) if stripped), None)
    if first_line is None:
        return None

    return AgentProgress(
        kind=AgentProgressKind.NOTE,
        target=display_progress_target(AgentProgressKind.NOTE, first_line, worktree_path),
    )
