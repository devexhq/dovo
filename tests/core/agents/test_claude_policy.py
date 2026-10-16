"""Tests for rendering structured tool policies into Claude Code CLI flags."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import pytest

from dovo.common.mcp_servers import McpServerDefinition
from dovo.common.tool_policy import ToolCapability, ToolPolicy, ToolRule
from dovo.core.agents import PolicyRoots, default_tool_policy
from dovo.core.agents.claude_policy import ClaudePolicyArgs, McpConfigFile, render_claude_policy

_ALL_TOOLS = "Read,Grep,Glob,Edit,Write,NotebookEdit,Bash,WebFetch"
_READ_WRITE_TOOLS = "Read,Grep,Glob,Edit,Write,NotebookEdit"
_MCP_FLAGS = ("--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}')
_GITHUB = McpServerDefinition(command="npx", args=["-y", "pkg"], env={"TOKEN": "${GITHUB_TOKEN}"})
_GITHUB_CONFIG = '{"mcpServers":{"github":{"command":"npx","args":["-y","pkg"],"env":{"TOKEN":"${GITHUB_TOKEN}"}}}}'


def _roots(tmp_path: Path, *, scratch: bool = True) -> PolicyRoots:
    """Build policy roots under tmp_path, with a scratch root only when requested."""
    return PolicyRoots(worktree=tmp_path, scratch=tmp_path / "scratch" if scratch else None, control=None)


def _control_roots(tmp_path: Path) -> PolicyRoots:
    """Build policy roots under tmp_path with a control root at tmp_path/control."""
    return PolicyRoots(worktree=tmp_path, scratch=tmp_path / "scratch", control=tmp_path / "control")


def _render(
    policy: ToolPolicy,
    roots: PolicyRoots,
    *,
    configured: Mapping[str, McpServerDefinition] | None = None,
    user: Mapping[str, dict[str, object]] | None = None,
) -> ClaudePolicyArgs | str:
    """Render policy with no MCP servers defined unless configured or user entries are given."""
    return render_claude_policy(policy, roots, configured=configured or {}, user=user or {})


def _spec(root: Path, pattern: str = "**") -> str:
    """Return the permission spec for pattern under root: '//' plus the root without its leading '/'."""
    return "//" + str(root).lstrip("/") + "/" + pattern


def _read_write_flags(root: Path) -> tuple[str, ...]:
    """Return the flags for a whole-root read and write grant on root."""
    return (
        "--permission-mode",
        "dontAsk",
        *_MCP_FLAGS,
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
        policy = ToolPolicy(
            allow=[_rule(ToolCapability.READ, pattern, root), _rule(ToolCapability.WRITE, pattern, root)]
        )

        assert _render(policy, _roots(tmp_path, scratch=False)) == ClaudePolicyArgs(flags=_read_write_flags(tmp_path))

    def test_narrowed_patterns_anchor_to_their_root(self, tmp_path: Path) -> None:
        policy = ToolPolicy(
            allow=[
                _rule(ToolCapability.READ, "src/**"),
                _rule(ToolCapability.WRITE, "docs/*.md"),
                _rule(ToolCapability.WRITE, "a\\b"),
            ]
        )

        assert _render(policy, _roots(tmp_path, scratch=False)) == ClaudePolicyArgs(
            flags=(
                "--permission-mode",
                "dontAsk",
                *_MCP_FLAGS,
                "--tools",
                _READ_WRITE_TOOLS,
                "--allowedTools",
                f"Read({_spec(tmp_path, 'src/**')}),Edit({_spec(tmp_path, 'docs/*.md')}),Edit({_spec(tmp_path, 'a/b')})",
            )
        )

    def test_read_and_write_denies_render_beside_overlapping_allows(self, tmp_path: Path) -> None:
        policy = ToolPolicy(
            allow=[_rule(ToolCapability.READ)],
            deny=[_rule(ToolCapability.READ, "secrets/**"), _rule(ToolCapability.WRITE)],
        )

        assert _render(policy, _roots(tmp_path, scratch=False)) == ClaudePolicyArgs(
            flags=(
                "--permission-mode",
                "dontAsk",
                *_MCP_FLAGS,
                "--tools",
                "Read,Grep,Glob",
                "--allowedTools",
                f"Read({_spec(tmp_path)})",
                "--disallowedTools",
                f"Read({_spec(tmp_path, 'secrets/**')}),Edit({_spec(tmp_path)})",
            )
        )

    def test_deny_only_path_rules_expose_no_tools(self, tmp_path: Path) -> None:
        policy = ToolPolicy(deny=[_rule(ToolCapability.WRITE)])

        assert _render(policy, _roots(tmp_path)) == ClaudePolicyArgs(
            flags=(
                "--permission-mode",
                "dontAsk",
                *_MCP_FLAGS,
                "--tools",
                "",
                "--disallowedTools",
                f"Edit({_spec(tmp_path)})",
            )
        )

    def test_allow_all_anchors_path_denies_and_adds_no_directory(self, tmp_path: Path) -> None:
        roots = _roots(tmp_path)
        assert roots.scratch is not None
        policy = ToolPolicy(allow_all=True, deny=[_rule(ToolCapability.READ, "private/**", "scratch")])

        assert _render(policy, roots) == ClaudePolicyArgs(
            flags=(
                "--permission-mode",
                "dontAsk",
                *_MCP_FLAGS,
                "--tools",
                _ALL_TOOLS,
                "--allowedTools",
                "Read,Edit,Bash,WebFetch",
                "--disallowedTools",
                f"Read({_spec(roots.scratch, 'private/**')})",
            )
        )

    def test_default_policy_carries_empty_mcp_set_after_permission_mode(self, tmp_path: Path) -> None:
        result = _render(default_tool_policy(), _roots(tmp_path))

        assert isinstance(result, ClaudePolicyArgs)
        assert result.flags[:5] == (
            "--permission-mode",
            "dontAsk",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
        )

    @pytest.mark.parametrize(
        ("pattern", "denied"),
        [
            pytest.param("github/create_issue", "mcp__github__create_issue", id="server-tool"),
            pytest.param("github/*", "mcp__github", id="server-wildcard"),
        ],
    )
    def test_mcp_deny_renders_claude_tool_name(self, tmp_path: Path, pattern: str, denied: str) -> None:
        policy = ToolPolicy(deny=[_rule(ToolCapability.MCP, pattern)])

        assert _render(policy, _roots(tmp_path)) == ClaudePolicyArgs(
            flags=("--permission-mode", "dontAsk", *_MCP_FLAGS, "--tools", "", "--disallowedTools", denied)
        )

    def test_mcp_deny_composes_with_other_denies_in_rule_order(self, tmp_path: Path) -> None:
        policy = ToolPolicy(
            deny=[
                _rule(ToolCapability.SHELL, "git push *"),
                _rule(ToolCapability.MCP, "github/*"),
                _rule(ToolCapability.MCP, "github/*"),
            ]
        )

        assert _render(policy, _roots(tmp_path)) == ClaudePolicyArgs(
            flags=(
                "--permission-mode",
                "dontAsk",
                *_MCP_FLAGS,
                "--tools",
                "",
                "--disallowedTools",
                "Bash(git push:*),mcp__github",
            )
        )

    def test_empty_policy_emits_empty_tools_value_and_no_other_lists(self, tmp_path: Path) -> None:
        assert _render(ToolPolicy(), _roots(tmp_path)) == ClaudePolicyArgs(
            flags=("--permission-mode", "dontAsk", *_MCP_FLAGS, "--tools", "")
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
        policy = ToolPolicy(allow=[_rule(ToolCapability.SHELL, pattern)])

        assert _render(policy, _roots(tmp_path)) == ClaudePolicyArgs(
            flags=("--permission-mode", "dontAsk", *_MCP_FLAGS, "--tools", "Bash", "--allowedTools", permission)
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
        policy = ToolPolicy(allow=[_rule(ToolCapability.NETWORK, pattern)])

        assert _render(policy, _roots(tmp_path)) == ClaudePolicyArgs(
            flags=("--permission-mode", "dontAsk", *_MCP_FLAGS, "--tools", "WebFetch", "--allowedTools", permission)
        )

    def test_tools_follow_fixed_order_and_duplicate_permissions_collapse(self, tmp_path: Path) -> None:
        policy = ToolPolicy(
            allow=[
                _rule(ToolCapability.NETWORK),
                _rule(ToolCapability.SHELL),
                _rule(ToolCapability.WRITE),
                _rule(ToolCapability.READ),
                _rule(ToolCapability.READ),
            ]
        )

        assert _render(policy, _roots(tmp_path)) == ClaudePolicyArgs(
            flags=(
                "--permission-mode",
                "dontAsk",
                *_MCP_FLAGS,
                "--tools",
                _ALL_TOOLS,
                "--allowedTools",
                f"WebFetch,Bash,Edit({_spec(tmp_path)}),Read({_spec(tmp_path)})",
            )
        )

    def test_allow_all_with_mcp_allow_loads_named_server(self, tmp_path: Path) -> None:
        roots = _control_roots(tmp_path)
        policy = ToolPolicy(allow_all=True, allow=[_rule(ToolCapability.MCP, "github/*")])

        assert _render(policy, roots, configured={"github": _GITHUB, "other": _GITHUB}) == ClaudePolicyArgs(
            flags=(
                "--permission-mode",
                "bypassPermissions",
                "--strict-mcp-config",
                "--mcp-config",
                str(tmp_path / "control" / "mcp-config.json"),
            ),
            mcp_config=McpConfigFile(path=tmp_path / "control" / "mcp-config.json", content=_GITHUB_CONFIG),
        )

    def test_allow_all_with_denies_and_mcp_allow_grants_server_through_allowed_tools(self, tmp_path: Path) -> None:
        roots = _control_roots(tmp_path)
        policy = ToolPolicy(
            allow_all=True,
            allow=[_rule(ToolCapability.MCP, "github/create_issue")],
            deny=[_rule(ToolCapability.SHELL, "git push *")],
        )

        result = _render(policy, roots, configured={"github": _GITHUB})

        assert isinstance(result, ClaudePolicyArgs)
        assert result.flags[result.flags.index("--allowedTools") + 1] == (
            "Read,Edit,Bash,WebFetch,mcp__github__create_issue"
        )
        assert result.flags[2:5] == (
            "--strict-mcp-config",
            "--mcp-config",
            str(tmp_path / "control" / "mcp-config.json"),
        )

    def test_allow_all_with_denies_grants_every_builtin_and_renders_denies(self, tmp_path: Path) -> None:
        policy = ToolPolicy(allow_all=True, deny=[_rule(ToolCapability.SHELL, "git push *")])

        assert _render(policy, _roots(tmp_path)) == ClaudePolicyArgs(
            flags=(
                "--permission-mode",
                "dontAsk",
                *_MCP_FLAGS,
                "--tools",
                _ALL_TOOLS,
                "--allowedTools",
                "Read,Edit,Bash,WebFetch",
                "--disallowedTools",
                "Bash(git push:*)",
            )
        )

    def test_allow_all_with_mcp_deny_renders_it_beside_dontask_grants(self, tmp_path: Path) -> None:
        policy = ToolPolicy(allow_all=True, deny=[_rule(ToolCapability.MCP, "github/*")])

        assert _render(policy, _roots(tmp_path)) == ClaudePolicyArgs(
            flags=(
                "--permission-mode",
                "dontAsk",
                *_MCP_FLAGS,
                "--tools",
                _ALL_TOOLS,
                "--allowedTools",
                "Read,Edit,Bash,WebFetch",
                "--disallowedTools",
                "mcp__github",
            )
        )

    def test_deny_appears_in_disallowed_tools_and_is_not_removed_by_a_broader_allow(self, tmp_path: Path) -> None:
        policy = ToolPolicy(
            allow=[_rule(ToolCapability.SHELL, "git *")],
            deny=[_rule(ToolCapability.SHELL, "git push *"), _rule(ToolCapability.NETWORK)],
        )

        assert _render(policy, _roots(tmp_path)) == ClaudePolicyArgs(
            flags=(
                "--permission-mode",
                "dontAsk",
                *_MCP_FLAGS,
                "--tools",
                "Bash",
                "--allowedTools",
                "Bash(git:*)",
                "--disallowedTools",
                "Bash(git push:*),WebFetch",
            )
        )

    def test_default_policy_appends_add_dir_and_anchors_both_roots(self, tmp_path: Path) -> None:
        roots = _roots(tmp_path)
        assert roots.scratch is not None
        allowed = ",".join(f"{tool}({_spec(root)})" for root in (tmp_path, roots.scratch) for tool in ("Read", "Edit"))

        assert _render(default_tool_policy(), roots) == ClaudePolicyArgs(
            flags=(
                "--permission-mode",
                "dontAsk",
                *_MCP_FLAGS,
                "--tools",
                _READ_WRITE_TOOLS,
                "--allowedTools",
                allowed,
                "--add-dir",
                str(roots.scratch),
            )
        )

    def test_scratch_only_read_is_supported_and_adds_the_directory(self, tmp_path: Path) -> None:
        roots = _roots(tmp_path)
        assert roots.scratch is not None
        policy = ToolPolicy(allow=[_rule(ToolCapability.READ, root="scratch")])

        assert _render(policy, roots) == ClaudePolicyArgs(
            flags=(
                "--permission-mode",
                "dontAsk",
                *_MCP_FLAGS,
                "--tools",
                "Read,Grep,Glob",
                "--allowedTools",
                f"Read({_spec(roots.scratch)})",
                "--add-dir",
                str(roots.scratch),
            )
        )

    def test_control_directory_is_never_rendered(self, tmp_path: Path) -> None:
        control = tmp_path / "control"
        roots = PolicyRoots(worktree=tmp_path / "work", scratch=tmp_path / "scratch", control=control)

        result = _render(default_tool_policy(), roots)

        assert isinstance(result, ClaudePolicyArgs)
        assert not any(str(control) in flag for flag in result.flags)

    @pytest.mark.parametrize(
        ("policy", "scratch", "reason"),
        [
            pytest.param(
                ToolPolicy(deny=[_rule(ToolCapability.MCP)]),
                True,
                "an mcp deny rule must name a server",
                id="mcp-deny-without-pattern",
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
        assert _render(policy, _roots(tmp_path, scratch=scratch)) == reason

    def test_mcp_deny_with_comma_returns_reason_string(self, tmp_path: Path) -> None:
        policy = ToolPolicy.model_construct(
            deny=[ToolRule.model_construct(capability=ToolCapability.MCP, root=None, pattern="a,b/tool")]
        )

        assert (
            _render(policy, _roots(tmp_path))
            == "mcp pattern 'a,b/tool' contains a comma and cannot be preserved for Claude Code"
        )

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
        worktree = tmp_path / worktree_name
        roots = PolicyRoots(worktree=worktree, scratch=None, control=None)
        policy = ToolPolicy(allow=[_rule(ToolCapability.READ, pattern)])

        assert (
            _render(policy, roots)
            == f"read path {_spec(worktree, reason_path)!r} contains a comma and cannot be preserved for Claude Code"
        )

    def test_scratch_read_allow_does_not_widen_worktree_write_allow(self, tmp_path: Path) -> None:
        roots = _roots(tmp_path)
        assert roots.scratch is not None
        policy = ToolPolicy(
            allow=[_rule(ToolCapability.WRITE, root="worktree"), _rule(ToolCapability.READ, root="scratch")]
        )

        assert _render(policy, roots) == ClaudePolicyArgs(
            flags=(
                "--permission-mode",
                "dontAsk",
                *_MCP_FLAGS,
                "--tools",
                _READ_WRITE_TOOLS,
                "--allowedTools",
                f"Edit({_spec(tmp_path)}),Read({_spec(roots.scratch)})",
                "--add-dir",
                str(roots.scratch),
            )
        )

    def test_mcp_allow_renders_config_file_and_grant(self, tmp_path: Path) -> None:
        policy = ToolPolicy(allow=[_rule(ToolCapability.MCP, "github/*")])
        config_path = tmp_path / "control" / "mcp-config.json"

        assert _render(policy, _control_roots(tmp_path), configured={"github": _GITHUB}) == ClaudePolicyArgs(
            flags=(
                "--permission-mode",
                "dontAsk",
                "--strict-mcp-config",
                "--mcp-config",
                str(config_path),
                "--tools",
                "",
                "--allowedTools",
                "mcp__github",
            ),
            mcp_config=McpConfigFile(path=config_path, content=_GITHUB_CONFIG),
        )

    def test_mcp_allow_tool_grants_only_that_tool(self, tmp_path: Path) -> None:
        policy = ToolPolicy(allow=[_rule(ToolCapability.MCP, "github/create_issue")])

        result = _render(policy, _control_roots(tmp_path), configured={"github": _GITHUB})

        assert isinstance(result, ClaudePolicyArgs)
        assert result.flags[-2:] == ("--allowedTools", "mcp__github__create_issue")

    def test_server_named_by_several_allow_rules_is_written_once(self, tmp_path: Path) -> None:
        policy = ToolPolicy(
            allow=[_rule(ToolCapability.MCP, "github/create_issue"), _rule(ToolCapability.MCP, "github/*")]
        )

        result = _render(policy, _control_roots(tmp_path), configured={"github": _GITHUB})

        assert isinstance(result, ClaudePolicyArgs)
        assert result.mcp_config == McpConfigFile(path=tmp_path / "control" / "mcp-config.json", content=_GITHUB_CONFIG)
        assert result.flags[-1] == "mcp__github__create_issue,mcp__github"

    def test_user_config_server_is_used_when_not_configured(self, tmp_path: Path) -> None:
        policy = ToolPolicy(allow=[_rule(ToolCapability.MCP, "github/*")])

        result = _render(policy, _control_roots(tmp_path), user={"github": {"command": "uvx"}})

        assert isinstance(result, ClaudePolicyArgs)
        assert result.mcp_config is not None
        assert result.mcp_config.content == '{"mcpServers":{"github":{"command":"uvx"}}}'

    def test_configured_server_wins_over_user_config(self, tmp_path: Path) -> None:
        policy = ToolPolicy(allow=[_rule(ToolCapability.MCP, "github/*")])

        result = _render(
            policy,
            _control_roots(tmp_path),
            configured={"github": McpServerDefinition(command="npx")},
            user={"github": {"command": "uvx", "args": ["extra"]}},
        )

        assert isinstance(result, ClaudePolicyArgs)
        assert result.mcp_config is not None
        assert result.mcp_config.content == '{"mcpServers":{"github":{"command":"npx"}}}'

    def test_only_allowed_servers_are_written(self, tmp_path: Path) -> None:
        policy = ToolPolicy(allow=[_rule(ToolCapability.MCP, "a/*")])

        result = _render(
            policy,
            _control_roots(tmp_path),
            configured={"a": McpServerDefinition(command="npx"), "b": McpServerDefinition(command="uvx")},
            user={"c": {"command": "node"}},
        )

        assert isinstance(result, ClaudePolicyArgs)
        assert result.mcp_config is not None
        assert result.mcp_config.content == '{"mcpServers":{"a":{"command":"npx"}}}'

    @pytest.mark.parametrize(
        ("policy", "has_control", "reason"),
        [
            pytest.param(
                ToolPolicy(allow=[_rule(ToolCapability.MCP, "github/*")]),
                True,
                "mcp server 'github' is neither defined in agents.claude.mcp_servers nor in the user's Claude config",
                id="unknown-server",
            ),
            pytest.param(
                ToolPolicy(allow=[_rule(ToolCapability.MCP)]),
                True,
                "an mcp allow rule must name a server",
                id="no-pattern",
            ),
            pytest.param(
                ToolPolicy(allow=[_rule(ToolCapability.MCP, "github/*")]),
                False,
                "an mcp allow rule requires a control root",
                id="no-control-root",
            ),
            pytest.param(
                ToolPolicy(allow_all=True, allow=[_rule(ToolCapability.MCP, "github/*")]),
                True,
                "mcp server 'github' is neither defined in agents.claude.mcp_servers nor in the user's Claude config",
                id="allow-all-unknown-server",
            ),
        ],
    )
    def test_unresolvable_mcp_allow_returns_reason(
        self, tmp_path: Path, policy: ToolPolicy, has_control: bool, reason: str
    ) -> None:
        roots = _control_roots(tmp_path) if has_control else _roots(tmp_path)

        assert _render(policy, roots) == reason

    def test_policy_without_mcp_allow_keeps_empty_configuration(self, tmp_path: Path) -> None:
        result = _render(default_tool_policy(), _control_roots(tmp_path), configured={"github": _GITHUB})

        assert isinstance(result, ClaudePolicyArgs)
        assert result.flags[2:5] == _MCP_FLAGS
        assert result.mcp_config is None
