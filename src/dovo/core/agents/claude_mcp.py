"""Read the MCP servers a user keeps in their own Claude Code config."""

from __future__ import annotations

import json
import os
from pathlib import Path


def load_user_claude_mcp_servers() -> dict[str, dict[str, object]]:
    """Return the user-scope servers from the host Claude config at call time, {} when the file is absent, unparseable or has no mcpServers object."""
    try:
        config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
        path = (Path(config_dir) if config_dir else Path.home()) / ".claude.json"
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RuntimeError):
        return {}

    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        return {}

    return {name: dict(entry) for name, entry in servers.items() if isinstance(entry, dict)}
