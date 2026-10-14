"""Render a structured tool policy into Claude Code CLI flags."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from dovo.common.tool_policy import ToolCapability, ToolPolicy, ToolRule
from dovo.core.agents.tools import PolicyRoots


@dataclass(frozen=True)
class ClaudePolicyArgs:
    """Rendered Claude Code CLI flags for one resolved policy."""

    flags: tuple[str, ...]


_PATH_GLOB_ALL: Final = (None, "**")
_PATH_CAPABILITIES: Final = (ToolCapability.READ, ToolCapability.WRITE)
_TOOL_GROUPS: Final = (
    (ToolCapability.READ, ("Read", "Grep", "Glob")),
    (ToolCapability.WRITE, ("Edit", "Write", "NotebookEdit")),
    (ToolCapability.SHELL, ("Bash",)),
    (ToolCapability.NETWORK, ("WebFetch",)),
)
_ALL_BUILTINS_APPROVALS: Final = ("Read", "Edit", "Bash", "WebFetch")
_BARE_PERMISSIONS: Final = {
    ToolCapability.READ: "Read",
    ToolCapability.WRITE: "Edit",
    ToolCapability.SHELL: "Bash",
    ToolCapability.NETWORK: "WebFetch",
}
_SHELL_PREFIX_SUFFIX: Final = " *"
_NETWORK_WILDCARD_PREFIX: Final = "*."


def _path_rule_error(rule: ToolRule, *, deny: bool) -> str | None:
    """Return why a read/write rule cannot be preserved, or None."""
    if deny:
        return f"{rule.capability.value} deny rules cannot be preserved for Claude Code"

    if rule.pattern not in _PATH_GLOB_ALL:
        return f"{rule.capability.value} pattern {rule.pattern!r} is narrower than a whole root and cannot be preserved"

    return None


def _rule_error(rule: ToolRule, *, deny: bool) -> str | None:
    """Return why rule cannot be preserved for Claude Code, or None."""
    if rule.capability == ToolCapability.MCP:
        return "mcp rules cannot be preserved for Claude Code"

    if rule.capability in _PATH_CAPABILITIES:
        return _path_rule_error(rule, deny=deny)

    if rule.capability == ToolCapability.NETWORK and (rule.pattern or "").startswith(_NETWORK_WILDCARD_PREFIX):
        return f"network pattern {rule.pattern!r} cannot be preserved for Claude Code"

    return None


def _rule_permission(rule: ToolRule) -> str:
    """Return the Claude permission string for a rule that passed _rule_error."""
    bare = _BARE_PERMISSIONS[rule.capability]
    if rule.pattern is None or rule.capability in _PATH_CAPABILITIES:
        return bare

    if rule.capability == ToolCapability.NETWORK:
        return f"{bare}(domain:{rule.pattern})"

    if rule.pattern.endswith(_SHELL_PREFIX_SUFFIX):
        return f"{bare}({rule.pattern.removesuffix(_SHELL_PREFIX_SUFFIX)}:*)"

    return f"{bare}({rule.pattern})"


def _first_error(rules: list[ToolRule], *, deny: bool) -> str | None:
    """Return the first _rule_error across rules, or None."""
    for rule in rules:
        error = _rule_error(rule, deny=deny)
        if error is not None:
            return error

    return None


def _unique(items: list[str]) -> list[str]:
    """Return items without duplicates, in first-seen order."""
    return list(dict.fromkeys(items))


def _tool_names(policy: ToolPolicy) -> list[str]:
    """Return the built-in tool names the allow rules expose, in the fixed FR-1 order."""
    granted = {rule.capability for rule in policy.allow}

    return [name for capability, names in _TOOL_GROUPS if capability in granted for name in names]


def _granted_roots(policy: ToolPolicy, capability: ToolCapability) -> set[str]:
    """Return the roots the allow rules of capability grant."""
    return {rule.root or "worktree" for rule in policy.allow if rule.capability == capability}


def _scratch_flags(policy: ToolPolicy, roots: PolicyRoots) -> list[str] | str:
    """Return the --add-dir flags the read/write grants imply, or why their per-root grants cannot be preserved.

    `--add-dir` grants edit access, so scratch must reach every granted path tool or none.
    """
    granted = [
        capability_roots
        for capability in _PATH_CAPABILITIES
        if (capability_roots := _granted_roots(policy, capability))
    ]
    if not granted:
        return []

    if any("worktree" not in granted_roots for granted_roots in granted):
        return "a read or write grant that omits the worktree cannot be preserved"

    scratch_flags = ["scratch" in granted_roots for granted_roots in granted]
    if any(scratch_flags) != all(scratch_flags):
        return "scratch must be granted to every available path tool or to none"

    if not any(scratch_flags):
        return []

    if roots.scratch is None:
        return "a scratch grant requires a scratch root"

    return ["--add-dir", str(roots.scratch)]


def _flags(
    mode: str, tools: list[str] | None, allowed: list[str], denied: list[str], add_dirs: list[str]
) -> ClaudePolicyArgs:
    """Assemble the flag tuple; --tools is emitted whenever tools is not None, the lists only when non-empty, add_dirs last."""
    flags = ["--permission-mode", mode]
    if tools is not None:
        flags += ["--tools", ",".join(tools)]

    if allowed:
        flags += ["--allowedTools", ",".join(allowed)]

    if denied:
        flags += ["--disallowedTools", ",".join(denied)]

    return ClaudePolicyArgs(flags=(*flags, *add_dirs))


def _render_allow_all(policy: ToolPolicy) -> ClaudePolicyArgs | str:
    """Return bypassPermissions without denies, else dontAsk with every built-in granted and the denies rendered."""
    if not policy.deny:
        return ClaudePolicyArgs(flags=("--permission-mode", "bypassPermissions"))

    error = _first_error(policy.deny, deny=True)
    if error is not None:
        return error

    every_tool = [name for _, names in _TOOL_GROUPS for name in names]
    denied = _unique([_rule_permission(rule) for rule in policy.deny])

    return _flags("dontAsk", every_tool, list(_ALL_BUILTINS_APPROVALS), denied, [])


def _render_restricted(policy: ToolPolicy, roots: PolicyRoots) -> ClaudePolicyArgs | str:
    """Return dontAsk flags for the allow and deny rules, or why a rule cannot be preserved."""
    error = _first_error(policy.allow, deny=False) or _first_error(policy.deny, deny=True)
    if error is not None:
        return error

    add_dirs = _scratch_flags(policy, roots)
    if isinstance(add_dirs, str):
        return add_dirs

    allowed = _unique([_rule_permission(rule) for rule in policy.allow])
    denied = _unique([_rule_permission(rule) for rule in policy.deny])

    return _flags("dontAsk", _tool_names(policy), allowed, denied, add_dirs)


def render_claude_policy(policy: ToolPolicy, roots: PolicyRoots) -> ClaudePolicyArgs | str:
    """Render policy into Claude Code flags, or return why a rule cannot be preserved."""
    if policy.allow_all:
        return _render_allow_all(policy)

    return _render_restricted(policy, roots)
