"""Render a structured tool policy into Claude Code CLI flags."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from dovo.common.tool_policy import ToolCapability, ToolPolicy, ToolRule, validate_rule_pattern
from dovo.core.agents.tools import PolicyRoots


@dataclass(frozen=True)
class ClaudePolicyArgs:
    """Rendered Claude Code CLI flags for one resolved policy."""

    flags: tuple[str, ...]


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
_EMPTY_MCP_FLAGS: Final = ("--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}')
_MCP_WILDCARD: Final = "*"


def _rule_root(rule: ToolRule, roots: PolicyRoots) -> Path | None:
    """Return the canonical root a read/write rule anchors to, or None when scratch is unavailable."""
    if rule.root == "scratch":
        return roots.scratch

    return roots.worktree


def _normalize_pattern(pattern: str) -> str:
    """Return pattern with backslashes as forward slashes."""
    return pattern.replace("\\", "/")


def _path_spec(root: Path, pattern: str | None) -> str:
    """Return '//<abs-root>/<pattern>' with backslashes as '/' and '**' for an absent pattern."""
    return "/" + root.as_posix() + "/" + (_normalize_pattern(pattern) if pattern is not None else "**")


def _path_rule_error(rule: ToolRule, roots: PolicyRoots) -> str | None:
    """Return why a read/write rule cannot be anchored to its root, or None."""
    root = _rule_root(rule, roots)
    if root is None:
        return "a scratch rule requires a scratch root"

    if validate_rule_pattern(rule.capability, rule.pattern) is not None:
        return f"{rule.capability.value} pattern {rule.pattern!r} resolves outside its {rule.root or 'worktree'} root"

    spec = _path_spec(root, rule.pattern)
    if "," in spec:
        return f"{rule.capability.value} path {spec!r} contains a comma and cannot be preserved for Claude Code"

    return None


def _mcp_permission(pattern: str) -> str:
    """Return 'mcp__<server>__<tool>' for 'server/tool' and 'mcp__<server>' for 'server/*'."""
    server, tool = pattern.split("/", 1)
    if tool == _MCP_WILDCARD:
        return f"mcp__{server}"

    return f"mcp__{server}__{tool}"


def _mcp_rule_error(rule: ToolRule, *, deny: bool) -> str | None:
    """Return why an mcp rule cannot be preserved: every allow, a deny with no pattern, or a deny pattern containing a comma."""
    if not deny:
        return "mcp allow rules cannot be preserved because Claude Code runs without user MCP servers"

    if rule.pattern is None:
        return "an mcp deny rule must name a server"

    if "," in rule.pattern:
        return f"mcp pattern {rule.pattern!r} contains a comma and cannot be preserved for Claude Code"

    return None


def _rule_error(rule: ToolRule, roots: PolicyRoots, *, deny: bool) -> str | None:
    """Return why rule cannot be preserved for Claude Code, or None."""
    if rule.capability == ToolCapability.MCP:
        return _mcp_rule_error(rule, deny=deny)

    if rule.capability in _PATH_CAPABILITIES:
        return _path_rule_error(rule, roots)

    if rule.capability == ToolCapability.NETWORK and (rule.pattern or "").startswith(_NETWORK_WILDCARD_PREFIX):
        return f"network pattern {rule.pattern!r} cannot be preserved for Claude Code"

    return None


def _rule_permission(rule: ToolRule, roots: PolicyRoots) -> str:
    """Return the Claude permission string for a rule that passed _rule_error."""
    if rule.capability == ToolCapability.MCP and rule.pattern is not None:
        return _mcp_permission(rule.pattern)

    bare = _BARE_PERMISSIONS[rule.capability]
    if rule.capability in _PATH_CAPABILITIES and (root := _rule_root(rule, roots)) is not None:
        return f"{bare}({_path_spec(root, rule.pattern)})"

    if rule.pattern is None:
        return bare

    if rule.capability == ToolCapability.NETWORK:
        return f"{bare}(domain:{rule.pattern})"

    if rule.pattern.endswith(_SHELL_PREFIX_SUFFIX):
        return f"{bare}({rule.pattern.removesuffix(_SHELL_PREFIX_SUFFIX)}:*)"

    return f"{bare}({rule.pattern})"


def _first_error(rules: list[ToolRule], roots: PolicyRoots, *, deny: bool) -> str | None:
    """Return the first _rule_error across rules, or None."""
    for rule in rules:
        error = _rule_error(rule, roots, deny=deny)
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


def _scratch_flags(policy: ToolPolicy, roots: PolicyRoots) -> list[str]:
    """Return ['--add-dir', scratch] when an allow rule grants the scratch root, else [].

    Under dontAsk an added directory is readable without a rule, but edits there still need an Edit allow rule.
    """
    grants_scratch = any(rule.capability in _PATH_CAPABILITIES and rule.root == "scratch" for rule in policy.allow)
    if grants_scratch and roots.scratch is not None:
        return ["--add-dir", str(roots.scratch)]

    return []


def _flags(
    mode: str, tools: list[str] | None, allowed: list[str], denied: list[str], add_dirs: list[str]
) -> ClaudePolicyArgs:
    """Assemble the flag tuple: mode, the empty MCP set, then --tools when not None, the lists only when non-empty, add_dirs last."""
    flags = ["--permission-mode", mode, *_EMPTY_MCP_FLAGS]
    if tools is not None:
        flags += ["--tools", ",".join(tools)]

    if allowed:
        flags += ["--allowedTools", ",".join(allowed)]

    if denied:
        flags += ["--disallowedTools", ",".join(denied)]

    return ClaudePolicyArgs(flags=(*flags, *add_dirs))


def _render_allow_all(policy: ToolPolicy, roots: PolicyRoots) -> ClaudePolicyArgs | str:
    """Return bypassPermissions without denies, else dontAsk with every built-in granted and the denies rendered."""
    if not policy.deny:
        return _flags("bypassPermissions", None, [], [], [])

    error = _first_error(policy.deny, roots, deny=True)
    if error is not None:
        return error

    every_tool = [name for _, names in _TOOL_GROUPS for name in names]
    denied = _unique([_rule_permission(rule, roots) for rule in policy.deny])

    return _flags("dontAsk", every_tool, list(_ALL_BUILTINS_APPROVALS), denied, [])


def _render_restricted(policy: ToolPolicy, roots: PolicyRoots) -> ClaudePolicyArgs | str:
    """Return dontAsk flags for the allow and deny rules, or why a rule cannot be preserved."""
    error = _first_error(policy.allow, roots, deny=False) or _first_error(policy.deny, roots, deny=True)
    if error is not None:
        return error

    allowed = _unique([_rule_permission(rule, roots) for rule in policy.allow])
    denied = _unique([_rule_permission(rule, roots) for rule in policy.deny])

    return _flags("dontAsk", _tool_names(policy), allowed, denied, _scratch_flags(policy, roots))


def render_claude_policy(policy: ToolPolicy, roots: PolicyRoots) -> ClaudePolicyArgs | str:
    """Render policy into Claude Code flags, or return why a rule cannot be preserved."""
    if policy.allow_all:
        return _render_allow_all(policy, roots)

    return _render_restricted(policy, roots)
