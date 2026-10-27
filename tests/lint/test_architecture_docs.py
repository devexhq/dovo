"""Invariant: every file in docs/agents/architecture/ keeps the decision-entry format its README defines."""

from __future__ import annotations

import ast
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
FENCES: Final = ("```", "~~~")

HEADING: Final = re.compile(r"^(?P<marks>#+)\s+(?P<title>.+)$")
BULLET: Final = re.compile(r"^(?:[-*+]|\d+\.)\s")
FIELD: Final = re.compile(r"^- \*\*(?P<name>[^*]+):\*\*\s*(?P<value>.*)$")
BACKTICKED: Final = re.compile(r"`([^`]+)`")
LINK_TARGET: Final = re.compile(r"\]\((?P<target>[^)#\s]+)(?:#[^)]*)?\)")
LINE_NUMBER: Final = re.compile(r":\d+(?:-\d+)?$")
PARAMETRIZE_ID: Final = re.compile(r"\[.*\]$")
PENDING: Final = re.compile(r"^pending #\d+(?: \(phase \d+\))?$")
TEST_PATHS: Final = re.compile(r"^`tests/[^`]+\.py(?:::[^`]+)?`(?:, `tests/[^`]+\.py(?:::[^`]+)?`)*$")


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


def _doc_params(docs: list[Path]) -> list[object]:
    """Return one pytest.param per doc, identified by its file name."""
    return [pytest.param(doc, id=doc.name) for doc in docs]


def _relative(doc: Path) -> str:
    """Return doc's path relative to the repository root."""
    return str(doc.relative_to(REPO_ROOT))


def _doc_lines(doc: Path) -> list[str]:
    """Return doc's lines without line endings."""
    return doc.read_text(encoding="utf-8").splitlines()


def _fence(text: str) -> str | None:
    """Return the fence marker a line opens or closes with, at any indentation, or None."""
    stripped = text.lstrip()
    return next((marker for marker in FENCES if stripped.startswith(marker)), None)


def _fenced_blocks(lines: list[str]) -> list[tuple[int, int, int]]:
    """Return (opening line, closing line, content line count) for each fenced code block; an unclosed one ends the doc."""
    blocks: list[tuple[int, int, int]] = []
    opened: tuple[int, str] | None = None
    for number, text in enumerate(lines, start=1):
        marker = _fence(text)
        if marker is None:
            continue
        if opened is None:
            opened = (number, marker)
        elif marker == opened[1]:
            blocks.append((opened[0], number, number - opened[0] - 1))
            opened = None
    if opened is not None:
        blocks.append((opened[0], len(lines) + 1, len(lines) - opened[0]))
    return blocks


def _prose_lines(lines: list[str]) -> list[Line]:
    """Return lines outside code fences, keeping each opening fence, with its indentation, as a one-line marker."""
    fenced = {number for start, end, _ in _fenced_blocks(lines) for number in range(start + 1, end + 1)}
    kept: list[Line] = []
    for number, text in enumerate(lines, start=1):
        if number in fenced:
            continue
        indent = text[: len(text) - len(text.lstrip())]
        kept.append(Line(number, f"{indent}```" if _fence(text) else text))
    return kept


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


def _kind(text: str, previous: str) -> str:
    """Classify one body line; an unindented line straight after a list item continues it."""
    if not text.strip():
        return "blank"
    if text.startswith((" ", "\t")):
        return "continuation"
    if BULLET.match(text):
        return "list"
    if text == "```":
        return "code"
    return "continuation" if previous == "list" else "paragraph"


def _blocks(section: Section) -> list[Block]:
    """Group a section's body into blocks; consecutive paragraph lines form one paragraph."""
    blocks: list[Block] = []
    previous = "blank"
    for line in section.body:
        kind = _kind(line.text, previous)
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


def _fields(section: Section) -> tuple[list[tuple[str, str]], list[Line]]:
    """Return a decision entry's (field, value) pairs in order, and its top-level bullets that are not a known field."""
    fields: list[tuple[str, str]] = []
    strays: list[Line] = []
    for line in section.body:
        match = FIELD.match(line.text)
        if match is not None and match["name"] in FIELDS:
            fields.append((match["name"], match["value"].strip()))
        elif BULLET.match(line.text):
            strays.append(line)
    return fields, strays


def _field_order_violations(where: str, names: list[str]) -> list[str]:
    """Return a violation for each repeated field and for fields out of the README's order."""
    repeated = sorted({name for name in names if names.count(name) > 1})
    violations = [f"{where}: **{name}:** appears more than once" for name in repeated]
    positions = [FIELDS.index(name) for name in names]
    if positions != sorted(positions):
        violations.append(f"{where}: fields out of order ({', '.join(names)}); use {', '.join(FIELDS)}")
    return violations


def _entry_violations(doc: Path, section: Section) -> list[str]:
    """Return a violation for each missing, repeated or misordered field, or stray top-level bullet, in one entry."""
    fields, strays = _fields(section)
    names = [name for name, _ in fields]
    where = f"{_relative(doc)} ({section.title})"
    missing = [f"{where}: missing **{name}:**" for name in REQUIRED_FIELDS if name not in names]
    stray = [f"{_relative(doc)}:{line.number} ({section.title}): top-level bullet is not a field" for line in strays]
    return [*missing, *_field_order_violations(where, names), *stray]


def _names_exist(source: Path, names: list[str]) -> bool:
    """Return whether the nested class or function names exist in a Python file, ignoring a [parametrize-id]."""
    if source.suffix != ".py":
        return False
    scope: list[ast.stmt] = ast.parse(source.read_text(encoding="utf-8")).body
    for name in (PARAMETRIZE_ID.sub("", part) for part in names):
        found = next(
            (
                node
                for node in scope
                if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name
            ),
            None,
        )
        if found is None:
            return False
        scope = found.body
    return True


def _is_repo_path(path: str) -> bool:
    """Return whether path names a file or folder in this repository by its prefix."""
    return path in ROOT_FILES or path.startswith(PATH_PREFIXES)


def _reference_problem(span: str) -> str | None:
    """Return why a backticked repository reference is invalid: a line suffix, a missing path or a missing test name."""
    if " " in span or any(mark in span for mark in "<>*{}"):
        return None
    path, _, names = span.partition("::")
    if LINE_NUMBER.search(path):
        return f"{span}: drop the line number" if _is_repo_path(LINE_NUMBER.sub("", path)) else None
    if not _is_repo_path(path):
        return None
    target = REPO_ROOT / path
    if not target.exists():
        return f"{span}: no such path"
    if names and not _names_exist(target, names.split("::")):
        return f"{span}: no such class or test"
    return None


def _enforced_by_is_valid(value: str) -> bool:
    """Return whether an Enforced by value is review only, pending #<n>, or existing backticked tests."""
    if value == "review only" or PENDING.match(value):
        return True
    if not TEST_PATHS.match(value):
        return False
    return all(_reference_problem(span) is None for span in BACKTICKED.findall(value))


def _missing_links(doc: Path, text: str) -> list[str]:
    """Return each relative link target in text that does not exist next to doc."""
    links = [match["target"] for match in LINK_TARGET.finditer(text) if "://" not in match["target"]]
    return [f"{link}: no such file" for link in links if not (doc.parent / link).exists()]


def _reference_problems(text: str) -> list[str]:
    """Return the problem with each invalid backticked repository reference in text."""
    problems = [_reference_problem(span) for span in BACKTICKED.findall(text)]
    return [problem for problem in problems if problem is not None]


def _unresolved_references(doc: Path) -> list[str]:
    """Return `path:line: problem` for each invalid backticked repository reference or relative link in doc."""
    return [
        f"{_relative(doc)}:{line.number}: {problem}"
        for line in _prose_lines(_doc_lines(doc))
        for problem in [*_reference_problems(line.text), *_missing_links(doc, line.text)]
    ]


def _heading_violations(doc: Path) -> list[str]:
    """Return a violation for each heading deeper than level 3."""
    return [
        f"{_relative(doc)}:{line.number}: heading level {len(match['marks'])} ({match['title']})"
        for line in _prose_lines(_doc_lines(doc))
        if (match := HEADING.match(line.text)) is not None and len(match["marks"]) > MAX_HEADING_LEVEL
    ]


class ArchitectureDocsTests:
    def test_folder_has_area_files_so_the_rules_cannot_pass_vacuously(self) -> None:
        assert INDEX.is_file()
        assert AREA_DOCS, f"no area files in {_relative(ARCHITECTURE_DIR)}"

    @pytest.mark.parametrize("doc", _doc_params(ARCHITECTURE_DOCS))
    def test_headings_stay_within_levels_one_to_three(self, doc: Path) -> None:
        assert _heading_violations(doc) == []

    @pytest.mark.parametrize("doc", _doc_params(ARCHITECTURE_DOCS))
    def test_sections_open_with_at_most_one_short_paragraph_then_bullets(self, doc: Path) -> None:
        violations = [found for section in _sections(doc) for found in _prose_violations(doc, section)]

        assert violations == []

    @pytest.mark.parametrize("doc", _doc_params(ARCHITECTURE_DOCS))
    def test_code_blocks_including_nested_stay_within_line_limit(self, doc: Path) -> None:
        long_blocks = [
            f"{_relative(doc)}:{opened}: code block has {count} lines, over {MAX_CODE_BLOCK_LINES}"
            for opened, _, count in _fenced_blocks(_doc_lines(doc))
            if count > MAX_CODE_BLOCK_LINES
        ]

        assert long_blocks == []

    @pytest.mark.parametrize("doc", _doc_params(AREA_DOCS))
    def test_decision_entries_hold_required_fields_once_in_order(self, doc: Path) -> None:
        entries = [section for section in _sections(doc) if section.level == 2]
        assert entries, f"{_relative(doc)} has no decision entries"

        violations = [found for entry in entries for found in _entry_violations(doc, entry)]

        assert violations == []

    @pytest.mark.parametrize("doc", _doc_params(AREA_DOCS))
    def test_enforced_by_names_existing_tests_review_only_or_pending_issue(self, doc: Path) -> None:
        invalid = [
            f"{_relative(doc)} ({section.title}): Enforced by {value}"
            for section in _sections(doc)
            if section.level == 2
            and (value := dict(_fields(section)[0]).get("Enforced by")) is not None
            and not _enforced_by_is_valid(value)
        ]

        assert invalid == [], "use backticked tests that exist, `review only`, or `pending #<issue>`"

    @pytest.mark.parametrize("doc", _doc_params(ARCHITECTURE_DOCS))
    def test_cited_paths_tests_and_links_resolve_without_line_numbers(self, doc: Path) -> None:
        assert _unresolved_references(doc) == []

    @pytest.mark.parametrize("doc", _doc_params(ARCHITECTURE_DOCS))
    def test_file_stays_under_line_limit(self, doc: Path) -> None:
        count = len(_doc_lines(doc))

        assert count < MAX_FILE_LINES, f"{_relative(doc)} has {count} lines; split the area or trim entries"

    def test_index_lists_every_area_file(self) -> None:
        index = INDEX.read_text(encoding="utf-8")

        unlisted = [_relative(doc) for doc in AREA_DOCS if f"]({doc.name})" not in index]

        assert unlisted == [], f"list each area file in {_relative(INDEX)}"
