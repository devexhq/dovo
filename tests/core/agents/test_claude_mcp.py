"""Contract tests for reading MCP servers from the user's Claude config."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.core.agents.claude_mcp import load_user_claude_mcp_servers


class LoadUserClaudeMcpServersTests:
    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            pytest.param(
                '{"mcpServers": {"github": {"command": "npx"}}}', {"github": {"command": "npx"}}, id="servers"
            ),
            pytest.param('{"mcpServers": {"bad": "x"}}', {}, id="non-object-entry-skipped"),
            pytest.param('{"mcpServers": []}', {}, id="servers-not-object"),
            pytest.param("[]", {}, id="document-not-object"),
            pytest.param("not json", {}, id="unparseable"),
            pytest.param(None, {}, id="absent"),
        ],
    )
    def test_load_reads_user_scope_servers(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        content: str | None,
        expected: dict[str, dict[str, object]],
    ) -> None:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        if content is not None:
            (tmp_path / ".claude.json").write_text(content, encoding="utf-8")

        assert load_user_claude_mcp_servers() == expected

    def test_load_uses_home_when_config_dir_unset(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        (tmp_path / ".claude.json").write_text('{"mcpServers": {"github": {"command": "uvx"}}}', encoding="utf-8")

        assert load_user_claude_mcp_servers() == {"github": {"command": "uvx"}}
