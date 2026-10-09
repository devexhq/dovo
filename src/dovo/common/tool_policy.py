"""Structured agent tool policy: capabilities, rules, and the pattern grammar shared by steps and config."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Final, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

LEGACY_TOOLS_MESSAGE: Final[str] = (
    "Agent step tools must be a policy object with allow, deny, and allow_all. "
    "Fix: replace the tools string list with structured capability rules."
)

_DRIVE_PATH_RE: Final = re.compile(r"^[A-Za-z]:")
_HOST_RE: Final = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*$")
_MCP_RE: Final = re.compile(r"^[^/*\s]+/([^/*\s]+|\*)$")
_SHELL_FORBIDDEN_CHARS: Final = frozenset(";&|<>$`()\n\r\\*?[]{}~!")
_SHELL_PREFIX_SUFFIX: Final = " *"


class ToolCapability(StrEnum):
    """Tool families a policy can grant or deny."""

    SHELL = "shell"
    READ = "read"
    WRITE = "write"
    NETWORK = "network"
    MCP = "mcp"


class ToolRule(BaseModel):
    """One allow or deny rule: a capability, an optional root for read/write, and an optional pattern."""

    model_config = {"extra": "forbid", "strict": True}

    capability: ToolCapability
    root: Literal["worktree", "scratch"] | None = None
    pattern: str | None = None

    @field_validator("capability", mode="before")
    @classmethod
    def coerce_capability(cls, val: Any) -> Any:
        """Coerce YAML/JSON strings to ToolCapability (strict mode skips enum coercion)."""
        if isinstance(val, str):
            try:
                return ToolCapability(val)
            except ValueError:
                pass
        return val

    @model_validator(mode="after")
    def validate_rule_shape(self) -> ToolRule:
        """Reject a root on shell/network/mcp rules and any pattern outside the capability's grammar."""
        if self.root is not None and self.capability not in (ToolCapability.READ, ToolCapability.WRITE):
            raise ValueError(f"root is only valid for read and write rules, not {self.capability.value}.")
        reason = validate_rule_pattern(self.capability, self.pattern)
        if reason is not None:
            raise ValueError(f"Invalid {self.capability.value} pattern {self.pattern!r}: {reason}.")
        return self


class ToolPolicy(BaseModel):
    """Complete tool policy for one agent step; an explicit instance replaces any inherited default."""

    model_config = {"extra": "forbid", "strict": True}

    allow: list[ToolRule] = Field(default_factory=list)
    deny: list[ToolRule] = Field(default_factory=list)
    allow_all: bool = False


def _read_write_error(pattern: str) -> str | None:
    """Return why pattern is not a root-relative POSIX glob, or None."""
    if not pattern:
        return "pattern must not be empty"
    if "\x00" in pattern:
        return "pattern must not contain NUL"
    normalized = pattern.replace("\\", "/")
    if normalized.startswith("/") or _DRIVE_PATH_RE.match(normalized):
        return "pattern must be relative to the root, not absolute"
    if ".." in normalized.split("/"):
        return "pattern must not contain '..' segments"
    return None


def _shell_error(pattern: str) -> str | None:
    """Return why pattern is not exact command text or a trailing ' *' prefix, or None."""
    if not pattern.strip():
        return "pattern must not be empty"
    command = pattern.removesuffix(_SHELL_PREFIX_SUFFIX)
    if not command.strip() or command != command.strip():
        return "pattern must be a command optionally followed by ' *'"
    if any(char in _SHELL_FORBIDDEN_CHARS for char in command):
        return "only a trailing ' *' wildcard and no shell chaining, redirection, or substitution syntax is allowed"
    return None


def _network_error(pattern: str) -> str | None:
    """Return why pattern is not a literal host or '*.' plus a host, or None."""
    host = pattern.removeprefix("*.")
    if not _HOST_RE.match(host):
        return "pattern must be a literal host or '*.' followed by a host, without scheme, path, or credentials"
    return None


def _mcp_error(pattern: str) -> str | None:
    """Return why pattern is not 'server/tool' or 'server/*', or None."""
    if not _MCP_RE.match(pattern):
        return "pattern must be 'server/tool' or 'server/*'"
    return None


_PATTERN_CHECKS: Final = {
    ToolCapability.READ: _read_write_error,
    ToolCapability.WRITE: _read_write_error,
    ToolCapability.SHELL: _shell_error,
    ToolCapability.NETWORK: _network_error,
    ToolCapability.MCP: _mcp_error,
}


def validate_rule_pattern(capability: ToolCapability, pattern: str | None) -> str | None:
    """Return why pattern is outside capability's grammar, or None when valid."""
    if pattern is None:
        return None
    return _PATTERN_CHECKS[capability](pattern)


def coerce_tool_policy(val: object) -> object:
    """Raise ValueError(LEGACY_TOOLS_MESSAGE) for a list; pass None and mappings through."""
    if isinstance(val, list):
        raise ValueError(LEGACY_TOOLS_MESSAGE)
    return val
