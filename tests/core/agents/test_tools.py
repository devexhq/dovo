"""Contract tests for the tool policy default and step-boundary resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.common.tool_policy import ToolCapability, ToolPolicy, ToolRule
from dovo.core.agents import AgentInvocationContext, ProviderSpec
from dovo.core.agents.copilot import CopilotAgentAdapter
from dovo.core.agents.tools import (
    PolicyRoots,
    check_policy_support,
    default_tool_policy,
    policy_rules,
    requests_scratch,
    resolve_policy_roots,
    resolve_tool_policy,
    tool_policy_unsupported_message,
)


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


def _spec(*, supports_tool_policy: bool) -> ProviderSpec:
    return ProviderSpec(
        token="unit-test",
        credential_envs=(),
        requires_model=False,
        supports_tool_policy=supports_tool_policy,
        supports_os_sandbox=False,
        build=CopilotAgentAdapter,
    )


class CheckPolicySupportTests:
    @pytest.mark.parametrize(
        ("supports", "policy", "rejected"),
        [
            pytest.param(True, ToolPolicy(), False, id="capable-provider-accepts-any-policy"),
            pytest.param(False, default_tool_policy(), True, id="incapable-provider-rejects-default"),
            pytest.param(False, ToolPolicy(), True, id="incapable-provider-rejects-empty"),
            pytest.param(False, ToolPolicy(allow_all=True), False, id="incapable-provider-accepts-unrestricted"),
            pytest.param(
                False,
                ToolPolicy(allow=[ToolRule(capability=ToolCapability.SHELL)], allow_all=True),
                False,
                id="allow-rules-do-not-restrict-allow-all",
            ),
            pytest.param(
                False,
                ToolPolicy(deny=[ToolRule(capability=ToolCapability.SHELL, pattern="rm *")], allow_all=True),
                True,
                id="incapable-provider-rejects-allow-all-with-deny",
            ),
        ],
    )
    def test_incapable_provider_accepts_only_unrestricted_allow_all(
        self, supports: bool, policy: ToolPolicy, rejected: bool
    ) -> None:
        """[tier-1/unit] check_policy_support: returns the fixed unsupported message for the token when the spec cannot enforce a restrictive policy, else None."""
        result = check_policy_support(_spec(supports_tool_policy=supports), policy)

        assert result == (tool_policy_unsupported_message("unit-test") if rejected else None)

    def test_message_names_the_provider_token_and_code(self) -> None:
        """[tier-1/unit] tool_policy_unsupported_message: embeds the token and AGENT_TOOL_POLICY_UNSUPPORTED."""
        message = tool_policy_unsupported_message("copilot")

        assert message.startswith("Agent provider 'copilot' cannot enforce the requested tool policy")
        assert "(AGENT_TOOL_POLICY_UNSUPPORTED)" in message


class PolicyRuleHelpersTests:
    def test_rules_list_allows_before_denies_and_scratch_is_detected_in_either(self) -> None:
        """[tier-1/unit] policy_rules / requests_scratch: allow rules precede deny rules and a scratch root in either list counts as requesting scratch."""
        allow = ToolRule(capability=ToolCapability.READ)
        deny = ToolRule(capability=ToolCapability.WRITE, root="scratch")

        assert policy_rules(ToolPolicy(allow=[allow], deny=[deny])) == [allow, deny]
        assert requests_scratch(ToolPolicy(deny=[deny])) is True
        assert requests_scratch(ToolPolicy(allow=[allow])) is False


def _dirs(tmp_path: Path) -> tuple[Path, Path, Path]:
    worktree, scratch, control = tmp_path / "wt", tmp_path / "inv" / "scratch", tmp_path / "inv" / "control"
    for path in (worktree, scratch, control):
        path.mkdir(parents=True)
    return worktree.resolve(), scratch.resolve(), control.resolve()


def _context(scratch: Path, control: Path) -> AgentInvocationContext:
    return AgentInvocationContext(invocation_id="a" * 32, scratch_path=scratch, control_path=control)


class ResolvePolicyRootsTests:
    def test_default_policy_with_invocation_returns_canonical_worktree_and_scratch(self, tmp_path: Path) -> None:
        """[tier-1/unit] resolve_policy_roots: returns PolicyRoots(worktree=resolved worktree, scratch=resolved scratch, control=resolved control)."""
        worktree, scratch, control = _dirs(tmp_path)

        roots = resolve_policy_roots(default_tool_policy(), worktree, _context(scratch, control))

        assert roots == PolicyRoots(worktree=worktree, scratch=scratch, control=control)

    def test_worktree_only_policy_needs_no_invocation(self, tmp_path: Path) -> None:
        """[tier-1/unit] resolve_policy_roots: worktree-only policy with invocation=None returns PolicyRoots(scratch=None, control=None)."""
        worktree, _, _ = _dirs(tmp_path)
        policy = ToolPolicy(allow=[ToolRule(capability=ToolCapability.READ)])

        assert resolve_policy_roots(policy, worktree, None) == PolicyRoots(
            worktree=worktree, scratch=None, control=None
        )

    @pytest.mark.parametrize(
        "case",
        [
            pytest.param("missing-context", id="scratch-rule-without-invocation"),
            pytest.param("symlinked-scratch", id="symlink-escape"),
            pytest.param("scratch-equals-control", id="control-overlap"),
            pytest.param("scratch-inside-worktree", id="worktree-overlap"),
            pytest.param("scratch-removed", id="vanished-scratch"),
            pytest.param("control-inside-worktree", id="control-worktree-overlap"),
            pytest.param("worktree-removed", id="vanished-worktree"),
        ],
    )
    def test_unsafe_or_missing_scratch_returns_error_string(self, tmp_path: Path, case: str) -> None:
        """[tier-1/unit] resolve_policy_roots: each case returns a non-empty str and never creates a directory."""
        worktree, scratch, control = _dirs(tmp_path)
        invocation: AgentInvocationContext | None = _context(scratch, control)
        if case == "missing-context":
            invocation = None
        elif case == "symlinked-scratch":
            link = tmp_path / "link"
            link.symlink_to(scratch)
            invocation = _context(link, control)
        elif case == "scratch-equals-control":
            invocation = _context(control, control)
        elif case == "scratch-inside-worktree":
            inside = worktree / "scratch"
            inside.mkdir()
            invocation = _context(inside, control)
        elif case == "scratch-removed":
            scratch.rmdir()
        elif case == "control-inside-worktree":
            inside = worktree / "control"
            inside.mkdir()
            invocation = _context(scratch, inside)
        else:
            worktree = tmp_path / "gone"
        before = sorted(tmp_path.rglob("*"))

        result = resolve_policy_roots(default_tool_policy(), worktree, invocation)

        assert isinstance(result, str)
        assert result
        assert sorted(tmp_path.rglob("*")) == before
