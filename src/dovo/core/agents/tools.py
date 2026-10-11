"""Tool policy defaults and step-boundary resolution for agent attempts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from dovo.common.tool_policy import ToolCapability, ToolPolicy, ToolRule
from dovo.core.agents.models import AgentInvocationContext

TOOL_POLICY_UNSUPPORTED_CODE: Final[str] = "AGENT_TOOL_POLICY_UNSUPPORTED"


@dataclass(frozen=True)
class PolicyRoots:
    """Canonical filesystem roots a policy's read/write rules resolve against."""

    worktree: Path
    scratch: Path | None
    control: Path | None


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


def tool_policy_unsupported_message(token: str) -> str:
    """Return the fixed AGENT_TOOL_POLICY_UNSUPPORTED diagnostic naming the provider token."""
    return (
        f"Agent provider '{token}' cannot enforce the requested tool policy ({TOOL_POLICY_UNSUPPORTED_CODE}). "
        "Fix: choose a policy-capable provider or an explicitly authorized policy it can enforce."
    )


class _PolicyDescriptor(Protocol):
    """Structural view of ``ProviderSpec`` so this module need not import ``base.py``, which imports it."""

    @property
    def token(self) -> str:
        """Return the provider token."""
        ...

    @property
    def supports_tool_policy(self) -> bool:
        """Return True when the provider enforces the shared tool policy."""
        ...


def check_policy_support(spec: _PolicyDescriptor, policy: ToolPolicy) -> str | None:
    """Return the unsupported message when spec cannot enforce policy and policy is not unrestricted allow_all, else None."""
    if spec.supports_tool_policy or (policy.allow_all and not policy.deny):
        return None

    return tool_policy_unsupported_message(spec.token)


def policy_rules(policy: ToolPolicy) -> list[ToolRule]:
    """Return allow rules followed by deny rules."""
    return [*policy.allow, *policy.deny]


def requests_scratch(policy: ToolPolicy) -> bool:
    """Return True when any allow or deny rule selects the scratch root."""
    return any(rule.root == "scratch" for rule in policy_rules(policy))


def _overlaps(first: Path, second: Path) -> bool:
    """Return True when the paths are equal or one contains the other."""
    return first == second or first in second.parents or second in first.parents


def _resolve_private_dir(label: str, supplied: Path) -> Path | str:
    """Return the canonical path of an existing directory that must equal its supplied path, else why not."""
    try:
        resolved = supplied.resolve(strict=True)
    except OSError as exc:
        return f"{label} path cannot be resolved: {exc}"

    if resolved != supplied:
        return f"{label} path {supplied} resolves outside its allocated location to {resolved}"

    return resolved


def resolve_policy_roots(
    policy: ToolPolicy, worktree_path: Path, invocation: AgentInvocationContext | None
) -> PolicyRoots | str:
    """Return the canonical roots the policy needs, or why they are unavailable or unsafe.

    Scratch and control must already exist and are never created here; neither may equal, contain, or sit inside the other or the worktree.
    """
    try:
        worktree = worktree_path.resolve(strict=True)
    except OSError as exc:
        return f"worktree path cannot be resolved: {exc}"

    if invocation is None:
        if requests_scratch(policy):
            return "a scratch rule requires an invocation context, but none was supplied"
        return PolicyRoots(worktree=worktree, scratch=None, control=None)

    control = _resolve_private_dir("control", invocation.control_path)
    if isinstance(control, str):
        return control

    if _overlaps(control, worktree):
        return "control path overlaps the worktree"

    if not requests_scratch(policy):
        return PolicyRoots(worktree=worktree, scratch=None, control=control)

    scratch = _resolve_private_dir("scratch", invocation.scratch_path)
    if isinstance(scratch, str):
        return scratch

    if _overlaps(scratch, control) or _overlaps(scratch, worktree):
        return "scratch path overlaps the control path or the worktree"

    return PolicyRoots(worktree=worktree, scratch=scratch, control=control)
