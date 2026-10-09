"""Contract tests for the structured agent tool policy models and their JSON schemas."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

import pytest
from pydantic import ValidationError

from dovo.common.schema_validation import CONFIG_VALIDATOR, SchemaValidator
from dovo.common.tool_policy import ToolCapability, ToolPolicy, ToolRule

_SCHEMA_PACKAGE = files("dovo.schemas.v1")
_WORKFLOW_VALIDATOR = SchemaValidator(_SCHEMA_PACKAGE / "workflow.json")

_POLICY_EXAMPLE: dict[str, Any] = {
    "allow": [
        {"capability": "read", "pattern": "src/**"},
        {"capability": "read", "root": "scratch", "pattern": "**"},
        {"capability": "write", "root": "scratch", "pattern": "**"},
        {"capability": "shell", "pattern": "git status"},
    ],
    "deny": [{"capability": "write", "pattern": ".dovo/**"}],
    "allow_all": False,
}


def _schema_defs(name: str) -> dict[str, Any]:
    """Return the $defs table of a bundled v1 schema."""
    return json.loads((_SCHEMA_PACKAGE / name).read_text(encoding="utf-8"))["$defs"]


class ToolRuleContractTests:
    @pytest.mark.parametrize(
        "rule",
        [
            pytest.param({"capability": "shell", "root": "worktree"}, id="root-on-shell"),
            pytest.param({"capability": "read", "pattern": "../x"}, id="dotdot"),
            pytest.param({"capability": "read", "pattern": "a/../x"}, id="nested-dotdot"),
            pytest.param({"capability": "read", "pattern": "/etc/**"}, id="absolute"),
            pytest.param({"capability": "read", "pattern": "C:/x"}, id="drive"),
            pytest.param({"capability": "read", "pattern": "//host/share"}, id="unc"),
            pytest.param({"capability": "read", "pattern": ""}, id="empty"),
            pytest.param({"capability": "write", "pattern": "a\x00b"}, id="nul"),
            pytest.param({"capability": "shell", "pattern": "git status; rm"}, id="chaining"),
            pytest.param({"capability": "shell", "pattern": "git*"}, id="shell-glob"),
            pytest.param({"capability": "shell", "pattern": "*"}, id="shell-bare-star"),
            pytest.param({"capability": "shell", "pattern": "git  *"}, id="shell-extra-space"),
            pytest.param({"capability": "shell", "pattern": " git"}, id="shell-leading-space"),
            pytest.param({"capability": "network", "pattern": "*."}, id="network-bare-wildcard-prefix"),
            pytest.param({"capability": "network", "pattern": "https://x.test/a"}, id="url"),
            pytest.param({"capability": "network", "pattern": "user@x.test"}, id="credentials"),
            pytest.param({"capability": "mcp", "pattern": "*/tool"}, id="wildcard-server"),
            pytest.param({"capability": "mcp", "pattern": "srv"}, id="server-only"),
            pytest.param({"capability": "bogus"}, id="unknown-capability"),
            pytest.param({"capability": "read", "extra": 1}, id="unknown-field"),
        ],
    )
    def test_invalid_rule_raises_validation_error(self, rule: dict[str, object]) -> None:
        """[tier-1/unit] ToolRule: each invalid rule shape raises ValidationError."""
        with pytest.raises(ValidationError):
            ToolRule.model_validate(rule)

    @pytest.mark.parametrize(
        ("rule", "capability"),
        [
            pytest.param({"capability": "read", "pattern": "src/**"}, ToolCapability.READ, id="read-glob"),
            pytest.param({"capability": "read"}, ToolCapability.READ, id="read-no-pattern"),
            pytest.param(
                {"capability": "write", "root": "scratch", "pattern": "**"}, ToolCapability.WRITE, id="scratch-write"
            ),
            pytest.param({"capability": "shell", "pattern": "git *"}, ToolCapability.SHELL, id="shell-prefix"),
            pytest.param({"capability": "shell", "pattern": "git status"}, ToolCapability.SHELL, id="shell-exact"),
            pytest.param({"capability": "network", "pattern": "*.example.com"}, ToolCapability.NETWORK, id="subdomain"),
            pytest.param({"capability": "network", "pattern": "example.com"}, ToolCapability.NETWORK, id="host"),
            pytest.param({"capability": "mcp", "pattern": "srv/*"}, ToolCapability.MCP, id="server-wildcard"),
            pytest.param({"capability": "mcp", "pattern": "srv/tool"}, ToolCapability.MCP, id="server-tool"),
        ],
    )
    def test_valid_string_token_rule_constructs_with_capability_enum(
        self, rule: dict[str, object], capability: ToolCapability
    ) -> None:
        """[tier-1/unit] ToolRule.model_validate: YAML-style string tokens yield a ToolCapability member."""
        assert ToolRule.model_validate(rule).capability is capability


class ToolPolicyContractTests:
    def test_default_policy_is_empty_and_not_allow_all(self) -> None:
        """[tier-1/unit] ToolPolicy(): allow == [], deny == [], allow_all is False."""
        policy = ToolPolicy()

        assert (policy.allow, policy.deny, policy.allow_all) == ([], [], False)

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({"allow_all": "yes"}, id="non-bool-allow-all"),
            pytest.param({"extra": 1}, id="unknown-field"),
        ],
    )
    def test_invalid_policy_raises_validation_error(self, payload: dict[str, object]) -> None:
        """[tier-1/unit] ToolPolicy.model_validate: a non-bool allow_all or an unknown field raises ValidationError."""
        with pytest.raises(ValidationError):
            ToolPolicy.model_validate(payload)


class ToolPolicySchemaParityTests:
    def test_workflow_and_config_schemas_share_one_tool_policy_definition(self) -> None:
        """[tier-1/unit] workflow.json and config.json: $defs.toolPolicy and $defs.toolRule are equal dicts."""
        workflow_defs = _schema_defs("workflow.json")
        config_defs = _schema_defs("config.json")

        assert workflow_defs["toolPolicy"] == config_defs["toolPolicy"]
        assert workflow_defs["toolRule"] == config_defs["toolRule"]

    def test_schemas_accept_policy_example(self) -> None:
        """[tier-1/unit] WORKFLOW_VALIDATOR / CONFIG_VALIDATOR: the policy example validates for step tools and agent.tools."""
        workflow = {
            "version": 1,
            "name": "w",
            "id": "w",
            "steps": [{"id": "s", "uses": "catalog/x", "tools": _POLICY_EXAMPLE}],
        }
        config = {"version": 1, "project": {"name": "p"}, "agent": {"tools": _POLICY_EXAMPLE}}

        assert (_WORKFLOW_VALIDATOR.validate(workflow).errors, CONFIG_VALIDATOR.validate(config).errors) == ([], [])

    def test_schemas_reject_legacy_list_and_unknown_capability(self) -> None:
        """[tier-1/unit] WORKFLOW_VALIDATOR / CONFIG_VALIDATOR: ['shell'] and an unknown capability each fail for step tools and agent.tools."""
        bad_values: list[object] = [["shell"], {"allow": [{"capability": "bogus"}]}]
        for bad in bad_values:
            workflow = {"version": 1, "name": "w", "id": "w", "steps": [{"id": "s", "uses": "x", "tools": bad}]}
            config = {"version": 1, "project": {"name": "p"}, "agent": {"tools": bad}}

            assert _WORKFLOW_VALIDATOR.validate(workflow).errors
            assert CONFIG_VALIDATOR.validate(config).errors
