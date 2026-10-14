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


def _roots(tmp_path: Path, *, scratch: bool = True) -> PolicyRoots:
    """Build policy roots under tmp_path, with a scratch root only when requested."""
    return PolicyRoots(worktree=tmp_path, scratch=tmp_path / "scratch" if scratch else None, control=None)


def _spec(root: Path, pattern: str = "**") -> str:
    """Return the absolute-path permission spec for pattern under root, written as '//' plus the root without its leading '/'."""
    return "//" + str(root).lstrip("/") + "/" + pattern


def _read_write_flags(root: Path) -> tuple[str, ...]:
    """Return the flags for a whole-root read and write grant on root."""
    return (
        "--permission-mode",
        "dontAsk",
        "--tools",
        _READ_WRITE_TOOLS,
        "--allowedTools",
        f"Read({_spec(root)}),Edit({_spec(root)})",
    )


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
    def test_worktree_read_write_renders_tools_and_anchored_preapprovals(
        self, tmp_path: Path, root: Literal["worktree"] | None, pattern: str | None
    ) -> None:
        """[tier-1/unit] render_claude_policy: read+write whole-worktree grants (root None and 'worktree', pattern None and '**') return flags ending '--allowedTools', f'Read(//{root}/**),Edit(//{root}/**)' with tools 'Read,Grep,Glob,Edit,Write,NotebookEdit'."""
        policy = ToolPolicy(
            allow=[_rule(ToolCapability.READ, pattern, root), _rule(ToolCapability.WRITE, pattern, root)]
        )

        assert render_claude_policy(policy, _roots(tmp_path, scratch=False)) == ClaudePolicyArgs(
            flags=_read_write_flags(tmp_path)
        )

    def test_narrowed_patterns_anchor_to_their_root(self, tmp_path: Path) -> None:
        """[tier-1/unit] render_claude_policy: read 'src/**', write 'docs/*.md' and write 'a\\b' on worktree return '--allowedTools' 'Read(//<root>/src/**),Edit(//<root>/docs/*.md),Edit(//<root>/a/b)'."""
        policy = ToolPolicy(
            allow=[
                _rule(ToolCapability.READ, "src/**"),
                _rule(ToolCapability.WRITE, "docs/*.md"),
                _rule(ToolCapability.WRITE, "a\\b"),
            ]
        )

        assert render_claude_policy(policy, _roots(tmp_path, scratch=False)) == ClaudePolicyArgs(
            flags=(
                "--permission-mode",
                "dontAsk",
                "--tools",
                _READ_WRITE_TOOLS,
                "--allowedTools",
                f"Read({_spec(tmp_path, 'src/**')}),Edit({_spec(tmp_path, 'docs/*.md')}),Edit({_spec(tmp_path, 'a/b')})",
            )
        )

    def test_read_and_write_denies_render_beside_overlapping_allows(self, tmp_path: Path) -> None:
        """[tier-1/unit] render_claude_policy: read allow None + read deny 'secrets/**' + write deny None return '--allowedTools' 'Read(//<root>/**)' and '--disallowedTools' 'Read(//<root>/secrets/**),Edit(//<root>/**)'."""
        policy = ToolPolicy(
            allow=[_rule(ToolCapability.READ)],
            deny=[_rule(ToolCapability.READ, "secrets/**"), _rule(ToolCapability.WRITE)],
        )

        assert render_claude_policy(policy, _roots(tmp_path, scratch=False)) == ClaudePolicyArgs(
            flags=(
                "--permission-mode",
                "dontAsk",
                "--tools",
                "Read,Grep,Glob",
                "--allowedTools",
                f"Read({_spec(tmp_path)})",
                "--disallowedTools",
                f"Read({_spec(tmp_path, 'secrets/**')}),Edit({_spec(tmp_path)})",
            )
        )

    def test_deny_only_path_rules_expose_no_tools(self, tmp_path: Path) -> None:
        """[tier-1/unit] render_claude_policy: write deny None alone returns flags ('--permission-mode', 'dontAsk', '--tools', '', '--disallowedTools', 'Edit(//<root>/**)') with no '--allowedTools'."""
        policy = ToolPolicy(deny=[_rule(ToolCapability.WRITE)])

        assert render_claude_policy(policy, _roots(tmp_path)) == ClaudePolicyArgs(
            flags=(
                "--permission-mode",
                "dontAsk",
                "--tools",
                "",
                "--disallowedTools",
                f"Edit({_spec(tmp_path)})",
            )
        )

    def test_allow_all_anchors_path_denies_and_adds_no_directory(self, tmp_path: Path) -> None:
        """[tier-1/unit] render_claude_policy: allow_all with read deny on scratch 'private/**' returns dontAsk, all eight tools, 'Read,Edit,Bash,WebFetch', '--disallowedTools' 'Read(//<scratch>/private/**)' and no '--add-dir'."""
        roots = _roots(tmp_path)
        assert roots.scratch is not None
        policy = ToolPolicy(allow_all=True, deny=[_rule(ToolCapability.READ, "private/**", "scratch")])

        assert render_claude_policy(policy, roots) == ClaudePolicyArgs(
            flags=(
                "--permission-mode",
                "dontAsk",
                "--tools",
                _ALL_TOOLS,
                "--allowedTools",
                "Read,Edit,Bash,WebFetch",
                "--disallowedTools",
                f"Read({_spec(roots.scratch, 'private/**')})",
            )
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
        """[tier-1/unit] render_claude_policy: allows listed network, shell, write, read, read return '--tools' 'Read,Grep,Glob,Edit,Write,NotebookEdit,Bash,WebFetch' and '--allowedTools' with 'Read(//<root>/**)' exactly once."""
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
            flags=(
                "--permission-mode",
                "dontAsk",
                "--tools",
                _ALL_TOOLS,
                "--allowedTools",
                f"WebFetch,Bash,Edit({_spec(tmp_path)}),Read({_spec(tmp_path)})",
            )
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

    def test_default_policy_appends_add_dir_and_anchors_both_roots(self, tmp_path: Path) -> None:
        """[tier-1/unit] render_claude_policy: default_tool_policy() with roots.scratch set returns '--allowedTools' 'Read(//<root>/**),Edit(//<root>/**),Read(//<scratch>/**),Edit(//<scratch>/**)' followed last by ('--add-dir', str(roots.scratch))."""
        roots = _roots(tmp_path)
        assert roots.scratch is not None
        allowed = ",".join(f"{tool}({_spec(root)})" for root in (tmp_path, roots.scratch) for tool in ("Read", "Edit"))

        assert render_claude_policy(default_tool_policy(), roots) == ClaudePolicyArgs(
            flags=(
                "--permission-mode",
                "dontAsk",
                "--tools",
                _READ_WRITE_TOOLS,
                "--allowedTools",
                allowed,
                "--add-dir",
                str(roots.scratch),
            )
        )

    def test_scratch_only_read_is_supported_and_adds_the_directory(self, tmp_path: Path) -> None:
        """[tier-1/unit] render_claude_policy: read allow on scratch with no worktree grant returns '--tools' 'Read,Grep,Glob', '--allowedTools' 'Read(//<scratch>/**)' and ('--add-dir', str(roots.scratch))."""
        roots = _roots(tmp_path)
        assert roots.scratch is not None
        policy = ToolPolicy(allow=[_rule(ToolCapability.READ, root="scratch")])

        assert render_claude_policy(policy, roots) == ClaudePolicyArgs(
            flags=(
                "--permission-mode",
                "dontAsk",
                "--tools",
                "Read,Grep,Glob",
                "--allowedTools",
                f"Read({_spec(roots.scratch)})",
                "--add-dir",
                str(roots.scratch),
            )
        )

    def test_control_directory_is_never_rendered(self, tmp_path: Path) -> None:
        """[tier-1/unit] render_claude_policy: default_tool_policy() with roots.control set returns flags none of which contain str(roots.control)."""
        control = tmp_path / "control"
        roots = PolicyRoots(worktree=tmp_path / "work", scratch=tmp_path / "scratch", control=control)

        result = render_claude_policy(default_tool_policy(), roots)

        assert isinstance(result, ClaudePolicyArgs)
        assert not any(str(control) in flag for flag in result.flags)

    @pytest.mark.parametrize(
        ("policy", "scratch", "reason"),
        [
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
                default_tool_policy(),
                False,
                "a scratch rule requires a scratch root",
                id="scratch-grant-without-scratch-root",
            ),
            pytest.param(
                ToolPolicy(deny=[_rule(ToolCapability.READ, root="scratch")]),
                False,
                "a scratch rule requires a scratch root",
                id="scratch-deny-without-scratch-root",
            ),
            pytest.param(
                ToolPolicy.model_construct(
                    allow=[ToolRule.model_construct(capability=ToolCapability.READ, root=None, pattern="../x")]
                ),
                True,
                "read pattern '../x' resolves outside its worktree root",
                id="read-pattern-escapes-root",
            ),
            pytest.param(
                ToolPolicy.model_construct(
                    allow=[ToolRule.model_construct(capability=ToolCapability.WRITE, root=None, pattern="/etc/x")]
                ),
                True,
                "write pattern '/etc/x' resolves outside its worktree root",
                id="write-pattern-absolute",
            ),
            pytest.param(
                ToolPolicy.model_construct(
                    allow=[ToolRule.model_construct(capability=ToolCapability.READ, root="scratch", pattern="C:x")]
                ),
                True,
                "read pattern 'C:x' resolves outside its scratch root",
                id="read-pattern-drive-letter",
            ),
            pytest.param(
                ToolPolicy.model_construct(
                    deny=[ToolRule.model_construct(capability=ToolCapability.READ, root=None, pattern="a\\..\\b")]
                ),
                True,
                "read pattern 'a\\\\..\\\\b' resolves outside its worktree root",
                id="read-pattern-backslash-parent",
            ),
        ],
    )
    def test_unsupported_rule_returns_reason_string(
        self, tmp_path: Path, policy: ToolPolicy, scratch: bool, reason: str
    ) -> None:
        """[tier-1/unit] render_claude_policy: mcp allow, mcp deny, network '*.example.com' allow and deny, default_tool_policy() with roots.scratch None ('a scratch rule requires a scratch root'), scratch read deny with roots.scratch None (same reason), and model_construct patterns '../x', '/etc/x', 'C:x' and 'a\\..\\b' ('<capability> pattern '<pattern>' resolves outside its <root> root') each return the exact str and no ClaudePolicyArgs."""
        assert render_claude_policy(policy, _roots(tmp_path, scratch=scratch)) == reason

    @pytest.mark.parametrize(
        ("worktree_name", "pattern", "reason_path"),
        [
            pytest.param("work", "src/{a,b}/**", "src/{a,b}/**", id="comma-in-pattern"),
            pytest.param("a,b", None, "**", id="comma-in-root"),
        ],
    )
    def test_comma_in_path_spec_returns_reason_string(
        self, tmp_path: Path, worktree_name: str, pattern: str | None, reason_path: str
    ) -> None:
        """[tier-1/unit] render_claude_policy: a read allow whose pattern 'src/{a,b}/**' or whose worktree root contains a comma returns "read path '<spec>' contains a comma and cannot be preserved for Claude Code" and no ClaudePolicyArgs."""
        worktree = tmp_path / worktree_name
        roots = PolicyRoots(worktree=worktree, scratch=None, control=None)
        policy = ToolPolicy(allow=[_rule(ToolCapability.READ, pattern)])

        assert (
            render_claude_policy(policy, roots)
            == f"read path {_spec(worktree, reason_path)!r} contains a comma and cannot be preserved for Claude Code"
        )

    def test_scratch_read_allow_does_not_widen_worktree_write_allow(self, tmp_path: Path) -> None:
        """[tier-1/unit] render_claude_policy: write allow on the worktree plus read allow on scratch returns '--allowedTools' 'Edit(//<root>/**),Read(//<scratch>/**)' with no Edit spec for scratch, followed by ('--add-dir', str(roots.scratch))."""
        roots = _roots(tmp_path)
        assert roots.scratch is not None
        policy = ToolPolicy(
            allow=[_rule(ToolCapability.WRITE, root="worktree"), _rule(ToolCapability.READ, root="scratch")]
        )

        assert render_claude_policy(policy, roots) == ClaudePolicyArgs(
            flags=(
                "--permission-mode",
                "dontAsk",
                "--tools",
                _READ_WRITE_TOOLS,
                "--allowedTools",
                f"Edit({_spec(tmp_path)}),Read({_spec(roots.scratch)})",
                "--add-dir",
                str(roots.scratch),
            )
        )
