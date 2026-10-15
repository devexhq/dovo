"""Contract tests for mapping Claude Code stream-json events to agent progress items."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dovo.core.agents import AgentProgress, AgentProgressKind
from dovo.core.agents.claude_progress import CLAUDE_TARGET_FIELDS, CLAUDE_TOOL_KINDS, map_claude_line
from tests.harness.fixtures import load_agent_fixture

_WORKTREE = Path("/work")

_EXPECTED_SEQUENCES: dict[str, list[tuple[AgentProgressKind, str]]] = {
    "planning": [
        (AgentProgressKind.READ, "calc.py"),
        (AgentProgressKind.NOTE, "**Bug:** `add` in `calc.py` subtracts instead of adding. Line 2 is `return a - b`."),
    ],
    "implementing_create": [
        (AgentProgressKind.EDIT, "notes.txt"),
        (
            AgentProgressKind.NOTE,
            "I couldn't create `notes.txt`. The Write call was denied because Claude Code is running in "
            '"don\'t ask" permission mode, so nothing has been written.',
        ),
    ],
    "implementing_edit": [
        (AgentProgressKind.SHELL, "cat /work/calc.py; ls -la /work/"),
        (AgentProgressKind.EDIT, "calc.py"),
        (
            AgentProgressKind.NOTE,
            "I couldn't make the fix: the edit to `calc.py` was denied because the session is in "
            "\"don't ask\" permission mode. I haven't tried another way to write the file.",
        ),
    ],
    "unmapped_tool": [
        (
            AgentProgressKind.NOTE,
            "I couldn't get the page title: WebFetch failed twice with `getaddrinfo ENOTFOUND example.com`, so the "
            "hostname didn't resolve from this environment. This looks like a DNS or network restriction on the "
            "sandbox rather than a problem with the page itself. If you can reach the site from your machine, or "
            "allow this host here, I can retry.",
        ),
    ],
}


_EXPECTED_RESULTS: dict[str, list[tuple[str | None, bool]]] = {
    "planning": [("toolu-1", False)],
    "implementing_create": [("toolu-1", True)],
    "implementing_edit": [("toolu-1", False), ("toolu-2", True)],
    "unmapped_tool": [("toolu-2", True), ("toolu-3", True)],
}


def _tool_block(tool_name: str, tool_input: object, call_id: str | None = None) -> dict[str, object]:
    block: dict[str, object] = {"type": "tool_use", "name": tool_name, "input": tool_input}
    if call_id is not None:
        block["id"] = call_id

    return block


def _assistant_line(*blocks: dict[str, object]) -> str:
    return json.dumps({"type": "assistant", "message": {"role": "assistant", "content": list(blocks)}})


def _user_line(*blocks: dict[str, object]) -> str:
    return json.dumps({"type": "user", "message": {"role": "user", "content": list(blocks)}})


def _result_block(
    content: object, call_id: str | None = "toolu-1", *, is_error: bool | None = None
) -> dict[str, object]:
    block: dict[str, object] = {"type": "tool_result", "content": content}
    if call_id is not None:
        block["tool_use_id"] = call_id
    if is_error is not None:
        block["is_error"] = is_error

    return block


def _map(line: str) -> tuple[AgentProgress, ...]:
    return map_claude_line(line, worktree_path=_WORKTREE)


class ClaudeMapLineTests:
    @pytest.mark.parametrize(
        ("tool_name", "tool_input", "kind", "target"),
        [
            pytest.param("Read", {"file_path": "/work/calc.py"}, AgentProgressKind.READ, "calc.py", id="read-relative"),
            pytest.param("Grep", {"pattern": "needle", "path": "src"}, AgentProgressKind.SEARCH, "needle", id="grep"),
            pytest.param("Glob", {"pattern": "needle"}, AgentProgressKind.SEARCH, "needle", id="glob-pattern"),
            pytest.param("Edit", {"file_path": "/work/a/b.py"}, AgentProgressKind.EDIT, "a/b.py", id="edit-relative"),
            pytest.param("Write", {"file_path": "/work/n.txt"}, AgentProgressKind.EDIT, "n.txt", id="write-relative"),
            pytest.param(
                "NotebookEdit",
                {"notebook_path": "/work/n.ipynb"},
                AgentProgressKind.EDIT,
                "n.ipynb",
                id="notebook-edit-notebook-path",
            ),
            pytest.param("Bash", {"command": "ls\n  -la"}, AgentProgressKind.SHELL, "ls -la", id="bash-collapsed"),
            pytest.param(
                "Read",
                {"file_path": "/elsewhere/x.py"},
                AgentProgressKind.READ,
                "/elsewhere/x.py",
                id="outside-worktree-absolute",
            ),
        ],
    )
    def test_mapped_tool_returns_kind_and_target(
        self, tool_name: str, tool_input: dict[str, str], kind: AgentProgressKind, target: str
    ) -> None:
        """Return one item of the mapped kind whose target is the tool's own target field, displayed per kind."""
        assert _map(_assistant_line(_tool_block(tool_name, tool_input))) == (AgentProgress(kind=kind, target=target),)

    def test_event_with_several_tool_use_blocks_returns_items_in_block_order(self) -> None:
        """Return the Read, note and Bash items for one assistant event holding a thinking block, Read, text and Bash."""
        line = _assistant_line(
            {"type": "thinking", "thinking": ""},
            _tool_block("Read", {"file_path": "/work/calc.py"}),
            {"type": "text", "text": "reading"},
            _tool_block("Bash", {"command": "ls"}),
        )

        assert _map(line) == (
            AgentProgress(kind=AgentProgressKind.READ, target="calc.py"),
            AgentProgress(kind=AgentProgressKind.NOTE, target="reading"),
            AgentProgress(kind=AgentProgressKind.SHELL, target="ls"),
        )

    @pytest.mark.parametrize(
        ("text", "target"),
        [
            pytest.param("Step 1: read README.md.", "Step 1: read README.md.", id="single-line"),
            pytest.param("\n\n  First   line\nSecond line", "First line", id="first-non-blank-line-collapsed"),
        ],
    )
    def test_text_block_returns_note_with_its_first_line(self, text: str, target: str) -> None:
        """Return one note whose target is the first non-blank line of the text with whitespace collapsed."""
        assert _map(_assistant_line({"type": "text", "text": text})) == (
            AgentProgress(kind=AgentProgressKind.NOTE, target=target),
        )

    @pytest.mark.parametrize(
        "block",
        [
            pytest.param({"type": "text", "text": ""}, id="empty-text"),
            pytest.param({"type": "text", "text": " \n\t"}, id="whitespace-only-text"),
            pytest.param({"type": "text", "text": 3}, id="non-string-text"),
            pytest.param({"type": "text"}, id="missing-text"),
            pytest.param({"type": "thinking", "thinking": "hidden"}, id="thinking-block"),
        ],
    )
    def test_block_without_usable_text_returns_empty_tuple(self, block: dict[str, object]) -> None:
        """Return () for a blank or non-string text block and for a thinking block."""
        assert _map(_assistant_line(block)) == ()

    def test_unusable_block_is_skipped_while_sibling_blocks_still_map(self) -> None:
        """Return only the Bash item when the preceding Read block has no target field."""
        line = _assistant_line(_tool_block("Read", {}), _tool_block("Bash", {"command": "ls"}))

        assert _map(line) == (AgentProgress(kind=AgentProgressKind.SHELL, target="ls"),)

    @pytest.mark.parametrize(
        "line",
        [
            pytest.param('{"type":"user","message":{"content":[]}}', id="user-event"),
            pytest.param('{"type":"result","subtype":"success"}', id="result-event"),
            pytest.param('{"type":"system","subtype":"init"}', id="system-event"),
            pytest.param('{"type":"rate_limit_event"}', id="rate-limit-event"),
            pytest.param('{"type":"assistant","message":{"content":"text"}}', id="content-not-list"),
            pytest.param('{"type":"assistant","message":"text"}', id="message-not-object"),
        ],
    )
    def test_non_assistant_content_line_returns_empty_tuple(self, line: str) -> None:
        """Return () for events that carry no assistant tool_use or text block."""
        assert _map(line) == ()

    def test_tool_use_block_carries_its_call_id(self) -> None:
        """Return the tool_use block's id as the item's call_id."""
        line = _assistant_line(_tool_block("Bash", {"command": "ls"}, call_id="toolu-9"))

        assert _map(line) == (AgentProgress(kind=AgentProgressKind.SHELL, target="ls", call_id="toolu-9"),)

    @pytest.mark.parametrize(
        ("content", "target"),
        [
            pytest.param("a\nb", "a\nb", id="string-content"),
            pytest.param(
                [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}], "a\nb", id="text-blocks-joined"
            ),
            pytest.param("see /work/src/x.py", "see src/x.py", id="worktree-path-made-relative"),
        ],
    )
    def test_tool_result_returns_result_item_with_text_and_call_id(self, content: object, target: str) -> None:
        """Return one RESULT item with the call id, the joined text and the worktree prefix removed."""
        assert _map(_user_line(_result_block(content, "toolu-3"))) == (
            AgentProgress(kind=AgentProgressKind.RESULT, target=target, call_id="toolu-3"),
        )

    def test_error_tool_result_is_flagged(self) -> None:
        """Return a RESULT item with is_error set when the block says is_error is true."""
        assert _map(_user_line(_result_block("boom", is_error=True))) == (
            AgentProgress(kind=AgentProgressKind.RESULT, target="boom", call_id="toolu-1", is_error=True),
        )

    @pytest.mark.parametrize(
        "block",
        [
            pytest.param(_result_block("", "toolu-1"), id="empty-text"),
            pytest.param(_result_block("  \n", "toolu-1"), id="whitespace-only-text"),
            pytest.param(_result_block([{"type": "tool_reference", "tool_name": "X"}], "toolu-1"), id="no-text-blocks"),
            pytest.param(_result_block(3, "toolu-1"), id="non-text-content"),
            pytest.param(_result_block("ok", None), id="missing-call-id"),
            pytest.param({"type": "text", "text": "user text"}, id="text-block-in-user-event"),
        ],
    )
    def test_user_block_without_usable_result_returns_empty_tuple(self, block: dict[str, object]) -> None:
        """Return () for a tool_result with no call id or no text, and for a text block in a user event."""
        assert _map(_user_line(block)) == ()

    def test_unmapped_tool_returns_empty_tuple(self) -> None:
        """Return () for a WebFetch tool_use block."""
        assert _map(_assistant_line(_tool_block("WebFetch", {"url": "https://example.com"}))) == ()

    @pytest.mark.parametrize(
        ("tool_name", "tool_input"),
        [
            pytest.param("Read", {}, id="missing-field"),
            pytest.param("Read", {"file_path": ""}, id="empty-string"),
            pytest.param("Read", {"file_path": "  \n"}, id="whitespace-only"),
            pytest.param("Read", {"file_path": 3}, id="non-string"),
            pytest.param("Read", "/work/calc.py", id="input-not-object"),
            pytest.param("NotebookEdit", {"file_path": "/work/n.ipynb"}, id="notebook-edit-with-file-path-only"),
        ],
    )
    def test_unusable_target_returns_empty_tuple(self, tool_name: str, tool_input: object) -> None:
        """Return () when the tool's target field is missing, blank or not a string."""
        assert _map(_assistant_line(_tool_block(tool_name, tool_input))) == ()

    @pytest.mark.parametrize(
        "line",
        [
            pytest.param("", id="empty-line"),
            pytest.param("not json", id="not-json"),
            pytest.param('"a string"', id="json-string"),
            pytest.param("[]", id="json-array"),
            pytest.param("null", id="json-null"),
        ],
    )
    def test_invalid_or_non_object_line_returns_empty_tuple(self, line: str) -> None:
        """Return () without raising for a line that is not a JSON object."""
        assert _map(line) == ()


class ClaudeFixtureProgressTests:
    @pytest.mark.parametrize("fixture_name", list(_EXPECTED_SEQUENCES))
    def test_fixture_log_maps_to_its_expected_progress_sequence(self, fixture_name: str) -> None:
        """Map every fixture line with worktree /work to the sequence in _EXPECTED_SEQUENCES, () for unmapped_tool."""
        mapped = [progress for line in load_agent_fixture("claude", fixture_name) for progress in _map(line)]
        shown = [progress for progress in mapped if progress.kind is not AgentProgressKind.RESULT]

        assert [(progress.kind, progress.target) for progress in shown] == _EXPECTED_SEQUENCES[fixture_name]

    @pytest.mark.parametrize("fixture_name", list(_EXPECTED_RESULTS))
    def test_fixture_log_maps_to_its_expected_results(self, fixture_name: str) -> None:
        """Map every fixture tool_result to a RESULT item with the expected call id and error flag."""
        mapped = [progress for line in load_agent_fixture("claude", fixture_name) for progress in _map(line)]
        results = [progress for progress in mapped if progress.kind is AgentProgressKind.RESULT]

        assert [(progress.call_id, progress.is_error) for progress in results] == _EXPECTED_RESULTS[fixture_name]


class ClaudeProgressContractTests:
    def test_every_mapped_tool_has_a_target_field(self) -> None:
        """Assert set(CLAUDE_TOOL_KINDS) == set(CLAUDE_TARGET_FIELDS)."""
        assert set(CLAUDE_TOOL_KINDS) == set(CLAUDE_TARGET_FIELDS)
