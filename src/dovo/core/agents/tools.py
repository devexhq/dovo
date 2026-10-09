"""Tool policy defaults and step-boundary resolution for agent attempts."""

from __future__ import annotations

from dovo.common.tool_policy import ToolCapability, ToolPolicy, ToolRule


def default_tool_policy() -> ToolPolicy:
    """Return the safe default: read/write `**` on the worktree and scratch roots, no denies, no shell/network/MCP."""
    return ToolPolicy(
        allow=[
            ToolRule(capability=capability, root=root, pattern="**")
            for root in ("worktree", "scratch")
            for capability in (ToolCapability.READ, ToolCapability.WRITE)
        ],
        deny=[],
        allow_all=False,
    )


def resolve_tool_policy(step_policy: ToolPolicy | None, configured: ToolPolicy) -> ToolPolicy:
    """Return the step's explicit policy when declared, else the configured default; policies are never merged."""
    return step_policy if step_policy is not None else configured
