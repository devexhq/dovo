"""Parity: the `Error codes` sections of the `docs/cli/` command pages list every published `ErrorCode`, and no page names
a code that is not published.

A published code is every `ErrorCode` member outside `UNPUBLISHED`, the codes no command can surface. A page names a code
when a backticked token on it is `INTERNAL_ERROR`, or starts with the first word of an `ErrorCode` member and ends in one
of the `STATES` of `test_error_codes.py`, so `ARTIFACT_NAME`, `DOVO_HOME`, `NOT_A_GIT_REPO` and `STEP_DONE` are not codes.
A page lists a code when the code is backticked in the first cell of a table row inside its `Error codes` section, which
runs from a heading whose text is exactly `Error codes` to the next heading of the same or a higher level.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest

from dovo.common.error_codes import ErrorCode
from tests.lint.astlib import REPO_ROOT
from tests.lint.test_error_codes import STATES

CLI_DOCS: Final[Path] = REPO_ROOT / "docs" / "cli"
UNPUBLISHED: Final[dict[ErrorCode, str]] = {
    ErrorCode.PROJECT_IDENTITY_NOT_FOUND: "identity provisioning checks the file exists first; a command reports "
    "WORKSPACE_NOT_INITIALIZED instead",
    ErrorCode.AGENT_PATCH_WRITE_FAILED: "its only caller keeps the failure as a note on the rejected edit",
    ErrorCode.CATALOG_TYPE_INVALID: "every caller passes a catalog item type member",
    ErrorCode.STEP_SESSION_MISSING: "an internal step always runs with its session id",
    ErrorCode.STEP_AGENT_SETTINGS_MISSING: "an agent step always runs with resolved settings",
    ErrorCode.CONFIG_PARENT_MISSING: "init creates `.dovo/` before it writes the config, so a bootstrap code fires first",
    ErrorCode.CONFIG_PATH_UNWRITABLE: "init checks `.dovo/` is writable before it writes the config, so a bootstrap code "
    "fires first",
}
PUBLISHED: Final[frozenset[str]] = frozenset(code.value for code in ErrorCode) - {code.value for code in UNPUBLISHED}

_SECTION_TITLE: Final[str] = "Error codes"
_HEADING_RE: Final[re.Pattern[str]] = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<title>.+?)\s*$")
_FENCE_RE: Final[re.Pattern[str]] = re.compile(r"^```.*?^```[^\n]*$", re.MULTILINE | re.DOTALL)
_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"`([A-Z][A-Z0-9_]*)`")
_PREFIXES: Final[frozenset[str]] = frozenset(code.value.split("_", 1)[0] for code in ErrorCode)


def is_code_token(token: str) -> bool:
    """Return True when token reads as an error code: INTERNAL_ERROR, or an ErrorCode prefix ending in a known state."""
    if token == ErrorCode.INTERNAL_ERROR.value:
        return True
    return token.split("_", 1)[0] in _PREFIXES and any(token.endswith(f"_{state}") for state in STATES)


def named_codes(text: str) -> set[str]:
    """Return every backticked code token anywhere in text."""
    return {token for token in _TOKEN_RE.findall(text) if is_code_token(token)}


def error_codes_section(text: str) -> list[str] | None:
    """Return the lines of the page's Error codes section, or None when the page has no such heading.

    Fenced code blocks are dropped first, so a `# comment` line in a shell example is not read as a heading.
    """
    lines = _FENCE_RE.sub("", text).splitlines()
    for index, line in enumerate(lines):
        heading = _HEADING_RE.match(line)
        if heading is None or heading["title"] != _SECTION_TITLE:
            continue
        level = len(heading["hashes"])
        body: list[str] = []
        for following in lines[index + 1 :]:
            nested = _HEADING_RE.match(following)
            if nested is not None and len(nested["hashes"]) <= level:
                break
            body.append(following)
        return body
    return None


def listed_codes(section: list[str]) -> set[str]:
    """Return the code tokens in the first cell of each table row of section."""
    codes: set[str] = set()
    for line in section:
        if not line.startswith("|"):
            continue
        first_cell = line.split("|", 2)[1]
        codes |= named_codes(first_cell)
    return codes


def _pages() -> list[Path]:
    """Return every command page under docs/cli/."""
    return sorted(CLI_DOCS.glob("*.md"))


_FIXTURE_PAGE: Final[str] = """# `dovo thing`

Mentions `ARTIFACT_NAME`, `DOVO_HOME`, `NOT_A_GIT_REPO` and `STEP_DONE`, which are not codes.

## Error codes

| Code | Cause |
| --- | --- |
| `GIT_FAILED`, `GIT_TIMEOUT` | Git failed. |
| `WORKTREE_NOT_FOUND` | Unknown id; see `RUN_STEP_FAILED`. |

### Nested

| Code | Cause |
| --- | --- |
| `INTERNAL_ERROR` | Unexpected. |

## Exit codes

| Code | Cause |
| --- | --- |
| `SESSION_NOT_FOUND` | Outside the section. |
"""


class ErrorCodeDocParityTests:
    """Every command page lists its codes, every published code is listed, and no page names an unpublished one."""

    @pytest.mark.parametrize("page", _pages(), ids=lambda page: page.stem)
    def test_every_command_page_has_an_error_codes_section(self, page: Path) -> None:
        section = error_codes_section(page.read_text(encoding="utf-8"))

        assert section is not None, f"{page.name}: no '{_SECTION_TITLE}' heading"
        assert listed_codes(section), f"{page.name}: the '{_SECTION_TITLE}' section lists no code"

    def test_command_pages_name_only_published_codes(self) -> None:
        unknown = sorted(
            f"{page.name}: {token}"
            for page in _pages()
            for token in named_codes(page.read_text(encoding="utf-8"))
            if token not in PUBLISHED
        )

        assert unknown == []

    def test_every_published_code_appears_on_a_page(self) -> None:
        listed: set[str] = set()
        for page in _pages():
            listed |= listed_codes(error_codes_section(page.read_text(encoding="utf-8")) or [])

        assert sorted(PUBLISHED - listed) == []

    def test_every_unpublished_code_carries_its_reason(self) -> None:
        assert [code for code, reason in UNPUBLISHED.items() if not reason.strip()] == []


class ErrorCodeExtractionTests:
    """The section reader and the token rule on an inline page."""

    def test_extracts_codes_from_first_cells_of_the_section_and_its_subsections(self) -> None:
        section = error_codes_section(_FIXTURE_PAGE)

        assert section is not None
        assert listed_codes(section) == {"GIT_FAILED", "GIT_TIMEOUT", "WORKTREE_NOT_FOUND", "INTERNAL_ERROR"}

    @pytest.mark.parametrize(
        ("token", "is_code"),
        [
            pytest.param("ARTIFACT_NAME", False, id="artifact-env-key"),
            pytest.param("DOVO_HOME", False, id="env-var"),
            pytest.param("NOT_A_GIT_REPO", False, id="status-text"),
            pytest.param("STEP_DONE", False, id="event-name"),
            pytest.param("INTERNAL_ERROR", True, id="internal-error"),
            pytest.param("RUN_STEP_FAILED", True, id="run-code"),
            pytest.param("WORKTREE_GONE_MISSING", True, id="unknown-code"),
        ],
    )
    def test_token_rule(self, token: str, *, is_code: bool) -> None:
        assert is_code_token(token) is is_code

    @pytest.mark.parametrize(
        "token",
        [
            pytest.param("WORKTREE_GONE_MISSING", id="not-a-member"),
            pytest.param(ErrorCode.CATALOG_TYPE_INVALID.value, id="unpublished"),
        ],
    )
    def test_unknown_code_token_is_reported(self, token: str) -> None:
        page = f"# `dovo thing`\n\nFails with `{token}`.\n"

        assert {found for found in named_codes(page) if found not in PUBLISHED} == {token}

    def test_page_without_section_has_none(self) -> None:
        assert error_codes_section("# `dovo thing`\n\n## Exit codes\n") is None

    def test_heading_inside_a_code_block_does_not_end_the_section(self) -> None:
        page = "## Error codes\n\n```bash\n# a comment\n```\n\n| `GIT_FAILED` | Git failed. |\n\n## Exit codes\n"

        assert listed_codes(error_codes_section(page) or []) == {"GIT_FAILED"}
