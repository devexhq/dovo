"""MCP server definitions a Dovo config can hand to an agent provider."""

from __future__ import annotations

import re
from typing import Final, Literal

from pydantic import BaseModel, Field, model_validator

_NAME_RE: Final = re.compile(r"^[^/*\s,]+$")
_REFERENCE_RE: Final = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*(:-[^}]*)?\}")


def validate_mcp_server_name(name: str) -> str | None:
    """Return why name cannot be a server key (a '/', '*', ',', whitespace or '__'), or None."""
    if _NAME_RE.fullmatch(name) is None or "__" in name:
        return f"mcp server name {name!r} must not contain '/', '*', ',', '__' or whitespace"

    return None


class McpServerDefinition(BaseModel):
    """One MCP server; env and headers hold ${NAME} references so the config never stores a secret."""

    model_config = {"extra": "forbid", "strict": True}

    type: Literal["stdio", "http", "sse"] = "stdio"
    command: str | None = Field(default=None, min_length=1)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = Field(default=None, min_length=1)
    headers: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_shape(self) -> McpServerDefinition:
        """Reject an MCP server whose fields do not fit its transport."""
        error = self._stdio_error() if self.type == "stdio" else self._remote_error()
        if error is not None:
            raise ValueError(error)

        return self

    def _stdio_error(self) -> str | None:
        """Return why this stdio server is malformed, or None."""
        if self.command is None:
            return "a stdio mcp server requires command"

        if self.url is not None or self.headers:
            return "a stdio mcp server must not set url or headers"

        return _literal_error(self.env, "env")

    def _remote_error(self) -> str | None:
        """Return why this http or sse server is malformed, or None."""
        if self.url is None:
            return f"an {self.type} mcp server requires url"

        if self.command is not None or self.args or self.env:
            return f"an {self.type} mcp server must not set command, args or env"

        return _literal_error(self.headers, "header")


def _literal_error(values: dict[str, str], label: str) -> str | None:
    """Return why the first value without a ${NAME} reference is rejected, or None."""
    for key, value in values.items():
        if _REFERENCE_RE.search(value) is None:
            return f"{label} value for {key!r} must reference a variable as ${{NAME}}"

    return None
