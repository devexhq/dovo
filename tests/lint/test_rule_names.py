"""Invariant: AGENTS.md rules are cited by name, and every cited name is one AGENTS.md defines."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from tests.lint.astlib import REPO_ROOT

AGENTS: Final[Path] = REPO_ROOT / "AGENTS.md"
RULE_COUNT: Final = 18
MIN_WORDS: Final = 3
MAX_WORDS: Final = 4

DEFINITION: Final = re.compile(r"^- \*\*(?P<name>R-[a-z0-9-]+)\.", re.MULTILINE)
STALE_NUMBER: Final = re.compile(r"\bR(?:1[0-8]|[1-9])\b")
FEEDBACK_ITEM_ID: Final = re.compile(r'"id":\s*"R\d+"')
RULE_TOKEN: Final = re.compile(r"(?<![\w-])R-[a-z][a-z0-9-]*")

STALE_GLOBS: Final = (
    "AGENTS.md",
    ".agents/skills/**/*.md",
    ".dovo/catalog/prompts/**/*.md",
    "docs/agents/**/*.md",
    "src/**/*.py",
)
TOKEN_GLOBS: Final = (*STALE_GLOBS, "tests/**/*.py", "scripts/**/*.py")


def _defined_names() -> list[str]:
    """Return every rule name AGENTS.md defines, in file order."""
    return DEFINITION.findall(AGENTS.read_text(encoding="utf-8"))


def _files(globs: tuple[str, ...]) -> list[Path]:
    """Return every file the globs match under the repository root, sorted."""
    return sorted({path for pattern in globs for path in REPO_ROOT.glob(pattern) if path.is_file()})


def _hits(pattern: re.Pattern[str], globs: tuple[str, ...]) -> list[tuple[str, str]]:
    """Return (`path:line`, matched text) for every match of pattern in the files the globs select."""
    found: list[tuple[str, str]] = []
    for path in _files(globs):
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, start=1):
            found.extend((f"{path.relative_to(REPO_ROOT)}:{number}", match) for match in pattern.findall(line))
    return found


def _stale_hits() -> list[str]:
    """Return `path:line` for every numeric rule id left in the scanned files, feedback item ids aside."""
    stale: list[str] = []
    for path in _files(STALE_GLOBS):
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, start=1):
            if STALE_NUMBER.search(FEEDBACK_ITEM_ID.sub("", line)):
                stale.append(f"{path.relative_to(REPO_ROOT)}:{number}")
    return stale


class TestRuleNames:
    """Rule names are the only way to cite an AGENTS.md rule."""

    def test_agents_defines_eighteen_names_of_three_or_four_words(self) -> None:
        names = _defined_names()

        assert len(names) == RULE_COUNT
        assert len(set(names)) == RULE_COUNT
        wrong_length = [name for name in names if not MIN_WORDS <= len(name.removeprefix("R-").split("-")) <= MAX_WORDS]
        assert wrong_length == []

    def test_no_numeric_rule_id_remains(self) -> None:
        assert _stale_hits() == []

    def test_every_cited_name_is_defined(self) -> None:
        defined = set(_defined_names())

        unknown = [f"{where} {token}" for where, token in _hits(RULE_TOKEN, TOKEN_GLOBS) if token not in defined]

        assert unknown == []
