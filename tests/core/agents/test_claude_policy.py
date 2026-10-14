"""Tests for rendering structured tool policies into Claude Code CLI flags."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from dovo.common.tool_policy import ToolCapability, ToolPolicy, ToolRule
from dovo.core.agents import PolicyRoots, default_tool_policy
from dovo.core.agents.claude_policy import ClaudePolicyArgs, render_claude_policy

_ALL_TOOLS = "Read,Grep,Glob,Edit,Write,NotebookEdit,Bash,WebFetch"
_READ_WRITE_TOOLS = "Read,Grep,Glob,Edit,Write,NotebookEdit"
_READ_WRITE_FLAGS = ("--permission-mode", "dontAsk", "--tools", _READ_WRITE_TOOLS, "--allowedTools", "Read,Edit")


def _roots(tmp_path: Path, *, scratch: bool = True) -> PolicyRoots:
    """Build policy roots under tmp_path, with a scratch root only when requested."""
    return PolicyRoots(worktree=tmp_path, scratch=tmp_path / "scratch" if scratch else None, control=None)


def _rule(
    capability: ToolCapability, pattern: str | None = None, root: Literal["worktree", "scratch"] | None = None
) -> ToolRule:
    """Build a tool rule for the capability, root and pattern."""
    return ToolRule(capability=capability, root=root, pattern=pattern)


class ClaudePolicyRenderTests:
    @pytest.mark.parametrize(
        ("root", "pattern"),
        [
            pytest.param(None, None, id="root-and-pattern-absent"),
            pytest.param("worktree", "**", id="explicit-worktree-glob"),
            pytest.param(None, "**", id="glob-only"),
            pytest.param("worktree", None, id="worktree-only"),
        ],
    )
    def test_worktree_read_write_renders_tools_and_bare_preapprovals(
        self, tmp_path: Path, root: Literal["worktree"] | None, pattern: str | None
    ) -> None:
        """[tier-1/unit] render_claude_policy: read+write whole-worktree grants (root None and 'worktree', pattern None and '**') return ClaudePolicyArgs(flags=('--permission-mode', 'dontAsk', '--tools', 'Read,Grep,Glob,Edit,Write,NotebookEdit', '--allowedTools', 'Read,Edit'))."""
        policy = ToolPolicy(
            allow=[_rule(ToolCapability.READ, pattern, root), _rule(ToolCapability.WRITE, pattern, root)]
        )

        assert render_claude_policy(policy, _roots(tmp_path, scratch=False)) == ClaudePolicyArgs(
            flags=_READ_WRITE_FLAGS
        )

    def test_empty_policy_emits_empty_tools_value_and_no_other_lists(self, tmp_path: Path) -> None:
        """[tier-1/unit] render_claude_policy: ToolPolicy() returns ClaudePolicyArgs(flags=('--permission-mode', 'dontAsk', '--tools', ''))."""
        assert render_claude_policy(ToolPolicy(), _roots(tmp_path)) == ClaudePolicyArgs(
            flags=("--permission-mode", "dontAsk", "--tools", "")
        )

    @pytest.mark.parametrize(
        ("pattern", "permission"),
        [
            pytest.param(None, "Bash", id="any-command"),
            pytest.param("git *", "Bash(git:*)", id="prefix"),
            pytest.param("git status", "Bash(git status)", id="exact"),
        ],
    )
    def test_shell_allow_maps_pattern_to_bash_permission(
        self, tmp_path: Path, pattern: str | None, permission: str
    ) -> None:
        """[tier-1/unit] render_claude_policy: shell allow None/'git *'/'git status' returns flags with '--tools' 'Bash' and '--allowedTools' 'Bash'/'Bash(git:*)'/'Bash(git status)'."""
        policy = ToolPolicy(allow=[_rule(ToolCapability.SHELL, pattern)])

        assert render_claude_policy(policy, _roots(tmp_path)) == ClaudePolicyArgs(
            flags=("--permission-mode", "dontAsk", "--tools", "Bash", "--allowedTools", permission)
        )

    @pytest.mark.parametrize(
        ("pattern", "permission"),
        [
            pytest.param(None, "WebFetch", id="any-host"),
            pytest.param("example.com", "WebFetch(domain:example.com)", id="literal-host"),
        ],
    )
    def test_network_allow_maps_host_to_webfetch_permission(
        self, tmp_path: Path, pattern: str | None, permission: str
    ) -> None:
        """[tier-1/unit] render_claude_policy: network allow None/'example.com' returns flags with '--tools' 'WebFetch' and '--allowedTools' 'WebFetch'/'WebFetch(domain:example.com)'."""
        policy = ToolPolicy(allow=[_rule(ToolCapability.NETWORK, pattern)])

        assert render_claude_policy(policy, _roots(tmp_path)) == ClaudePolicyArgs(
            flags=("--permission-mode", "dontAsk", "--tools", "WebFetch", "--allowedTools", permission)
        )

    def test_tools_follow_fixed_order_and_duplicate_permissions_collapse(self, tmp_path: Path) -> None:
        """[tier-1/unit] render_claude_policy: allows listed network, shell, write, read, read return '--tools' 'Read,Grep,Glob,Edit,Write,NotebookEdit,Bash,WebFetch' and '--allowedTools' with 'Read' exactly once."""
        policy = ToolPolicy(
            allow=[
                _rule(ToolCapability.NETWORK),
                _rule(ToolCapability.SHELL),
                _rule(ToolCapability.WRITE),
                _rule(ToolCapability.READ),
                _rule(ToolCapability.READ),
            ]
        )

        assert render_claude_policy(policy, _roots(tmp_path)) == ClaudePolicyArgs(
            flags=("--permission-mode", "dontAsk", "--tools", _ALL_TOOLS, "--allowedTools", "WebFetch,Bash,Edit,Read")
        )

    def test_allow_all_without_denies_returns_bypass_only(self, tmp_path: Path) -> None:
        """[tier-1/unit] render_claude_policy: ToolPolicy(allow_all=True) with a stray mcp allow returns ClaudePolicyArgs(flags=('--permission-mode', 'bypassPermissions')) with no '--tools' or '--allowedTools'."""
        policy = ToolPolicy(allow_all=True, allow=[_rule(ToolCapability.MCP, "server/tool")])

        assert render_claude_policy(policy, _roots(tmp_path)) == ClaudePolicyArgs(
            flags=("--permission-mode", "bypassPermissions")
        )

    def test_allow_all_with_denies_grants_every_builtin_and_renders_denies(self, tmp_path: Path) -> None:
        """[tier-1/unit] render_claude_policy: allow_all with shell deny 'git push *' returns flags ('--permission-mode', 'dontAsk', '--tools', <all eight names>, '--allowedTools', 'Read,Edit,Bash,WebFetch', '--disallowedTools', 'Bash(git push:*)')."""
        policy = ToolPolicy(allow_all=True, deny=[_rule(ToolCapability.SHELL, "git push *")])

        assert render_claude_policy(policy, _roots(tmp_path)) == ClaudePolicyArgs(
            flags=(
                "--permission-mode",
                "dontAsk",
                "--tools",
                _ALL_TOOLS,
                "--allowedTools",
                "Read,Edit,Bash,WebFetch",
                "--disallowedTools",
                "Bash(git push:*)",
            )
        )

    def test_deny_appears_in_disallowed_tools_and_is_not_removed_by_a_broader_allow(self, tmp_path: Path) -> None:
        """[tier-1/unit] render_claude_policy: shell allow 'git *' with shell deny 'git push *' and network deny None returns '--allowedTools' 'Bash(git:*)' and '--disallowedTools' 'Bash(git push:*),WebFetch'."""
        policy = ToolPolicy(
            allow=[_rule(ToolCapability.SHELL, "git *")],
            deny=[_rule(ToolCapability.SHELL, "git push *"), _rule(ToolCapability.NETWORK)],
        )

        assert render_claude_policy(policy, _roots(tmp_path)) == ClaudePolicyArgs(
            flags=(
                "--permission-mode",
                "dontAsk",
                "--tools",
                "Bash",
                "--allowedTools",
                "Bash(git:*)",
                "--disallowedTools",
                "Bash(git push:*),WebFetch",
            )
        )

    def test_default_policy_appends_add_dir_for_the_scratch_root(self, tmp_path: Path) -> None:
        """[tier-1/unit] render_claude_policy: default_tool_policy() with roots.scratch set returns the worktree read/write flags followed by ('--add-dir', str(roots.scratch)) as the last two elements."""
        roots = _roots(tmp_path)

        assert render_claude_policy(default_tool_policy(), roots) == ClaudePolicyArgs(
            flags=(*_READ_WRITE_FLAGS, "--add-dir", str(roots.scratch))
        )

    @pytest.mark.parametrize(
        ("policy", "scratch", "reason"),
        [
            pytest.param(
                ToolPolicy(allow=[_rule(ToolCapability.READ, "src/**")]),
                True,
                "read pattern 'src/**' is narrower than a whole root and cannot be preserved",
                id="read-narrow-pattern",
            ),
            pytest.param(
                ToolPolicy(allow=[_rule(ToolCapability.WRITE, "docs/**")]),
                True,
                "write pattern 'docs/**' is narrower than a whole root and cannot be preserved",
                id="write-narrow-pattern",
            ),
            pytest.param(
                ToolPolicy(deny=[_rule(ToolCapability.READ)]),
                True,
                "read deny rules cannot be preserved for Claude Code",
                id="read-deny",
            ),
            pytest.param(
                ToolPolicy(deny=[_rule(ToolCapability.WRITE)]),
                True,
                "write deny rules cannot be preserved for Claude Code",
                id="write-deny",
            ),
            pytest.param(
                ToolPolicy(allow=[_rule(ToolCapability.MCP, "server/tool")]),
                True,
                "mcp rules cannot be preserved for Claude Code",
                id="mcp-allow",
            ),
            pytest.param(
                ToolPolicy(deny=[_rule(ToolCapability.MCP, "server/tool")]),
                True,
                "mcp rules cannot be preserved for Claude Code",
                id="mcp-deny",
            ),
            pytest.param(
                ToolPolicy(allow=[_rule(ToolCapability.NETWORK, "*.example.com")]),
                True,
                "network pattern '*.example.com' cannot be preserved for Claude Code",
                id="network-allow-wildcard",
            ),
            pytest.param(
                ToolPolicy(deny=[_rule(ToolCapability.NETWORK, "*.example.com")]),
                True,
                "network pattern '*.example.com' cannot be preserved for Claude Code",
                id="network-deny-wildcard",
            ),
            pytest.param(
                ToolPolicy(allow=[_rule(ToolCapability.READ, root="scratch")]),
                True,
                "a read or write grant that omits the worktree cannot be preserved",
                id="scratch-only-read",
            ),
            pytest.param(
                ToolPolicy(
                    allow=[
                        _rule(ToolCapability.READ, root="worktree"),
                        _rule(ToolCapability.WRITE, root="worktree"),
                        _rule(ToolCapability.WRITE, root="scratch"),
                    ]
                ),
                True,
                "scratch must be granted to every available path tool or to none",
                id="scratch-granted-to-write-only",
            ),
            pytest.param(
                default_tool_policy(),
                False,
                "a scratch grant requires a scratch root",
                id="scratch-grant-without-scratch-root",
            ),
            pytest.param(
                ToolPolicy(allow_all=True, deny=[_rule(ToolCapability.READ)]),
                True,
                "read deny rules cannot be preserved for Claude Code",
                id="allow-all-with-read-deny",
            ),
        ],
    )
    def test_unsupported_rule_returns_reason_string(
        self, tmp_path: Path, policy: ToolPolicy, scratch: bool, reason: str
    ) -> None:
        """[tier-1/unit] render_claude_policy: read 'src/**', write 'docs/**', read deny, write deny, mcp allow, mcp deny, network allow '*.example.com', network deny '*.example.com', scratch-only read, scratch granted to write while read is worktree-only, scratch grant with roots.scratch None each return the str naming the rejected rule and no ClaudePolicyArgs."""
        assert render_claude_policy(policy, _roots(tmp_path, scratch=scratch)) == reason
