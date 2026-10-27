"""Invariant: every file in docs/agents/architecture/ keeps the decision-entry format its README defines."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

from tests.lint.astlib import REPO_ROOT

ARCHITECTURE_DIR: Final[Path] = REPO_ROOT / "docs" / "agents" / "architecture"
INDEX: Final[Path] = ARCHITECTURE_DIR / "README.md"
ARCHITECTURE_DOCS: Final[list[Path]] = sorted(ARCHITECTURE_DIR.glob("*.md"))
AREA_DOCS: Final[list[Path]] = [doc for doc in ARCHITECTURE_DOCS if doc != INDEX]

MAX_HEADING_LEVEL: Final = 3
MAX_PARAGRAPH_WORDS: Final = 40
MAX_CODE_BLOCK_LINES: Final = 10
MAX_FILE_LINES: Final = 150
FIELDS: Final = ("Decision", "Why", "Exemplar", "Enforced by", "Since")
REQUIRED_FIELDS: Final = ("Decision", "Why", "Enforced by")
PATH_PREFIXES: Final = ("src/", "tests/", "docs/", "e2e/", "scripts/", ".agents/")
ROOT_FILES: Final = frozenset({"AGENTS.md", "README.md", "mkdocs.yml", "pyproject.toml"})

HEADING: Final = re.compile(r"^(?P<marks>#+)\s+(?P<title>.+)$")
BULLET: Final = re.compile(r"^(?:[-*+]|\d+\.)\s")
FIELD: Final = re.compile(r"^- \*\*(?P<name>[^*]+):\*\*\s*(?P<value>.*)$")
BACKTICKED: Final = re.compile(r"`([^`]+)`")
LINK_TARGET: Final = re.compile(r"\]\((?P<target>[^)#\s]+)(?:#[^)]*)?\)")
LINE_SUFFIX: Final = re.compile(r"(?::\d+(?:-\d+)?|::\S+)$")
PENDING: Final = re.compile(r"^pending #\d+(?: \(phase \d+\))?$")
TEST_PATHS: Final = re.compile(r"^`tests/[^`]+\.py`(?:, `tests/[^`]+\.py`)*$")


@dataclass(frozen=True, slots=True)
class Line:
    """One line of a doc, outside fenced code, with its 1-based number."""

    number: int
    text: str


@dataclass(frozen=True, slots=True)
class Section:
    """A heading and the lines up to the next heading of any level."""

    level: int
    title: str
    body: tuple[Line, ...]


@dataclass(frozen=True, slots=True)
class Block:
    """A run of body lines of one kind: a paragraph, a list or a code block."""

    kind: str
    first: Line
    words: int


def _relative(doc: Path) -> str:
    """Return doc's path relative to the repository root."""
    return str(doc.relative_to(REPO_ROOT))


def _doc_lines(doc: Path) -> list[str]:
    """Return doc's lines without line endings."""
    return doc.read_text(encoding="utf-8").splitlines()


def _prose_lines(lines: list[str]) -> list[Line]:
    """Return lines outside code fences, keeping each opening fence as a one-line marker."""
    kept: list[Line] = []
    in_fence = False
    for number, text in enumerate(lines, start=1):
        if text.startswith("```"):
            if not in_fence:
                kept.append(Line(number, "```"))
            in_fence = not in_fence
        elif not in_fence:
            kept.append(Line(number, text))
    return kept


def _code_block_lengths(lines: list[str]) -> list[tuple[int, int]]:
    """Return (opening line number, content line count) for each fenced code block."""
    blocks: list[tuple[int, int]] = []
    opened: int | None = None
    for number, text in enumerate(lines, start=1):
        if not text.startswith("```"):
            continue
        if opened is None:
            opened = number
        else:
            blocks.append((opened, number - opened - 1))
            opened = None
    return blocks


def _sections(doc: Path) -> list[Section]:
    """Split doc's prose lines into sections, one per heading; lines before the first heading form a level-0 section."""
    sections: list[Section] = []
    level, title, body = 0, "(before the first heading)", []
    for line in _prose_lines(_doc_lines(doc)):
        match = HEADING.match(line.text)
        if match is None:
            body.append(line)
            continue
        sections.append(Section(level, title, tuple(body)))
        level, title, body = len(match["marks"]), match["title"], []
    sections.append(Section(level, title, tuple(body)))
    return sections


def _kind(text: str) -> str:
    """Classify one body line as blank, continuation, list, code or paragraph."""
    if not text.strip():
        return "blank"
    if text.startswith((" ", "\t")):
        return "continuation"
    if BULLET.match(text):
        return "list"
    return "code" if text == "```" else "paragraph"


def _blocks(section: Section) -> list[Block]:
    """Group a section's body into blocks; consecutive paragraph lines form one paragraph."""
    blocks: list[Block] = []
    previous = "blank"
    for line in section.body:
        kind = _kind(line.text)
        if kind == "continuation":
            continue
        words = len(line.text.split())
        if kind == "paragraph" and previous == "paragraph":
            blocks[-1] = Block(kind, blocks[-1].first, blocks[-1].words + words)
        elif kind != "blank":
            blocks.append(Block(kind, line, words))
        previous = kind
    return blocks


def _prose_violations(doc: Path, section: Section) -> list[str]:
    """Return a violation for each paragraph that is not the section's first block, or that runs over the word limit."""
    violations: list[str] = []
    for index, block in enumerate(_blocks(section)):
        if block.kind != "paragraph":
            continue
        where = f"{_relative(doc)}:{block.first.number} ({section.title})"
        if index > 0:
            violations.append(f"{where}: prose after the section's first block; use bullets")
        if block.words > MAX_PARAGRAPH_WORDS:
            violations.append(f"{where}: paragraph has {block.words} words, over {MAX_PARAGRAPH_WORDS}")
    return violations


def _fields(section: Section) -> tuple[dict[str, str], list[Line]]:
    """Return a decision entry's field values by name, and its top-level bullets that are not a known field."""
    fields: dict[str, str] = {}
    strays: list[Line] = []
    for line in section.body:
        match = FIELD.match(line.text)
        if match is not None and match["name"] in FIELDS:
            fields[match["name"]] = match["value"].strip()
        elif BULLET.match(line.text):
            strays.append(line)
    return fields, strays


def _entry_violations(doc: Path, section: Section) -> list[str]:
    """Return a violation for each missing required field or stray top-level bullet in one decision entry."""
    fields, strays = _fields(section)
    where = f"{_relative(doc)} ({section.title})"
    missing = [f"{where}: missing **{name}:**" for name in REQUIRED_FIELDS if name not in fields]
    stray = [f"{_relative(doc)}:{line.number} ({section.title}): top-level bullet is not a field" for line in strays]
    return [*missing, *stray]


def _enforced_by_is_valid(value: str) -> bool:
    """Return whether an Enforced by value is review only, pending #<n>, or existing backticked test paths."""
    if value == "review only" or PENDING.match(value):
        return True
    if not TEST_PATHS.match(value):
        return False
    return all((REPO_ROOT / path).is_file() for path in BACKTICKED.findall(value))


def _cited_path(span: str) -> str | None:
    """Return span as a repository path to check, or None when it is not a concrete repository path."""
    if " " in span or any(mark in span for mark in "<>*{}"):
        return None
    path = LINE_SUFFIX.sub("", span)
    if path in ROOT_FILES or path.startswith(PATH_PREFIXES):
        return path
    return None


def _missing_paths(text: str) -> list[str]:
    """Return each backticked repository path in text that does not exist."""
    paths = [path for span in BACKTICKED.findall(text) if (path := _cited_path(span)) is not None]
    return [path for path in paths if not (REPO_ROOT / path).exists()]


def _missing_links(doc: Path, text: str) -> list[str]:
    """Return each relative link target in text that does not exist next to doc."""
    links = [match["target"] for match in LINK_TARGET.finditer(text) if "://" not in match["target"]]
    return [link for link in links if not (doc.parent / link).exists()]


def _unresolved_references(doc: Path) -> list[str]:
    """Return `path:line: reference` for each backticked repository path or relative link in doc that does not exist."""
    return [
        f"{_relative(doc)}:{line.number}: {reference}"
        for line in _prose_lines(_doc_lines(doc))
        for reference in [*_missing_paths(line.text), *_missing_links(doc, line.text)]
    ]


def _heading_violations(doc: Path) -> list[str]:
    """Return a violation for each heading deeper than level 3."""
    return [
        f"{_relative(doc)}:{line.number}: heading level {len(match['marks'])} ({match['title']})"
        for line in _prose_lines(_doc_lines(doc))
        if (match := HEADING.match(line.text)) is not None and len(match["marks"]) > MAX_HEADING_LEVEL
    ]


def _doc_id(doc: Path) -> str:
    """Return a parametrize id naming doc."""
    return doc.name


class ArchitectureDocsTests:
    def test_folder_has_area_files_so_the_rules_cannot_pass_vacuously(self) -> None:
        assert INDEX.is_file()
        assert AREA_DOCS, f"no area files in {_relative(ARCHITECTURE_DIR)}"

    @pytest.mark.parametrize("doc", ARCHITECTURE_DOCS, ids=_doc_id)
    def test_headings_stay_within_levels_one_to_three(self, doc: Path) -> None:
        assert _heading_violations(doc) == []

    @pytest.mark.parametrize("doc", ARCHITECTURE_DOCS, ids=_doc_id)
    def test_sections_open_with_at_most_one_short_paragraph_then_bullets(self, doc: Path) -> None:
        violations = [found for section in _sections(doc) for found in _prose_violations(doc, section)]

        assert violations == []

    @pytest.mark.parametrize("doc", ARCHITECTURE_DOCS, ids=_doc_id)
    def test_code_blocks_stay_within_line_limit(self, doc: Path) -> None:
        long_blocks = [
            f"{_relative(doc)}:{opened}: code block has {count} lines, over {MAX_CODE_BLOCK_LINES}"
            for opened, count in _code_block_lengths(_doc_lines(doc))
            if count > MAX_CODE_BLOCK_LINES
        ]

        assert long_blocks == []

    @pytest.mark.parametrize("doc", AREA_DOCS, ids=_doc_id)
    def test_decision_entries_carry_required_fields_only_as_fields(self, doc: Path) -> None:
        entries = [section for section in _sections(doc) if section.level == 2]
        assert entries, f"{_relative(doc)} has no decision entries"

        violations = [found for entry in entries for found in _entry_violations(doc, entry)]

        assert violations == []

    @pytest.mark.parametrize("doc", AREA_DOCS, ids=_doc_id)
    def test_enforced_by_names_existing_tests_review_only_or_pending_issue(self, doc: Path) -> None:
        invalid = [
            f"{_relative(doc)} ({section.title}): Enforced by `{value}`"
            for section in _sections(doc)
            if section.level == 2
            and (value := _fields(section)[0].get("Enforced by")) is not None
            and not _enforced_by_is_valid(value)
        ]

        assert invalid == [], "use backticked test paths that exist, `review only`, or `pending #<issue>`"

    @pytest.mark.parametrize("doc", ARCHITECTURE_DOCS, ids=_doc_id)
    def test_cited_paths_and_links_resolve(self, doc: Path) -> None:
        assert _unresolved_references(doc) == []

    @pytest.mark.parametrize("doc", ARCHITECTURE_DOCS, ids=_doc_id)
    def test_file_stays_under_line_limit(self, doc: Path) -> None:
        count = len(_doc_lines(doc))

        assert count < MAX_FILE_LINES, f"{_relative(doc)} has {count} lines; split the area or trim entries"

    def test_index_lists_every_area_file(self) -> None:
        index = INDEX.read_text(encoding="utf-8")

        unlisted = [_relative(doc) for doc in AREA_DOCS if f"]({doc.name})" not in index]

        assert unlisted == [], f"list each area file in {_relative(INDEX)}"
