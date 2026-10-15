"""Contract tests for MCP server definitions and server-name validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dovo.common.mcp_servers import McpServerDefinition, validate_mcp_server_name


class McpServerDefinitionTests:
    @pytest.mark.parametrize(
        ("payload", "reason"),
        [
            pytest.param({"type": "stdio"}, "a stdio mcp server requires command", id="stdio-no-command"),
            pytest.param({"type": "http"}, "an http mcp server requires url", id="http-no-url"),
            pytest.param(
                {"command": "npx", "env": {"TOKEN": "literal"}},
                "env value for 'TOKEN' must reference a variable as ${NAME}",
                id="env-literal",
            ),
            pytest.param(
                {"type": "sse", "url": "https://x", "headers": {"A": "b"}},
                "header value for 'A' must reference a variable as ${NAME}",
                id="header-literal",
            ),
            pytest.param(
                {"command": "npx", "url": "https://x"},
                "a stdio mcp server must not set url or headers",
                id="stdio-with-url",
            ),
            pytest.param(
                {"type": "http", "url": "https://x", "command": "npx"},
                "an http mcp server must not set command, args or env",
                id="http-with-command",
            ),
        ],
    )
    def test_invalid_definition_is_rejected(self, payload: dict[str, object], reason: str) -> None:
        """[tier-1/unit] McpServerDefinition: each invalid payload raises ValidationError whose message contains the exact reason."""
        with pytest.raises(ValidationError) as raised:
            McpServerDefinition.model_validate(payload)

        assert reason in str(raised.value)

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({"command": "npx", "env": {"TOKEN": "${GITHUB_TOKEN}"}}, id="stdio-env-reference"),
            pytest.param(
                {"type": "http", "url": "https://x", "headers": {"Authorization": "Bearer ${API_KEY:-x}"}},
                id="http-header-reference-with-default",
            ),
        ],
    )
    def test_valid_definitions_round_trip(self, payload: dict[str, object]) -> None:
        """[tier-1/unit] McpServerDefinition: a stdio server with env {'TOKEN': '${GITHUB_TOKEN}'} and an http server with headers {'Authorization': 'Bearer ${API_KEY:-x}'} validate and model_dump(exclude_defaults=True) returns the input dict."""
        definition = McpServerDefinition.model_validate(payload)

        assert definition.model_dump(exclude_defaults=True) == payload

    @pytest.mark.parametrize("name", ["a/b", "a*", "a,b", "a b", "a__b"])
    def test_invalid_server_name_returns_reason(self, name: str) -> None:
        """[tier-1/unit] validate_mcp_server_name: each of 'a/b', 'a*', 'a,b', 'a b', 'a__b' returns the exact string "mcp server name '<name>' must not contain '/', '*', ',', '__' or whitespace"; 'github' returns None."""
        assert validate_mcp_server_name(name) == (
            f"mcp server name {name!r} must not contain '/', '*', ',', '__' or whitespace"
        )

    def test_valid_server_name_returns_none(self) -> None:
        """[tier-1/unit] validate_mcp_server_name: 'github' returns None."""
        assert validate_mcp_server_name("github") is None
