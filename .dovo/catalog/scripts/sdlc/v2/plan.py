#!/usr/bin/env python3
"""Checks the slice plan and phase files the issue-code-v2 blueprint's planning steps write, keeps its decisions on the issue, and lists the issue's numbered items.

`check-questions` runs after `fold-answers`, which settles every open question
as a decision. It refuses a plan whose `open_questions` is not `[]`, or whose
`decisions` holds an entry that is not a well-formed decision (the shape is
`Decision` in plan_formats.py) or reuses another's id. Given `--slice`, it
also refuses a decision whose `phases` name a phase the slice does not have.
A plan that passes prints nothing and exits 0.

`check-breakdown` runs after `breakdown`, which writes one phase file per
phase of the slice to .agentic/phases/ (the shape is `Phase` in
plan_formats.py). It refuses, naming every problem it finds: a file that is
not a well-formed phase, or not `phase-<n>.json` for phase n of the slice; a
phase of the slice (plan.json `slices[].phases`) with no file, or a file for
no phase of it; a phase that depends on a phase that is not an earlier one of
the slice; a quoted requirement requirements.json does not list, or whose text
differs from it; a requirement of requirements.json no action cites; a phase
missing a plan.json decision whose `phases` include it, or that names no phase
and so applies to every phase, or listing one that applies to neither, or one
plan.json does not have; and a phase whose `binding` is not an exact copy of
the code blocks under `### Code` in plan.md's `## Phase <n>` section, whose
`departures` and `tests` are not copies of the list items under that section's
`### Decisions` and `### Tests`, or whose `owned_elsewhere` is not a copy of
plan.md's `## Owned elsewhere` list. Quotes and list items are compared with
every run of whitespace as one space. A list item is a top-level one, starting
at column 0 with `- `, `* `, `+ ` or `<n>. `, and runs until the next, a blank
line or a heading. Code fences follow CommonMark. A refusal about a quote, the
binding or a copied list shows the text expected, JSON-encoded. A breakdown
that passes prints nothing, exits 0 and has .agentic/phases/index.json written
from it.

`decisions-comment` runs once the plan is approved. It posts one comment
listing the plan's decisions to the issue, headed by a
`<!-- dovo:decisions ... -->` marker naming the slice, the session and
`body_sha`, the sha256 of the part of the issue's description a person wrote
(`body_sha` below), as prior-decisions.json recorded it before planning, so
the comment names the description the plan was written against. The comment
is dated with the latest decision's date. Then it hides, as outdated, each
earlier dovo:decisions comment of another session that is not hidden and whose
every decision this plan replaces. It never edits a comment, and a plan with
no decisions posts nothing.

`prior-decisions` runs before planning. It writes to prior-decisions.json the
description's `body_sha` and the decisions in the issue's dovo:decisions
comments that are not hidden and that no other decision replaces (the shape is
`PriorDecision` in plan_formats.py): each is `given` when its comment's
body_sha matches the description now, and `to_confirm` otherwise. A hidden
comment's own decisions are left out, but the decisions it says it replaces
stay replaced, so hiding a comment never brings back what it replaced.

`requirements` runs in the ready check. It writes to requirements.json each
numbered item of the issue's description with its exact text (the shape is
`Requirement` in plan_formats.py). A short issue, one with an `## Outcomes`
section, numbers its outcomes `- **O-<n>** ...` and the decisions under
`## Decisions` `- **D-<n>** ...`. A legacy issue numbers `### FR-<n>: ...` and
`### NFR-<n>: ...` sections, and the numbered list under
`## Error cases to handle`, whose items become `EC-<n>` by their position
in it, whatever number each is written with. A list item's indented
continuation lines join its text with one space each; a section's text is its
heading's words, a blank line, then its body. Anything inside a fenced code
block is never read as an item. A description that numbers one id twice is
refused; one with no numbered items writes an empty list.

Every refusal is one line on stderr and exit code 1.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from plan_formats import (
    SESSION_ID,
    Decision,
    FormatError,
    Phase,
    PriorDecision,
    Requirement,
    phase_index_json,
    prior_decisions_json,
    requirements_json,
)

PLAN_JSON = Path(".agentic/plan.json")
PLAN_MD = Path(".agentic/plan.md")
PHASES_DIR = Path(".agentic/phases")
PHASE_NAME = re.compile(r"phase-(?P<phase>[1-9]\d*)\.json")
PHASE_HEADING = re.compile(r"## Phase (?P<phase>\d+)\b")
CODE_HEADING = "### Code"
DECISIONS_HEADING = "### Decisions"
TESTS_HEADING = "### Tests"
OWNED_ELSEWHERE = "## Owned elsewhere"
TOP_ITEM = re.compile(r"(?:[-*+]|\d+\.) (?P<text>.*)")
ATX_HEADING = re.compile(r"#{1,6}(?:[ \t]|$)")
PRIOR_JSON = Path(".agentic/prior-decisions.json")
REQUIREMENTS_JSON = Path(".agentic/requirements.json")
OUTCOMES = "## Outcomes"
SHORT_ITEMS = {
    OUTCOMES: ("outcome", re.compile(r"- \*\*(?P<id>O-\d+)\*\* (?P<text>.+)")),
    "## Decisions": ("decision", re.compile(r"- \*\*(?P<id>D-\d+)\*\* (?P<text>.+)")),
}
LEGACY_HEADING = re.compile(r"### (?P<id>N?FR-\d+)\b:?[ \t]*(?P<text>.*)")
LEGACY_KINDS = {"FR": "functional", "NFR": "non_functional"}
ERROR_CASES = "## Error cases to handle"
ERROR_CASE = re.compile(r"(?P<id>\d+)\. (?P<text>.+)")
FENCE_OPEN = re.compile(r" {0,3}(?P<fence>`{3,}|~{3,})")
MARKER = "<!-- dovo:decisions schema_version=1 slice={slice} session={session} body_sha={body_sha} -->"
MARKER_RE = re.compile(
    r"<!-- dovo:decisions schema_version=1 slice=(?P<slice>\S+) session=(?P<session>\S+)"
    r" body_sha=(?P<body_sha>[0-9a-f]{64}) -->"
)
BODY_SHA = re.compile(r"[0-9a-f]{64}")
BULLET_RE = re.compile(r"- \*\*(?P<id>D-\d+)\*\* (?P<text>.+)")
REPLACES_RE = re.compile(r"Replaces (?P<id>D-\d+) \(session (?P<session>[^\s()]+)\)\.$")
GENERATED_SECTIONS = frozenset({"## Feature contract", "## Work plan"})
SETTLED_LABELS = {"assumed_accepted": "Assumed answer, accepted", "planner": "Planner, approved with the plan"}
MINIMIZE = "mutation($id: ID!) { minimizeComment(input: {subjectId: $id, classifier: OUTDATED}) { clientMutationId } }"


class PlanError(RuntimeError):
    """Raised when a subcommand refuses; the message is the one-line reason."""


@dataclass(frozen=True)
class PostedComment:
    """One of the issue's dovo:decisions comments, with its decision bullets as (id, text) and whether it is hidden."""

    node_id: str
    slice: str
    session: str
    body_sha: str
    bullets: tuple[tuple[str, str], ...]
    hidden: bool


def run(*args: str) -> str:
    """Run a command, raising on a non-zero exit, and return its stripped stdout.

    Args:
        args: Command and arguments to execute.

    Returns:
        The command's stdout with surrounding whitespace removed.
    """
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()


def _with_file(text: str, command: Callable[[str], str]) -> str:
    """Write text to a temporary file, run command with its path and return the command's output."""
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as handle:
        handle.write(text)
    path = Path(handle.name)
    try:
        return command(str(path))
    finally:
        path.unlink(missing_ok=True)


def load_plan(path: Path) -> dict[str, object]:
    """Return the parsed plan at path.

    Raises:
        PlanError: The plan cannot be read, is not valid JSON or is not a JSON object.
    """
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PlanError(f"{path} cannot be read: {error}.") from error
    if not isinstance(plan, dict):
        raise PlanError(f"{path} is not a JSON object.")
    return plan


def _list(plan: dict[str, object], key: str, path: Path) -> list[object]:
    """Return plan[key] when it is a list.

    Raises:
        PlanError: plan[key] is missing or not a list.
    """
    value = plan.get(key)
    if not isinstance(value, list):
        raise PlanError(f"{path} has no {key} list.")
    return value


def _question_id(item: object, position: int) -> str:
    """Return an open question's id, or its position (from 1) when it has none."""
    ident = item.get("id") if isinstance(item, dict) else None
    return ident if isinstance(ident, str) else f"at position {position}"


def load_decisions(plan: dict[str, object], path: Path) -> list[Decision]:
    """Return the plan's decisions, in order.

    Raises:
        PlanError: decisions is missing, holds a malformed decision or reuses an id.
    """
    decisions: list[Decision] = []
    for position, raw in enumerate(_list(plan, "decisions", path), start=1):
        try:
            decision = Decision.from_json(raw, position)
        except FormatError as error:
            raise PlanError(f"{path}: {error}") from error
        if any(earlier.id == decision.id for earlier in decisions):
            raise PlanError(f"{path}: Decision {decision.id} appears twice.")
        decisions.append(decision)
    return decisions


def check_questions(path: Path, slice_id: str | None) -> None:
    """Refuse the plan at path unless every open question is settled as a well-formed decision of the slice.

    Raises:
        PlanError: open_questions is not [], decisions is missing, malformed or reuses an id, or, given
            slice_id, a decision names a phase the slice does not have.
    """
    plan = load_plan(path)
    questions = _list(plan, "open_questions", path)
    if questions:
        named = ", ".join(_question_id(item, position) for position, item in enumerate(questions, start=1))
        raise PlanError(f"{path} still lists open questions {named}; fold-answers must settle each one as a decision.")
    decisions = load_decisions(plan, path)
    if slice_id is None:
        return
    numbers = slice_phases(plan, slice_id, path)
    stray = [
        f"Decision {decision.id} names phase {', '.join(str(p) for p in outside)}"
        for decision in decisions
        if (outside := [p for p in decision.phases if p not in numbers])
    ]
    if stray:
        listed = ", ".join(str(n) for n in numbers)
        raise PlanError(f"{path}: {'; '.join(stray)}, which slice {slice_id} does not have; its phases are {listed}.")


# --- the phase breakdown ---------------------------------------------------------


def slice_phases(plan: dict[str, object], slice_id: str, path: Path) -> list[int]:
    """Return the phase numbers plan.json's slices entry for slice_id lists, in order.

    Raises:
        PlanError: slices is missing, the slice is not in it, or the slice has no phases.
    """
    rows = [row for row in _list(plan, "slices", path) if isinstance(row, dict) and row.get("id") == slice_id]
    phases = rows[0].get("phases") if rows else None
    numbers = [number for number in phases if isinstance(number, int)] if isinstance(phases, list) else []
    if not numbers:
        raise PlanError(f"{path} lists no phases for slice {slice_id}.")
    return numbers


def load_requirements(path: Path) -> list[Requirement]:
    """Return the requirements requirements.json at path lists.

    Raises:
        PlanError: The file cannot be read, or has no requirements list of well-formed entries.
    """
    document = load_plan(path)
    try:
        return [Requirement.from_json(raw, n) for n, raw in enumerate(_list(document, "requirements", path), start=1)]
    except FormatError as error:
        raise PlanError(f"{path}: {error}") from error


def _read_phase(path: Path, slice_id: str) -> Phase:
    """Return the phase in the file at path, which must be phase-<n>.json for phase n of slice_id.

    Raises:
        FormatError: The file is not valid JSON, is no well-formed phase, or names another phase or slice.
    """
    named = PHASE_NAME.fullmatch(path.name)
    if named is None:
        raise FormatError(
            f"{path.name} is not named phase-<n>.json for a phase number n, written without leading zeros."
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise FormatError(f"{path.name} is not valid JSON: {error}.") from error
    phase = Phase.from_json(raw, path.name)
    if phase.phase != int(named["phase"]) or phase.slice != slice_id:
        expected = f"phase {named['phase']} of slice {slice_id}"
        raise FormatError(f"{path.name} holds phase {phase.phase} of slice {phase.slice}; it must hold {expected}.")
    return phase


def load_phases(folder: Path, slice_id: str) -> dict[int, Phase]:
    """Return the phases of slice_id in folder's phase-*.json files, by phase number.

    Raises:
        PlanError: A file is not a well-formed phase of the slice; the message names every such file.
    """
    phases: dict[int, Phase] = {}
    problems: list[str] = []
    for path in sorted(folder.glob("phase-*.json")):
        try:
            phase = _read_phase(path, slice_id)
        except FormatError as error:
            problems.append(str(error))
        else:
            phases[phase.phase] = phase
    if problems:
        raise PlanError(f"{folder} has malformed phase files: {' '.join(problems)}")
    return phases


def _file_problems(numbers: list[int], phases: dict[int, Phase], slice_id: str) -> list[str]:
    """Return a line for each phase of the slice with no file, and each file of no phase of the slice."""
    missing = [f"Phase {n} has no phase-{n}.json." for n in numbers if n not in phases]
    listed = ", ".join(str(n) for n in numbers)
    extra = [
        f"phase-{n}.json is not a phase of slice {slice_id}, whose phases are {listed}."
        for n in phases
        if n not in numbers
    ]
    return [*missing, *extra]


def _quote_problems(phases: dict[int, Phase], required: list[Requirement]) -> list[str]:
    """Return a line for each quoted requirement requirements.json does not list or words differently.

    Quotes are compared with every run of whitespace as one space.
    """
    texts = {requirement.id: requirement.text for requirement in required}
    problems: list[str] = []
    for n, phase in phases.items():
        for quote in phase.requirements:
            if quote.id not in texts:
                problems.append(f"phase-{n}.json quotes {quote.id}, which requirements.json does not list.")
            elif _words(quote.text) != _words(texts[quote.id]):
                problems.append(
                    f"phase-{n}.json quotes {quote.id} in words that differ from requirements.json, "
                    f"which has {json.dumps(texts[quote.id])}."
                )
    return problems


def _coverage_problems(phases: dict[int, Phase], required: list[Requirement]) -> list[str]:
    """Return a line naming every requirement of requirements.json that no action of any phase cites."""
    cited = {ident for phase in phases.values() for action in phase.actions for ident in action.requirements}
    uncovered = [requirement.id for requirement in required if requirement.id not in cited]
    return [f"No action covers {', '.join(uncovered)}."] if uncovered else []


def _decision_problems(phases: dict[int, Phase], decisions: list[Decision]) -> list[str]:
    """Return a line for each phase missing a decision that applies to it, or listing one that does not.

    A decision applies to the phases its `phases` name, and to every phase when it names none.
    """
    known = {decision.id for decision in decisions}
    every = [decision.id for decision in decisions if not decision.phases]
    problems: list[str] = []
    for n, phase in phases.items():
        named = [decision.id for decision in decisions if n in decision.phases]
        problems.extend(_phase_decision_problems(n, phase, named, every))
        problems.extend(_listed_decision_problems(n, phase, {*named, *every}, known))
    return problems


def _phase_decision_problems(n: int, phase: Phase, named: list[str], every: list[str]) -> list[str]:
    """Return phase n's lines for missing decisions that name it or that name no phase."""
    problems: list[str] = []
    if missing := [ident for ident in named if ident not in phase.decisions]:
        problems.append(f"phase-{n}.json lacks {', '.join(missing)}, whose phases in plan.json include {n}.")
    if missing := [ident for ident in every if ident not in phase.decisions]:
        problems.append(f"phase-{n}.json lacks {', '.join(missing)}; a decision with no phases applies to every phase.")
    return problems


def _listed_decision_problems(n: int, phase: Phase, applying: set[str], known: set[str]) -> list[str]:
    """Return phase n's lines for the decisions it lists outside applying: ones plan.json has, then ones it lacks."""
    listed = [ident for ident in phase.decisions if ident not in applying]
    problems: list[str] = []
    if other := [ident for ident in listed if ident in known]:
        problems.append(f"phase-{n}.json lists {', '.join(other)}, whose phases in plan.json do not include {n}.")
    if unknown := [ident for ident in listed if ident not in known]:
        problems.append(f"phase-{n}.json lists {', '.join(unknown)}, which plan.json does not have.")
    return problems


def _dependency_problems(numbers: list[int], phases: dict[int, Phase], slice_id: str) -> list[str]:
    """Return a line for each phase of the slice that depends on a phase that is not an earlier one of it.

    A file for no phase of the slice is already reported by _file_problems, so it is skipped here.
    """
    problems: list[str] = []
    for n, phase in phases.items():
        if n not in numbers:
            continue
        earlier = numbers[: numbers.index(n)]
        if wrong := [str(d) for d in phase.depends_on if d not in earlier]:
            problems.append(
                f"phase-{n}.json depends on phase {', '.join(wrong)}, which must each be an earlier phase of slice {slice_id}."
            )
    return problems


def _code_blocks(lines: list[str]) -> list[str]:
    """Return the text inside each closed fenced code block in lines, without its fences."""
    blocks: list[str] = []
    current: list[str] = []
    for line, role in _fence_roles(lines):
        if role == "open":
            current = []
        elif role == "close":
            blocks.append("\n".join(current))
        elif role == "code":
            current.append(line)
    return blocks


def _top_items(lines: list[tuple[str, bool]]) -> list[str]:
    """Return the text of each top-level list item in lines, its lines joined with one space.

    A top-level item starts at column 0 with `- `, `* `, `+ ` or `<n>. ` and runs
    until the next one, a blank line or a heading, taking in its unindented
    (lazy) continuation lines and its nested items.
    """
    items: list[list[str]] = []
    current: list[str] | None = None
    for line, markup in lines:
        match = TOP_ITEM.fullmatch(line) if markup else None
        if match is not None:
            current = [match["text"]]
            items.append(current)
        elif _ends_item(line, markup):
            current = None
        elif current is not None:
            current.append(line.strip())
    return [" ".join(parts) for parts in items]


def _ends_item(line: str, markup: bool) -> bool:
    """Return whether line ends the list item before it: a blank line or a heading."""
    return not line.strip() or (markup and ATX_HEADING.match(line) is not None)


def _subsections(lines: list[tuple[str, bool]]) -> dict[str, list[tuple[str, bool]]]:
    """Return a section's lines under each of its `### ` headings, by heading."""
    found: dict[str, list[tuple[str, bool]]] = {}
    current: list[tuple[str, bool]] | None = None
    for line, markup in lines:
        if markup and line.startswith("### "):
            current = found.setdefault(line, [])
        elif current is not None:
            current.append((line, markup))
    return found


@dataclass(frozen=True)
class PlanCopies:
    """What phase files copy from plan.md: each phase section's code blocks, departures and test lines, and the Owned elsewhere items."""

    code: dict[int, list[str]]
    departures: dict[int, list[str]]
    tests: dict[int, list[str]]
    owned: list[str]


def plan_copies(path: Path) -> PlanCopies:
    """Return what plan.md's `## Phase <n>` sections and `## Owned elsewhere` list hold for the phase files to copy.

    Raises:
        PlanError: plan.md cannot be read.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise PlanError(f"{path} cannot be read: {error}.") from error
    copies = PlanCopies(code={}, departures={}, tests={}, owned=[])
    for heading, lines in _sections(_lines(text)):
        if match := PHASE_HEADING.match(heading):
            n = int(match["phase"])
            parts = _subsections(lines)
            copies.code[n] = _code_blocks([line for line, _ in parts.get(CODE_HEADING, [])])
            copies.departures[n] = _top_items(parts.get(DECISIONS_HEADING, []))
            copies.tests[n] = _top_items(parts.get(TESTS_HEADING, []))
        elif heading == OWNED_ELSEWHERE:
            copies.owned.extend(_top_items(lines))
    return copies


def _block(text: str) -> str:
    """Return a code block as compared: line endings as LF, no trailing spaces, no blank lines around it."""
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").split("\n")).strip("\n")


def _words(text: str) -> str:
    """Return text with every run of whitespace as one space, as a quote or a copied list item is compared."""
    return " ".join(text.split())


def _binding_problem(n: int, binding: tuple[str, ...], code: list[str]) -> list[str]:
    """Return a line when phase n's binding is not an exact copy of its plan.md section's code blocks."""
    expected = [_block(block) for block in code]
    if [_block(block) for block in binding] == expected:
        return []
    return [
        f"phase-{n}.json's binding does not copy, exactly and in order, the {len(expected)} code block(s) "
        f"under {CODE_HEADING} in plan.md's ## Phase {n}, which are {json.dumps(expected)}."
    ]


def _items_problem(n: int, field: str, copied: tuple[str, ...], items: list[str], where: str) -> list[str]:
    """Return a line when phase n's field is not a copy of the plan.md list items found where."""
    expected = [_words(item) for item in items]
    if [_words(item) for item in copied] == expected:
        return []
    return [
        f"phase-{n}.json's {field} does not copy the {len(expected)} item(s) {where}, which are {json.dumps(expected)}."
    ]


def _copy_problems(phases: dict[int, Phase], copies: PlanCopies) -> list[str]:
    """Return a line for each phase whose binding, departures, tests or owned_elsewhere is not a copy of plan.md's."""
    problems: list[str] = []
    for n, phase in phases.items():
        section = f"in plan.md's ## Phase {n}"
        problems += _binding_problem(n, phase.binding, copies.code.get(n, []))
        problems += _items_problem(
            n, "departures", phase.departures, copies.departures.get(n, []), f"under {DECISIONS_HEADING} {section}"
        )
        problems += _items_problem(n, "tests", phase.tests, copies.tests.get(n, []), f"under {TESTS_HEADING} {section}")
        problems += _items_problem(
            n, "owned_elsewhere", phase.owned_elsewhere, copies.owned, f"of plan.md's {OWNED_ELSEWHERE}"
        )
    return problems


def check_breakdown(plan_path: Path, plan_md: Path, requirements_path: Path, folder: Path, slice_id: str) -> None:
    """Refuse a phase breakdown that drops or misquotes anything, else write folder's index.json.

    Raises:
        PlanError: An input is unreadable or malformed, or the phase files do not break down the slice.
    """
    plan = load_plan(plan_path)
    numbers = slice_phases(plan, slice_id, plan_path)
    decisions = load_decisions(plan, plan_path)
    copies = plan_copies(plan_md)
    required = load_requirements(requirements_path)
    phases = load_phases(folder, slice_id)
    problems = [
        *_file_problems(numbers, phases, slice_id),
        *_dependency_problems(numbers, phases, slice_id),
        *_quote_problems(phases, required),
        *_coverage_problems(phases, required),
        *_decision_problems(phases, decisions),
        *_copy_problems(phases, copies),
    ]
    if problems:
        raise PlanError(f"{folder} does not break down slice {slice_id}: {' '.join(problems)}")
    entries = [phases[n].to_index(str(folder / f"phase-{n}.json")) for n in numbers]
    index = json.dumps(phase_index_json(slice_id, entries), indent=2) + "\n"
    (folder / "index.json").write_text(index, encoding="utf-8")


# --- the dovo:decisions comment ------------------------------------------------


def body_sha(body: str) -> str:
    """Return the sha256 hex of the part of an issue's description a person wrote.

    Line endings become LF, each `## Feature contract` and `## Work plan` section
    (from its heading to the next `## ` heading outside a fenced code block, or
    the end) is cut, every line loses its trailing whitespace, and the text loses
    its leading and trailing newlines, so a work plan rewrite or a stray space
    never marks a decision stale.
    """
    kept: list[str] = []
    cutting = False
    for line, role in _fence_roles(body.replace("\r\n", "\n").replace("\r", "\n").split("\n")):
        if role == "text" and line.startswith("## "):
            cutting = line.rstrip() in GENERATED_SECTIONS
        if not cutting:
            kept.append(line.rstrip())
    return hashlib.sha256("\n".join(kept).strip("\n").encode("utf-8")).hexdigest()


def _sentence(text: str) -> str:
    """Return text on one line, without its closing full stop."""
    return " ".join(text.split()).rstrip(".")


def _label(decision: Decision) -> str:
    """Return the italic label saying who settled decision."""
    if decision.settled_by == "human":
        return f"Answered: {_sentence(decision.answer or '')}"
    return SETTLED_LABELS[decision.settled_by]


def render_bullet(decision: Decision) -> str:
    """Return decision's one-line bullet: its text, who settled it, what it rejected and what it replaces."""
    parts = [f"- **{decision.id}** {_sentence(decision.decision)}. *{_label(decision)}.*"]
    if decision.rejected:
        parts.append(f"Rejected: {'; '.join(_sentence(text) for text in decision.rejected)}.")
    if decision.replaces is not None:
        parts.append(f"Replaces {decision.replaces.id} (session {decision.replaces.session}).")
    return " ".join(parts)


def render_comment(decisions: list[Decision], slice_id: str, session: str, sha: str) -> str:
    """Return the dovo:decisions comment listing decisions, dated with the latest decision's date."""
    day = max(decision.date for decision in decisions)
    lines = [
        MARKER.format(slice=slice_id, session=session, body_sha=sha),
        f"**Planning decisions** ({day}, slice {slice_id}, session {session})",
        "",
        *(render_bullet(decision) for decision in decisions),
    ]
    return "\n".join(lines) + "\n"


def _posted(raw: object) -> PostedComment | None:
    """Return raw as a dovo:decisions comment, hidden or not, or None when it is another comment."""
    if not isinstance(raw, dict):
        return None
    body = str(raw.get("body") or "")
    marker = MARKER_RE.match(body)
    if marker is None:
        return None
    lines = (BULLET_RE.fullmatch(line.rstrip()) for line in body.splitlines())
    bullets = tuple((match["id"], match["text"]) for match in lines if match is not None)
    hidden = bool(raw.get("isMinimized"))
    return PostedComment(str(raw.get("id")), marker["slice"], marker["session"], marker["body_sha"], bullets, hidden)


def _view(issue: int, fields: str) -> dict[str, object]:
    """Return gh's JSON view of issue's fields.

    Raises:
        PlanError: gh printed something other than a JSON object.
    """
    try:
        view = json.loads(run("gh", "issue", "view", str(issue), "--json", fields))
    except ValueError as error:
        raise PlanError(f"gh issue view {issue} printed no JSON: {error}.") from error
    if not isinstance(view, dict):
        raise PlanError(f"gh issue view {issue} printed no JSON object.")
    return view


def view_issue(issue: int) -> tuple[str, list[PostedComment]]:
    """Return issue's description and its dovo:decisions comments, hidden ones included.

    Raises:
        PlanError: gh printed something other than a JSON object.
    """
    view = _view(issue, "body,comments")
    comments = view.get("comments")
    found = (_posted(raw) for raw in (comments if isinstance(comments, list) else []))
    return str(view.get("body") or ""), [comment for comment in found if comment is not None]


def _replaced(text: str) -> tuple[str, str] | None:
    """Return the (id, session) a posted bullet's text says it replaces, or None."""
    match = REPLACES_RE.search(text)
    return (match["id"], match["session"]) if match else None


def _token(value: str, what: str) -> str:
    """Return value when it is one marker token: non-empty, without whitespace or parentheses.

    Raises:
        PlanError: value is empty or would break the marker.
    """
    if not SESSION_ID.fullmatch(value):
        raise PlanError(f"{what} {value!r} must be non-empty, without whitespace or parentheses.")
    return value


def _hide(comment: PostedComment) -> None:
    """Minimize comment as outdated, reporting on stderr rather than refusing when gh fails."""
    try:
        run("gh", "api", "graphql", "-f", f"query={MINIMIZE}", "-f", f"id={comment.node_id}")
    except subprocess.CalledProcessError as error:
        stderr = str(error.stderr or "").strip()
        sys.stderr.write(f"plan.py: could not hide comment {comment.node_id} of session {comment.session}: {stderr}\n")


def recorded_body_sha(path: Path) -> str:
    """Return the description's body_sha that prior-decisions recorded in the prior-decisions.json at path.

    Raises:
        PlanError: The file cannot be read, is not a JSON object or has no body_sha.
    """
    sha = load_plan(path).get("body_sha")
    if not isinstance(sha, str) or not BODY_SHA.fullmatch(sha):
        raise PlanError(f"{path} has no body_sha; run prior-decisions before planning.")
    return sha


def decisions_comment(path: Path, prior: Path, issue: int, slice_id: str, session: str) -> None:
    """Post the plan's decisions to issue, then hide the earlier comments they wholly replace.

    Raises:
        PlanError: The plan or its decisions are malformed, slice_id or session is not one token, or prior
            records no body_sha.
    """
    decisions = load_decisions(load_plan(path), path)
    _token(slice_id, "Slice")
    _token(session, "Session")
    if not decisions:
        return
    sha = recorded_body_sha(prior)
    _, posted = view_issue(issue)
    comment = render_comment(decisions, slice_id, session, sha)
    _with_file(comment, lambda file: run("gh", "issue", "comment", str(issue), "--body-file", file))
    for earlier in outdated(posted, decisions, session):
        _hide(earlier)


def outdated(posted: list[PostedComment], decisions: list[Decision], session: str) -> list[PostedComment]:
    """Return the comments, not hidden, of other sessions than session whose every decision one of decisions replaces."""
    replaced = {(item.replaces.id, item.replaces.session) for item in decisions if item.replaces is not None}
    return [
        earlier
        for earlier in posted
        if not earlier.hidden
        and earlier.session != session
        and earlier.bullets
        and all((ident, earlier.session) in replaced for ident, _ in earlier.bullets)
    ]


def prior_decisions(issue: int, out: Path) -> None:
    """Write to out the description's body_sha and the decisions of issue's dovo:decisions comments that are not hidden and that no decision replaces.

    The decisions a hidden comment says it replaces stay replaced.
    """
    body, posted = view_issue(issue)
    sha = body_sha(body)
    replaced = {pair for comment in posted for _, text in comment.bullets if (pair := _replaced(text)) is not None}
    decisions = [
        PriorDecision(ident, text, comment.slice, comment.session, "given" if comment.body_sha == sha else "to_confirm")
        for comment in posted
        if not comment.hidden
        for ident, text in comment.bullets
        if (ident, comment.session) not in replaced
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(prior_decisions_json(issue, sha, decisions), indent=2) + "\n", encoding="utf-8")


# --- requirements.json -----------------------------------------------------------


def _fence_roles(lines: list[str]) -> Iterator[tuple[str, str]]:
    """Yield each line with its role: `open` or `close` for a code fence, `code` inside a fenced block, else `text`.

    Fences follow CommonMark: a block opens on a line indented at most 3 spaces
    that starts with 3 or more backticks or tildes, and closes only on a line of
    the same character, at least as long, indented at most 3 spaces and with
    nothing after it but whitespace. A block left open runs to the end of lines.
    """
    closing: re.Pattern[str] | None = None
    for line in lines:
        if closing is None:
            opening = FENCE_OPEN.match(line)
            if opening is not None:
                fence = opening["fence"]
                closing = re.compile(rf" {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*")
            yield line, "text" if opening is None else "open"
        elif closing.fullmatch(line):
            closing = None
            yield line, "close"
        else:
            yield line, "code"


def _lines(body: str) -> list[tuple[str, bool]]:
    """Return body's lines without trailing whitespace, each paired with whether it is markup.

    A line is markup when it is outside a fenced code block and is not a fence
    itself, so a heading or a list item quoted in a code block is never read.
    """
    split = body.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return [(line.rstrip(), role == "text") for line, role in _fence_roles(split)]


def _sections(lines: list[tuple[str, bool]]) -> list[tuple[str, list[tuple[str, bool]]]]:
    """Return lines split at each `## ` heading, as (heading, lines under it); text before any heading has ""."""
    sections: list[tuple[str, list[tuple[str, bool]]]] = [("", [])]
    for line, markup in lines:
        if markup and line.startswith("## "):
            sections.append((line, []))
        else:
            sections[-1][1].append((line, markup))
    return sections


def _list_items(lines: list[tuple[str, bool]], pattern: re.Pattern[str]) -> list[tuple[str, str]]:
    """Return the (id, text) of each list item pattern matches, joining its indented continuation lines."""
    items: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    for line, markup in lines:
        match = pattern.fullmatch(line) if markup else None
        if match is not None:
            current = [match["text"]]
            items.append((match["id"], current))
        elif current is not None and line[:1].isspace() and line.strip():
            current.append(line.strip())
        else:
            current = None
    return [(ident, " ".join(parts)) for ident, parts in items]


def _headed_items(lines: list[tuple[str, bool]]) -> list[tuple[str, str]]:
    """Return the (id, text) of each `### FR-<n>` or `### NFR-<n>` section: its heading's words, then its body."""
    items: list[tuple[str, str, list[str]]] = []
    current: list[str] | None = None
    for line, markup in lines:
        match = LEGACY_HEADING.fullmatch(line) if markup else None
        if match is not None:
            current = []
            items.append((match["id"], match["text"], current))
        elif markup and line.startswith("#"):
            current = None
        elif current is not None:
            current.append(line)
    return [(ident, _section_text(title, body)) for ident, title, body in items]


def _section_text(title: str, body: list[str]) -> str:
    """Return a section's text: its heading's words and its body, a blank line apart, leaving out either when empty."""
    return "\n\n".join(part for part in (title, "\n".join(body).strip()) if part)


def _short_requirements(sections: list[tuple[str, list[tuple[str, bool]]]]) -> list[Requirement]:
    """Return a short issue's outcomes and decisions, in the description's order."""
    found: list[Requirement] = []
    for heading, lines in sections:
        if heading in SHORT_ITEMS:
            kind, pattern = SHORT_ITEMS[heading]
            found.extend(Requirement(ident, kind, text) for ident, text in _list_items(lines, pattern))
    return found


def _legacy_requirements(sections: list[tuple[str, list[tuple[str, bool]]]]) -> list[Requirement]:
    """Return a legacy issue's FR-<n> and NFR-<n> sections and numbered error cases, in the description's order."""
    found: list[Requirement] = []
    for heading, lines in sections:
        found.extend(
            Requirement(ident, LEGACY_KINDS[ident.split("-")[0]], text) for ident, text in _headed_items(lines)
        )
        if heading == ERROR_CASES:
            cases = enumerate(_list_items(lines, ERROR_CASE), start=1)
            found.extend(Requirement(f"EC-{position}", "error_case", text) for position, (_, text) in cases)
    return found


def parse_requirements(body: str) -> tuple[str, list[Requirement]]:
    """Return a description's format, `short` when it has an `## Outcomes` section and `legacy` otherwise, and its items."""
    sections = _sections(_lines(body))
    if any(heading == OUTCOMES for heading, _ in sections):
        return "short", _short_requirements(sections)
    return "legacy", _legacy_requirements(sections)


def requirements(issue: int, out: Path) -> None:
    """Write to out the numbered items of issue's description, each with its exact text.

    Raises:
        PlanError: The description numbers one id twice.
    """
    issue_format, found = parse_requirements(str(_view(issue, "body").get("body") or ""))
    seen: set[str] = set()
    for requirement in found:
        if requirement.id in seen:
            raise PlanError(f"Issue {issue} numbers {requirement.id} twice; give each item its own id.")
        seen.add(requirement.id)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(requirements_json(issue, issue_format, found), indent=2) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    """Return the parser for the check-questions, check-breakdown, decisions-comment, prior-decisions and requirements subcommands."""
    parser = argparse.ArgumentParser(description="Check the slice plan in .agentic/plan.json and keep its decisions.")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-questions", help="Refuse a plan with an open question or a malformed decision.")
    check.add_argument("--plan", type=Path, default=PLAN_JSON, help="The plan.json to check.")
    check.add_argument("--slice", help="The slice whose phases every decision's phases must belong to.")
    breakdown = commands.add_parser("check-breakdown", help="Refuse phase files that drop or misquote anything.")
    breakdown.add_argument("--slice", required=True, help="The slice the phase files break down.")
    breakdown.add_argument("--plan", type=Path, default=PLAN_JSON, help="The plan.json listing the slice's phases.")
    breakdown.add_argument("--plan-md", type=Path, default=PLAN_MD, help="The plan.md the phase files copy from.")
    breakdown.add_argument("--requirements", type=Path, default=REQUIREMENTS_JSON, help="The requirements.json.")
    breakdown.add_argument("--phases", type=Path, default=PHASES_DIR, help="The folder holding the phase files.")
    comment = commands.add_parser("decisions-comment", help="Post the approved plan's decisions to the issue.")
    comment.add_argument("--plan", type=Path, default=PLAN_JSON, help="The approved plan.json.")
    comment.add_argument(
        "--prior", type=Path, default=PRIOR_JSON, help="The prior-decisions.json whose body_sha the comment names."
    )
    comment.add_argument("--issue", type=int, required=True, help="The issue to post the comment on.")
    comment.add_argument("--slice", required=True, help="The slice the plan phases.")
    comment.add_argument("--session", required=True, help="The planning session's id.")
    prior = commands.add_parser("prior-decisions", help="Write the decisions earlier sessions posted to the issue.")
    prior.add_argument("--issue", type=int, required=True, help="The issue whose comments to read.")
    prior.add_argument("--out", type=Path, default=PRIOR_JSON, help="The prior-decisions.json to write.")
    listed = commands.add_parser("requirements", help="Write the numbered items of the issue's description.")
    listed.add_argument("--issue", type=int, required=True, help="The issue whose description to read.")
    listed.add_argument("--out", type=Path, default=REQUIREMENTS_JSON, help="The requirements.json to write.")
    return parser


def dispatch(args: argparse.Namespace) -> None:
    """Run the subcommand args names."""
    if args.command == "check-questions":
        check_questions(args.plan, args.slice)
    elif args.command == "check-breakdown":
        check_breakdown(args.plan, args.plan_md, args.requirements, args.phases, args.slice)
    elif args.command == "decisions-comment":
        decisions_comment(args.plan, args.prior, args.issue, args.slice, args.session)
    elif args.command == "prior-decisions":
        prior_decisions(args.issue, args.out)
    else:
        requirements(args.issue, args.out)


def main() -> int:
    """Run one plan.py subcommand."""
    args = build_parser().parse_args()
    try:
        dispatch(args)
    except PlanError as error:
        sys.stderr.write(f"plan.py: {error}\n")
        return 1
    except subprocess.CalledProcessError as error:
        command: list[str] = error.cmd
        stderr = str(error.stderr or "").strip()
        sys.stderr.write(f"plan.py: command failed: {' '.join(command)}: {stderr}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
