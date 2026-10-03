"""Shared unified-diff fixtures for agent tests."""

from __future__ import annotations

AGENT_ADAPTER_FACTORY = "worktree.core.agents.services.run_direct.get_agent_adapter"


def new_file_diff(name: str, content: str = "hello") -> str:
    """Return a unified diff creating `name` with a single line of `content`."""
    return (
        f"diff --git a/{name} b/{name}\n"
        "new file mode 100644\n"
        "index 0000000..e69de29\n"
        "--- /dev/null\n"
        f"+++ b/{name}\n"
        "@@ -0,0 +1 @@\n"
        f"+{content}\n"
    )
