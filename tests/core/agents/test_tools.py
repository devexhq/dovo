"""Contract tests for the tool policy default and step-boundary resolution."""

from __future__ import annotations

import pytest

from dovo.common.tool_policy import ToolCapability, ToolPolicy, ToolRule
from dovo.core.agents.tools import default_tool_policy, resolve_tool_policy


class DefaultToolPolicyTests:
    def test_default_policy_grants_read_write_on_both_roots_only(self) -> None:
        """[tier-1/unit] default_tool_policy: allow == the four read/write '**' rules for roots worktree and scratch; deny == []; allow_all is False; no shell/network/mcp rule."""
        policy = default_tool_policy()

        assert policy == ToolPolicy(
            allow=[
                ToolRule(capability=ToolCapability.READ, root="worktree", pattern="**"),
                ToolRule(capability=ToolCapability.WRITE, root="worktree", pattern="**"),
                ToolRule(capability=ToolCapability.READ, root="scratch", pattern="**"),
                ToolRule(capability=ToolCapability.WRITE, root="scratch", pattern="**"),
            ],
            deny=[],
            allow_all=False,
        )

    def test_default_policy_returns_a_fresh_object_per_call(self) -> None:
        """[tier-1/unit] default_tool_policy: mutating one returned policy's allow list leaves the next call's policy unchanged."""
        first = default_tool_policy()
        first.allow.clear()

        assert len(default_tool_policy().allow) == 4


class ResolveToolPolicyTests:
    @pytest.mark.parametrize(
        ("step_policy", "expected"),
        [
            pytest.param(None, "configured", id="omitted-inherits-config"),
            pytest.param(ToolPolicy(), "empty", id="explicit-empty-grants-nothing"),
            pytest.param(
                ToolPolicy(allow=[ToolRule(capability=ToolCapability.SHELL)]),
                "step",
                id="step-replaces-and-drops-config-deny",
            ),
        ],
    )
    def test_step_policy_replaces_config_without_accumulating_denies(
        self, step_policy: ToolPolicy | None, expected: str
    ) -> None:
        """[tier-1/unit] resolve_tool_policy: omitted -> configured object; explicit -> that exact object with no config allow/deny added."""
        configured = ToolPolicy(
            allow=[ToolRule(capability=ToolCapability.READ, pattern="src/**")],
            deny=[ToolRule(capability=ToolCapability.WRITE, pattern=".dovo/**")],
        )

        resolved = resolve_tool_policy(step_policy, configured)

        assert resolved == (configured if expected == "configured" else step_policy)
        if expected != "configured":
            assert resolved.deny == []
