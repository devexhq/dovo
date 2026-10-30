#!/usr/bin/env python3
"""Keeps an issue's feature contract and work plan current in one comment on the issue.

The slice flow's plan run writes `## Feature contract` and `## Work plan` with
`render` into the issue's work plan comment, the one comment whose first line is
exactly `<!-- dovo:workplan schema_version=1 -->`, creating it when the issue
has none. A comment whose marker carries anything more, such as
`maintained=by-hand`, is a person's and is never read or written. The issue's
description is never written. Each code run then picks its slice with `next`,
takes it with `claim` once its own plan is approved, and records its pull
request with `land`, which repoints the work plan at the code session when its
report amended later slices or the contract. No child issue is created for a
slice, except by `split-out`, which a person runs to give one slice its own
issue. The work plan is a table for people plus a
`<!-- dovo:workplan {json} -->` line that this script alone parses and
rewrites. Every write re-reads the comment first; when it changed since this
run read it, the change is applied once more to the new text, and a second
change refuses. A write that would make the comment longer than GitHub's
65,536 characters refuses rather than truncating. `next` and `land` write their
outputs as `key=value` lines to `$DOVO_OUTPUT`: `remaining`, the number of
other slices not yet merged, and from `next` the `closing_lines` its pull
request ends with. With `--hold`, `next` also writes the holds file a
blueprint's `ask` step lists: `{"holds": []}` once a slice is picked, or one
hold per slice that waits on a person (an open blocker, an unmerged slice, a
pull request in review), in which case it exits 0 without outputs instead of
refusing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

CONTRACT_HEADING = "## Feature contract"
QUESTIONS_INTRO = "Open questions, each with a recommended answer until the user settles it:"
WORKPLAN_HEADING = "## Work plan"
WORKPLAN_MARKER = "<!-- dovo:workplan schema_version=1 -->"
WORKPLAN_COMMENT_RE = re.compile(r"<!-- dovo:workplan (\{.*\}) -->")
COMMENT_ID_RE = re.compile(r"#issuecomment-(\d+)$")
COMMENT_LIMIT = 65_536
READY_LABEL = "dovo:ready"
ACTIVE_STATUSES = ("in_progress", "in_review")
HEREDOC_DELIMITER = "WORKPLAN_EOF"
NONE_CELL = "—"
SIZES = ("s", "m", "l")
SIZE_LABEL_PREFIX = "dovo-size-"


class WorkplanError(RuntimeError):
    """Raised when a subcommand refuses; the message is the one-line reason."""


class WorkplanWait(WorkplanError):
    """Raised when no slice can start until a person acts; holds maps each waiting slice id to why it waits."""

    def __init__(self, message: str, holds: dict[str, str]) -> None:
        super().__init__(message)
        self.holds = holds


@dataclass
class Slice:
    """One slice row: its definition from plan.json and the state the code runs record.

    outcomes are the issue's O-n ids the slice delivers. child is None unless
    `split-out` gave the slice its own issue.
    """

    id: str
    title: str
    depends_on: list[str]
    blocked_by: list[int]
    closing_docs: bool
    outcomes: list[str] = field(default_factory=list)
    status: str = "pending"
    pr: int | None = None
    session: str | None = None
    size: str | None = None
    child: int | None = None


@dataclass
class WorkPlan:
    """The parsed `dovo:workplan` JSON line: the plan session to load and every slice row."""

    plan_session: str
    plan_revision: int | None
    slices: list[Slice]


@dataclass(frozen=True)
class WorkplanComment:
    """The issue's work plan comment: its id, None until `render` creates it, and its text."""

    id: int | None
    text: str


def run(*args: str) -> str:
    """Run a command, raising on a non-zero exit, and return its stripped stdout.

    Args:
        args: Command and arguments to execute.

    Returns:
        The command's stdout with surrounding whitespace removed.
    """
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()


# --- plan.json ---------------------------------------------------------------


def _object(value: object, what: str) -> dict[str, object]:
    """Return value as a JSON object, or refuse naming what it should have been."""
    if not isinstance(value, dict):
        raise WorkplanError(f"{what} must be a JSON object")
    return {str(key): item for key, item in value.items()}


def _list(value: object) -> list[object]:
    """Return value as a list, treating a missing or null value as empty."""
    return list(value) if isinstance(value, list) else []


def _optional_int(value: object, what: str) -> int | None:
    """Return value as an issue or PR number, or None when it is null."""
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise WorkplanError(f"{what} must be an issue number or null, not {value!r}")
    return value


def _size(value: object, slice_id: str) -> str | None:
    """Return a slice's review size, `s`, `m` or `l`, or None for a plan written before slices carried one."""
    if value is None:
        return None
    if value not in SIZES:
        raise WorkplanError(f"slice {slice_id} size must be s, m or l, not {value!r}")
    return str(value)


def _plan_slice(raw: object) -> Slice:
    """Return one plan.json slice as a pending row."""
    data = _object(raw, "each plan.json slice")
    slice_id = str(data.get("id") or "")
    if not slice_id:
        raise WorkplanError("every plan.json slice needs an id")
    return Slice(
        id=slice_id,
        title=str(data.get("title") or slice_id),
        depends_on=[str(item) for item in _list(data.get("depends_on"))],
        blocked_by=[int(str(item)) for item in _list(data.get("blocked_by"))],
        closing_docs=data.get("closing_docs") is True,
        outcomes=[str(item) for item in _list(data.get("outcomes"))],
        size=_size(data.get("size"), slice_id),
    )


def _check_graph(slices: list[Slice]) -> None:
    """Refuse duplicate slice ids and depends_on entries that name no earlier slice."""
    seen: set[str] = set()
    for row in slices:
        if row.id in seen:
            raise WorkplanError(f"slice {row.id} appears twice in plan.json")
        unknown = [dep for dep in row.depends_on if dep not in seen]
        if unknown:
            raise WorkplanError(f"slice {row.id} depends on {', '.join(unknown)}, which is not an earlier slice")
        seen.add(row.id)


def parse_plan(plan: object) -> tuple[dict[str, object] | None, list[Slice]]:
    """Return plan.json's contract (None for a plain issue) and its slices as pending rows.

    A plan whose `contract` is null is a plain issue's plan: it must have exactly
    one slice.

    Args:
        plan: The parsed plan.json.

    Returns:
        The contract object or None, and the slices in plan order.

    Raises:
        WorkplanError: When the plan has no slices or a slice is malformed.
    """
    data = _object(plan, "plan.json")
    raw_contract = data.get("contract")
    contract = None if raw_contract is None else _object(raw_contract, "plan.json contract")
    raw_slices = _list(data.get("slices"))
    if not raw_slices:
        raise WorkplanError("plan.json has no slices")
    if contract is None and len(raw_slices) != 1:
        raise WorkplanError(f"a plan without a contract must have one slice, not {len(raw_slices)}")
    slices = [_plan_slice(raw) for raw in raw_slices]
    _check_graph(slices)
    return contract, slices


def merge_rows(plan_slices: list[Slice], current: list[Slice]) -> list[Slice]:
    """Return plan_slices carrying over the status, PR, session and child each slice already has in current.

    Raises:
        WorkplanError: When the plan drops a slice that has already started.
    """
    planned = {row.id for row in plan_slices}
    dropped = [row.id for row in current if row.id not in planned and row.status != "pending"]
    if dropped:
        raise WorkplanError(f"the plan drops {', '.join(dropped)}, which already started")
    by_id = {row.id: row for row in current}
    return [_carry_over(row, by_id.get(row.id)) for row in plan_slices]


def _definition(row: Slice) -> tuple[object, ...]:
    """Return the fields of row that plan.json defines, as opposed to the state the code runs record."""
    return (row.title, row.depends_on, row.blocked_by, row.closing_docs, row.outcomes)


def check_started_unchanged(plan_slices: list[Slice], current: list[Slice]) -> None:
    """Refuse plan_slices when it redefines a slice that has already started; a report amends only later slices.

    Raises:
        WorkplanError: When a slice that is not pending has a different title, dependencies, closing-docs flag
            or outcomes.
    """
    planned = {row.id: row for row in plan_slices}
    changed = [
        row.id
        for row in current
        if row.status != "pending" and row.id in planned and _definition(planned[row.id]) != _definition(row)
    ]
    if changed:
        raise WorkplanError(f"the plan changes {', '.join(changed)}, which already started")


def _carry_over(row: Slice, old: Slice | None) -> Slice:
    """Return row with old's size (unless row names one), status, PR, session and child."""
    if old is None:
        return row
    size = row.size if row.size is not None else old.size
    return replace(row, size=size, status=old.status, pr=old.pr, session=old.session, child=old.child)


# --- comment sections ---------------------------------------------------------


def _find_block(text: str, is_start: Callable[[str], bool], is_end: Callable[[str], bool]) -> tuple[int, int] | None:
    """Return the span from the first line matching is_start to the next line matching is_end, outside code fences."""
    start: int | None = None
    offset = 0
    in_fence = False
    for line in text.splitlines(keepends=True):
        bare = line.rstrip("\r\n")
        if bare.startswith("```"):
            in_fence = not in_fence
        elif not in_fence and start is None and is_start(bare):
            start = offset
        elif not in_fence and start is not None and is_end(bare):
            return start, offset
        offset += len(line)
    return None if start is None else (start, len(text))


def find_section(body: str, heading: str) -> tuple[int, int] | None:
    """Return the span of the `## ` section titled heading, up to the next `## ` heading or the end."""
    return _find_block(body, lambda line: line.rstrip() == heading, lambda line: line.startswith("## "))


def set_section(body: str, heading: str, text: str, *, before: str | None = None) -> str:
    """Return body with the section titled heading replaced by text, else inserted before `before`, else appended."""
    span = find_section(body, heading)
    anchor = find_section(body, before) if span is None and before else None
    if anchor is not None:
        span = (anchor[0], anchor[0])
    if span is None:
        return body.rstrip("\n") + "\n\n" + text if body.strip() else text
    start, end = span
    tail = body[end:]
    return body[:start] + text + ("\n" + tail if tail else "")


def split_workplan(text: str) -> tuple[WorkPlan, str]:
    """Return the work plan parsed from the comment text's `dovo:workplan` JSON line and the text after it ("as landed" reports).

    Raises:
        WorkplanError: When text has no work plan or its JSON line is malformed.
    """
    span = find_section(text, WORKPLAN_HEADING)
    section = text[span[0] : span[1]] if span else ""
    match = WORKPLAN_COMMENT_RE.search(section)
    if match is None:
        raise WorkplanError("the issue has no work plan; run `workplan.py render` first")
    try:
        data = _object(json.loads(match.group(1)), "the dovo:workplan comment")
        slices = [_stored_slice(_object(row, "a dovo:workplan slice")) for row in _list(data["slices"])]
        plan = WorkPlan(str(data["plan_session"]), _optional_int(data.get("plan_revision"), "plan_revision"), slices)
    except (json.JSONDecodeError, KeyError, ValueError) as error:
        raise WorkplanError(f"the dovo:workplan comment is malformed: {error}") from error
    return plan, section[match.end() :].strip("\n")


def lf(text: str) -> str:
    """Return text with CRLF line endings, as a GitHub web edit writes them, normalized to LF."""
    return text.replace("\r\n", "\n")


def current_rows(text: str, plan_slices: list[Slice]) -> tuple[list[Slice], str]:
    """Return plan_slices merged with the comment text's work plan rows, and its "as landed" reports.

    Text without a `## Work plan` section gives plan_slices as they are.
    """
    if find_section(text, WORKPLAN_HEADING) is None:
        return plan_slices, ""
    current, landed = split_workplan(text)
    return merge_rows(plan_slices, current.slices), landed


def _stored_slice(data: dict[str, object]) -> Slice:
    """Return one slice row from the dovo:workplan JSON line."""
    session = data.get("session")
    return Slice(
        id=str(data["id"]),
        title=str(data["title"]),
        depends_on=[str(item) for item in _list(data.get("depends_on"))],
        blocked_by=[int(str(item)) for item in _list(data.get("blocked_by"))],
        closing_docs=data.get("closing_docs") is True,
        outcomes=[str(item) for item in _list(data.get("outcomes"))],
        status=str(data["status"]),
        pr=_optional_int(data.get("pr"), "pr"),
        session=None if session is None else str(session),
        size=_size(data.get("size"), str(data["id"])),
        child=_optional_int(data.get("child"), "child"),
    )


def _cell(text: str) -> str:
    """Return text made safe for one markdown table cell."""
    return " ".join(text.split()).replace("|", "\\|") or NONE_CELL


def _row(cells: list[str]) -> str:
    """Return one markdown table row."""
    return "| " + " | ".join(_cell(cell) for cell in cells) + " |"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    """Return a markdown table, or `None.` when there are no rows."""
    if not rows:
        return "None."
    return "\n".join([_row(headers), "|" + "---|" * len(headers), *(_row(row) for row in rows)])


def _refs(numbers: list[int]) -> list[str]:
    """Return issue numbers as `#n` references."""
    return [f"#{number}" for number in numbers]


def _comment_json(plan: WorkPlan) -> str:
    """Return the work plan as one-line JSON that cannot close the HTML comment holding it."""
    text = json.dumps(asdict(plan), ensure_ascii=False, separators=(",", ":"))
    return text.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


def render_workplan(plan: WorkPlan, landed: str) -> str:
    """Return the `## Work plan` section: the plan session line, the slice table, the JSON line and landed reports.

    A slice split out to its own issue names that issue after its id.

    Args:
        plan: The work plan to render.
        landed: The "as landed" reports to keep under the JSON line, or "".

    Returns:
        The section text, ending with a newline.
    """
    revision = "" if plan.plan_revision is None else f" (plan revision {plan.plan_revision})"
    rows = [
        [
            row.id if row.child is None else f"{row.id} (#{row.child})",
            row.title,
            ", ".join(row.outcomes),
            ", ".join(row.depends_on + _refs(row.blocked_by)),
            row.status,
            f"#{row.pr}" if row.pr is not None else "",
            row.session or "",
        ]
        for row in plan.slices
    ]
    table = _table(["Slice", "Scope", "Outcomes", "Depends on", "Status", "PR", "Code session"], rows)
    parts = [WORKPLAN_HEADING, f"Plan Session ID: `{plan.plan_session}`{revision}", "", table, ""]
    parts.append(f"<!-- dovo:workplan {_comment_json(plan)} -->")
    text = "\n".join(parts) + "\n"
    return text + ("\n" + landed.strip("\n") + "\n" if landed.strip() else "")


def with_contract(text: str, contract: dict[str, object], questions: str) -> str:
    """Return the comment text with its `## Feature contract` rendered from contract and questions, before the work plan."""
    return set_section(text, CONTRACT_HEADING, render_contract(contract, questions), before=WORKPLAN_HEADING)


def with_workplan(text: str, plan: WorkPlan, landed: str) -> str:
    """Return the comment text with its `## Work plan` section replaced by plan and landed."""
    return set_section(text, WORKPLAN_HEADING, render_workplan(plan, landed))


def add_landed(landed: str, slice_id: str, pr: int, report: str) -> str:
    """Return landed with slice_id's "as landed" report added, replacing an earlier one for that slice.

    Headings of level 1-3 in report are demoted to level 4, so the report stays inside the work plan.
    """
    demoted = re.sub(r"^#{1,3}(?=\s)", "####", report.strip("\n"), flags=re.MULTILINE)
    block = f"### {slice_id} as landed (#{pr})\n{demoted}\n"
    prefix = f"### {slice_id} as landed ("
    span = _find_block(landed, lambda line: line.startswith(prefix), lambda line: line.startswith("### "))
    if span is None:
        return f"{landed.rstrip()}\n\n{block}" if landed.strip() else block
    start, end = span
    tail = landed[end:]
    return landed[:start] + block + ("\n" + tail if tail else "")


# --- feature contract ---------------------------------------------------------


def _items(contract: dict[str, object], key: str) -> list[dict[str, object]]:
    """Return contract[key] as a list of objects."""
    return [_object(item, f"each contract {key} entry") for item in _list(contract.get(key))]


def _text(item: dict[str, object], key: str) -> str:
    """Return item[key] as text, or "" when it is missing or null."""
    value = item.get(key)
    return "" if value is None else str(value)


def _joined(item: dict[str, object], key: str, *, refs: bool = False) -> str:
    """Return item[key]'s entries joined by commas, as `#n` references when refs is set."""
    values = [str(value) for value in _list(item.get(key))]
    return ", ".join(f"#{value}" for value in values) if refs else ", ".join(values)


def _bullets(lines: list[str]) -> str:
    """Return lines as the body of a contract section, or `None.` when there are none."""
    return "\n".join(lines) if lines else "None."


def _existing_issues(contract: dict[str, object]) -> str:
    """Render `### Existing issues` as its Issue / Role / Becomes table."""
    rows = [
        [f"#{_text(item, 'issue')}", _text(item, "role"), _text(item, "becomes") or _joined(item, "slices")]
        for item in _items(contract, "existing_issues")
    ]
    return _table(["Issue", "Role", "Becomes"], rows)


def _prerequisites(contract: dict[str, object]) -> str:
    """Render `### Prerequisites` as one bullet per prerequisite issue."""
    return _bullets(
        [
            f"- #{_text(item, 'issue')}: needed by {_joined(item, 'for')}, because {_text(item, 'makes_true')}"
            for item in _items(contract, "prerequisites")
        ]
    )


def _non_goal(item: dict[str, object]) -> str:
    """Render one non-goal bullet with its kind and its owners or reason."""
    kind = _text(item, "kind")
    owners = _joined(item, "owners", refs=True)
    detail = f", because {_text(item, 'reason')}" if kind == "excluded" else f", owned by {owners or 'nobody'}"
    return f"- **{kind.capitalize()}:** {_text(item, 'text')}{detail}."


def _non_goals(contract: dict[str, object]) -> str:
    """Render `### Non-goals`."""
    return _bullets([_non_goal(item) for item in _items(contract, "non_goals")])


def _entry(item: dict[str, object]) -> str:
    """Return ` (`<entry>`)` for an item that cites an architecture entry, else ""."""
    entry = _text(item, "entry")
    return f" (`{entry}`)" if entry else ""


def _target(contract: dict[str, object]) -> str:
    """Render `### Target contract`: each surface's end state, baseline and the slices touching it."""
    lines: list[str] = []
    for item in _items(contract, "target"):
        baseline = item.get("baseline")
        base = _object(baseline, "a target baseline") if baseline is not None else {}
        lines.append(f"- **{_text(item, 'surface')}**{_entry(item)}: {_text(item, 'end_state')}")
        lines.append(f"  - Baseline ({_text(base, 'as_of')}): {_text(base, 'text')}")
        lines.append(f"  - Touched by: {_joined(item, 'touched_by')}.")
    return _bullets(lines)


def _exemplar(item: dict[str, object]) -> str:
    """Render one exemplar bullet, saying whether it conforms and which slice fixes it."""
    fixed_by = _text(item, "fixed_by")
    conforms = "conforms" if item.get("conforms") is True else f"does not yet conform (fixed by {fixed_by})"
    return f"- **Exemplar:** `{_text(item, 'path')}`, {conforms}. {_text(item, 'why')}"


def _conventions(contract: dict[str, object]) -> str:
    """Render `### Conventions`: each rule with its entry or the slice that writes it, then the exemplars."""
    lines = []
    for item in _items(contract, "conventions"):
        new_in = _text(item, "new_in")
        lines.append(f"- {_text(item, 'rule')}{f' (new; written by {new_in})' if new_in else _entry(item)}")
    lines.extend(_exemplar(item) for item in _items(contract, "exemplars"))
    return _bullets(lines)


def _fenced(text: str, indent: str) -> str:
    """Return text in a `text` fence one backtick longer than its longest backtick run, each line indented."""
    fence = "`" * max(3, 1 + max((len(run) for run in re.findall(r"`+", text)), default=0))
    return "\n".join(f"{indent}{line}" if line else line for line in [f"{fence}text", *text.split("\n"), fence])


def _override(item: dict[str, object]) -> str:
    """Render one override bullet, with a multi-line replacement fenced under it so its headings stay in the contract."""
    quoted = f'- #{_text(item, "issue")}, "{_text(item, "quote")}"'
    replacement = _text(item, "replacement").strip("\n")
    if "\n" not in replacement:
        return f"{quoted} → {replacement}, per {_text(item, 'per')}."
    return f"{quoted}, per {_text(item, 'per')}, becomes:\n{_fenced(replacement, '  ')}"


def _overrides(contract: dict[str, object]) -> str:
    """Render `### Overrides`, quoting each sentence a contract decision makes wrong."""
    return _bullets([_override(item) for item in _items(contract, "overrides")])


def _settled_by(item: dict[str, object]) -> str:
    """Return a decision's `Settled by` cell: its kind, reference and confirmation date."""
    raw = item.get("settled_by")
    settled = _object(raw, "a decision's settled_by") if raw is not None else {}
    confirmed = _text(settled, "confirmed")
    parts = [_text(settled, "kind"), _text(settled, "ref"), f"(confirmed {confirmed})" if confirmed else ""]
    return " ".join(part for part in parts if part)


def _open_question(item: dict[str, object]) -> str:
    """Render one open question with the slices it blocks and its recommended answer."""
    blocks = _joined(item, "blocks")
    blocked = f" (blocks {blocks})" if blocks else ""
    return f"- **{_text(item, 'id')}**{blocked}: {_text(item, 'summary')} **Recommended:** {_text(item, 'assumed')}"


def render_questions(open_questions: list[dict[str, object]]) -> str:
    """Return the open questions block listed under `### Decisions`, or "" when there are none."""
    if not open_questions:
        return ""
    return "\n".join([QUESTIONS_INTRO, *(_open_question(item) for item in open_questions)])


def body_questions(text: str) -> str:
    """Return the open questions block under the comment text's `## Feature contract` as `render_questions` wrote it, or ""."""
    span = find_section(text, CONTRACT_HEADING)
    section = lf(text[span[0] : span[1]]) if span else ""
    block = _find_block(section, lambda line: line == QUESTIONS_INTRO, lambda line: not line.strip())
    return section[block[0] : block[1]].rstrip("\n") if block else ""


def _decisions(contract: dict[str, object]) -> str:
    """Render `### Decisions` as its table, followed by the open questions block, leaving out whichever is empty."""
    rows = [
        [_text(item, "id"), _text(item, "decision"), _text(item, "why"), _settled_by(item)]
        for item in _items(contract, "decisions")
    ]
    parts = [_table(["ID", "Decision", "Why", "Settled by"], rows)] if rows else []
    questions = _text(contract, OPEN_QUESTIONS)
    if questions:
        parts.append(questions)
    return "\n\n".join(parts)


def _inventory(contract: dict[str, object]) -> str:
    """Render `### Code inventory`: one line per kind of site, then every site in a collapsed block."""
    raw = contract.get("inventory")
    inventory = _object(raw, "the contract inventory") if raw is not None else {}
    items = _items(inventory, "items")
    lines = [
        f"- {_text(item, 'kind')}: {_text(item, 'count')}, {_text(item, 'slice')} (`{_text(item, 'command')}`)"
        for item in items
    ]
    sites = [f"- `{site}`" for item in items for site in _list(item.get("sites"))]
    if sites:
        lines += ["", "<details><summary>Sites</summary>", "", *sites, "", "</details>"]
    sha = _text(inventory, "sha")
    return (f"At `{sha}`.\n" if sha else "") + _bullets(lines)


def _ownership(contract: dict[str, object]) -> str:
    """Render `### Shared-surface ownership` as its table."""
    rows = [
        [_text(item, "surface"), _text(item, "aspect"), _text(item, "owner"), _joined(item, "extended_by")]
        for item in _items(contract, "ownership")
    ]
    return _table(["Surface", "Aspect", "Owner", "Extended by"], rows)


def _lint_gates(contract: dict[str, object]) -> str:
    """Render `### Lint gates` as its table."""
    rows = [
        [
            f"`{_text(item, 'check')}`" + (" (extended)" if item.get("extends") is True else " (new)"),
            _text(item, "enforces"),
            _text(item, "slice"),
            f"`{_text(item, 'entry')}`" if _text(item, "entry") else "",
        ]
        for item in _items(contract, "lint_gates")
    ]
    return _table(["Check", "Enforces", "Lands with", "Cites"], rows)


def _checkpoint(item: dict[str, object]) -> str:
    """Render one checkpoint bullet: after the pilot, after shared-surface slices, or the baseline before a slice."""
    kind = _text(item, "kind")
    after = _joined(item, "after")
    if kind == "pilot":
        return f"- After the pilot ({after})."
    if kind == "baseline":
        return f"- Baseline: before {_text(item, 'before')}, across every landed slice and any open slice PR."
    return f"- After {after}: {kind}."


def _checkpoints(contract: dict[str, object]) -> str:
    """Render `### Checkpoints`."""
    return _bullets([_checkpoint(item) for item in _items(contract, "checkpoints")])


OPEN_QUESTIONS = "open_questions"
# Each section in template order, the contract keys it renders, and its renderer.
CONTRACT_SECTIONS: tuple[tuple[str, tuple[str, ...], Callable[[dict[str, object]], str]], ...] = (
    ("Problem", ("problem",), lambda contract: _text(contract, "problem")),
    ("Existing issues", ("existing_issues",), _existing_issues),
    ("Prerequisites", ("prerequisites",), _prerequisites),
    ("Non-goals", ("non_goals",), _non_goals),
    ("Target contract", ("target",), _target),
    ("Conventions", ("conventions", "exemplars"), _conventions),
    ("Overrides", ("overrides",), _overrides),
    ("Decisions", ("decisions", OPEN_QUESTIONS), _decisions),
    ("Code inventory", ("inventory",), _inventory),
    ("Shared-surface ownership", ("ownership",), _ownership),
    ("Lint gates", ("lint_gates",), _lint_gates),
    ("Checkpoints", ("checkpoints",), _checkpoints),
)


def render_contract(contract: dict[str, object], questions: str) -> str:
    """Return the `## Feature contract` section rendered from plan.json's contract and an open questions block.

    A section whose keys are all missing, null or empty is left out, heading
    and all, so a short contract renders only the sections it has.

    Args:
        contract: plan.json's `contract` object.
        questions: The open questions block listed under Decisions: `render_questions` of the epic
            plan's `open_questions`, or the block already in the comment, or "".

    Returns:
        The section text, ending with a newline.

    Raises:
        WorkplanError: When a contract field puts a `## ` heading outside a code fence, which would end the
            section early, so the comment could never be found to render as this contract again.
    """
    fields: dict[str, object] = {**contract, OPEN_QUESTIONS: questions}
    grounded_at = _text(contract, "grounded_at")
    parts = [CONTRACT_HEADING, *([f"Grounded at `main@{grounded_at}`."] if grounded_at else [])]
    for title, keys, render in CONTRACT_SECTIONS:
        if any(fields.get(key) for key in keys):
            parts += ["", f"### {title}", render(fields)]
    text = "\n".join(parts) + "\n"
    if find_section(text, CONTRACT_HEADING) != (0, len(text)):
        raise WorkplanError("the contract renders a `## ` heading outside a code fence, which would end its section")
    return text


# --- slice selection ----------------------------------------------------------


def remaining(plan: WorkPlan, slice_id: str) -> int:
    """Return how many slices other than slice_id are not merged yet."""
    return sum(1 for row in plan.slices if row.id != slice_id and row.status != "merged")


def closing_lines(issue: int, plan: WorkPlan, row: Slice) -> str:
    """Return the line a slice's pull request ends with: `Closes #N` when no other slice remains, else `Part of #N`.

    `Part of` names the slice and how many slices the work plan has, as in `Part of #925 (slice S2 of 4)`.
    """
    if remaining(plan, row.id) == 0:
        return f"Closes #{issue}"
    return f"Part of #{issue} (slice {row.id} of {len(plan.slices)})"


def refresh_merged(plan: WorkPlan, pr_state: Callable[[int], str]) -> WorkPlan:
    """Return plan with every active slice whose pull request GitHub reports merged marked `merged`.

    Raises:
        WorkplanError: When an active slice's pull request was closed without merging.
    """
    slices: list[Slice] = []
    for row in plan.slices:
        state = pr_state(row.pr) if row.pr is not None and row.status in ACTIVE_STATUSES else ""
        if state == "CLOSED":
            raise WorkplanError(f"{row.id}'s pull request #{row.pr} was closed without merging")
        slices.append(replace(row, status="merged") if state == "MERGED" else row)
    return replace(plan, slices=slices)


def _active_problem(plan: WorkPlan) -> str | None:
    """Return why no new slice may start while another is active, or None."""
    active = _active_slice(plan)
    return None if active is None else _active_message(active)


def _active_slice(plan: WorkPlan) -> Slice | None:
    """Return the first slice in progress or in review, or None."""
    return next((row for row in plan.slices if row.status in ACTIVE_STATUSES), None)


def _active_message(row: Slice) -> str:
    """Return why no new slice may start while row is active."""
    pr = f" (#{row.pr})" if row.pr is not None else ""
    return f"{row.id} is {row.status}{pr}; finish it before starting another slice"


def _active_refusal(plan: WorkPlan) -> WorkplanError | None:
    """Return the refusal for an active slice: a wait while its pull request is in review, else an error; or None."""
    active = _active_slice(plan)
    if active is None:
        return None
    problem = _active_message(active)
    return WorkplanWait(problem, {active.id: problem}) if active.status == "in_review" else WorkplanError(problem)


def _wait_reason(row: Slice, merged: set[str], is_closed: Callable[[int], bool]) -> str | None:
    """Return why a pending slice cannot start yet, or None when it is ready."""
    waiting = [dep for dep in row.depends_on if dep not in merged]
    if waiting:
        return f"{row.id} waits on {', '.join(waiting)}"
    still_open = [number for number in row.blocked_by if not is_closed(number)]
    if still_open:
        return f"{row.id} is blocked by open {', '.join(_refs(still_open))}"
    return None


def pick(plan: WorkPlan, is_closed: Callable[[int], bool]) -> Slice:
    """Return the first pending slice whose depends_on slices are merged and whose blocked_by issues are closed.

    Raises:
        WorkplanWait: When a slice's pull request is in review, or no pending slice is ready, saying why;
            a person settles either by merging or closing what the slice waits on.
        WorkplanError: When a slice is in progress, or every slice is merged.
    """
    refusal = _active_refusal(plan)
    if refusal is not None:
        raise refusal
    merged = {row.id for row in plan.slices if row.status == "merged"}
    reasons: dict[str, str] = {}
    for row in plan.slices:
        if row.status != "pending":
            continue
        reason = _wait_reason(row, merged, is_closed)
        if reason is None:
            return row
        reasons[row.id] = reason
    if not reasons:
        raise WorkplanError("every slice is merged")
    raise WorkplanWait(f"no slice is ready: {'; '.join(reasons.values())}", reasons)


def find_slice(plan: WorkPlan, slice_id: str) -> Slice:
    """Return the slice row with slice_id, or refuse."""
    for row in plan.slices:
        if row.id == slice_id:
            return row
    raise WorkplanError(f"the work plan has no slice {slice_id}")


def output_lines(outputs: dict[str, str]) -> str:
    """Return outputs as `$DOVO_OUTPUT` lines: `key=value`, or a heredoc block for a multi-line value."""
    lines: list[str] = []
    for key, value in outputs.items():
        if "\n" in value:
            lines += [f"{key}<<{HEREDOC_DELIMITER}", value, HEREDOC_DELIMITER]
        else:
            lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def sweep_problem(entry: dict[str, object], body: str) -> str | None:
    """Return why a sweep entry must be skipped because the live body is not the one it was drafted against."""
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if digest != entry.get("expected_body_sha256"):
        return "its body changed since the sweep was drafted"
    return None


# --- GitHub -------------------------------------------------------------------


def view_issue(issue: int, fields: str) -> dict[str, object]:
    """Return the requested JSON fields of issue."""
    return _object(json.loads(run("gh", "issue", "view", str(issue), "--json", fields)), "gh issue view")


def read_body(issue: int) -> str:
    """Return issue's body exactly as GitHub stores it."""
    return str(view_issue(issue, "body")["body"])


def issue_closed(issue: int) -> bool:
    """Return whether issue is closed."""
    return view_issue(issue, "state")["state"] == "CLOSED"


def pr_state(pr: int) -> str:
    """Return the pull request's state: OPEN, CLOSED or MERGED."""
    return str(_object(json.loads(run("gh", "pr", "view", str(pr), "--json", "state")), "gh pr view")["state"])


def _with_file(text: str, command: Callable[[str], str]) -> str:
    """Write text to a temporary file, run command with its path and return the command's output."""
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as handle:
        handle.write(text)
    path = Path(handle.name)
    try:
        return command(str(path))
    finally:
        path.unlink(missing_ok=True)


def write_body(issue: int, read: str, new: str) -> str:
    """Write new as issue's body unless it changed since this run read it; return new.

    Raises:
        WorkplanError: When the live body is no longer read.
    """
    if read_body(issue) != read:
        raise WorkplanError(f"#{issue}'s body changed since this run read it; run the command again")
    if new != read:
        _with_file(new, lambda path: run("gh", "issue", "edit", str(issue), "--body-file", path))
    return new


def _is_workplan(body: str) -> bool:
    """Return whether a comment's first line is exactly the work plan marker, which no person's comment carries."""
    return lf(body).split("\n", 1)[0] == WORKPLAN_MARKER


def _comment_id(url: str) -> int:
    """Return the comment id at the end of a comment's URL, such as `...#issuecomment-6513400137`."""
    match = COMMENT_ID_RE.search(url)
    if match is None:
        raise WorkplanError(f"cannot read the work plan comment's id from its URL {url!r}")
    return int(match.group(1))


def read_workplan(issue: int) -> WorkplanComment:
    """Return issue's work plan comment, or one with no id holding only the marker when the issue has none.

    Raises:
        WorkplanError: When the issue has more than one work plan comment.
    """
    comments = [_object(raw, "a comment") for raw in _list(view_issue(issue, "comments").get("comments"))]
    found = [comment for comment in comments if _is_workplan(str(comment.get("body") or ""))]
    if len(found) > 1:
        raise WorkplanError(f"#{issue} has {len(found)} work plan comments; delete all but one")
    if not found:
        return WorkplanComment(None, f"{WORKPLAN_MARKER}\n")
    return WorkplanComment(_comment_id(str(found[0].get("url") or "")), str(found[0]["body"]))


def write_workplan(issue: int, comment_id: int | None, text: str) -> None:
    """Edit the work plan comment comment_id to text, or post text as issue's work plan comment when it is None."""
    if comment_id is None:
        _with_file(text, lambda path: run("gh", "issue", "comment", str(issue), "--body-file", path))
        return
    endpoint = f"repos/{{owner}}/{{repo}}/issues/comments/{comment_id}"
    _with_file(text, lambda path: run("gh", "api", "--method", "PATCH", endpoint, "-F", f"body=@{path}"))


def update_workplan(issue: int, change: Callable[[str], str], refusal: str) -> str:
    """Apply change to issue's work plan comment and write the result, unless the comment changed meanwhile.

    The comment is re-read before writing. When it changed since it was read,
    change is applied once more to the new text; when it changed again, the
    update refuses with refusal. Returns the comment's text after the update.

    Raises:
        WorkplanError: When change refuses, the result is longer than GitHub allows a comment, or the
            comment changed twice.
    """
    for _ in range(2):
        comment = read_workplan(issue)
        new = change(comment.text)
        if new == comment.text:
            return new
        if len(new) > COMMENT_LIMIT:
            raise WorkplanError(
                f"the work plan comment would be {len(new):,} characters, over GitHub's {COMMENT_LIMIT:,}; "
                'shorten its "as landed" reports by hand and run the command again'
            )
        if read_workplan(issue) == comment:
            write_workplan(issue, comment.id, new)
            return new
    raise WorkplanError(refusal)


def _no_size_note(row: Slice, child: int) -> None:
    """Say on stderr that child gets no size label because row's work plan predates slice sizes."""
    sys.stderr.write(
        f"workplan.py: {row.id} has no size in the work plan, so #{child} gets no {SIZE_LABEL_PREFIX}* label; "
        "label it by hand\n"
    )


def child_body(issue: int, plan: WorkPlan, row: Slice) -> str:
    """Return the description of row's own issue: the slice it tracks and the outcomes it delivers."""
    outcomes = ", ".join(row.outcomes) or "none listed"
    return (
        f"Slice {row.id} of #{issue} ({len(plan.slices)} slices), split out to its own issue.\n\n"
        f"Delivers {outcomes} of #{issue}. The work plan comment on #{issue} tracks it.\n"
    )


def create_child(row: Slice, body: str) -> int:
    """Create row's own issue with body, labelled with the slice's size, and return its number.

    Raises:
        WorkplanError: When the issue was created but its number cannot be read from gh's output.
    """
    label = () if row.size is None else ("--label", f"{SIZE_LABEL_PREFIX}{row.size}")
    output = _with_file(
        body, lambda path: run("gh", "issue", "create", "--title", row.title, "--body-file", path, *label)
    )
    try:
        return int(output.splitlines()[-1].rstrip("/").rsplit("/", 1)[-1])
    except (ValueError, IndexError) as error:
        raise WorkplanError(
            f"created a child issue for {row.id} but cannot read its number from gh's output {output!r}; "
            f"record it as {row.id}'s child by hand"
        ) from error


def _with_child(text: str, slice_id: str, child: int) -> str:
    """Return the comment text with child recorded as slice_id's own issue."""
    plan, landed = split_workplan(text)
    find_slice(plan, slice_id).child = child
    return with_workplan(text, plan, landed)


def record_child(issue: int, slice_id: str, child: int) -> None:
    """Record child as slice_id's own issue in issue's work plan comment.

    Raises:
        WorkplanError: When the comment changed twice, naming the created child.
    """
    refusal = f"created #{child} but #{issue}'s work plan comment kept changing; record #{child} as {slice_id}'s child"
    update_workplan(issue, lambda text: _with_child(text, slice_id, child), refusal)


def link_child(epic: int, child: int) -> None:
    """Make child a native sub-issue of epic, unless it already is one."""
    repository = run("gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner")
    endpoint = f"repos/{repository}/issues/{epic}/sub_issues"
    if str(child) in run("gh", "api", "--paginate", endpoint, "--jq", ".[].number").split():
        return
    child_id = run("gh", "api", f"repos/{repository}/issues/{child}", "--jq", ".id")
    run("gh", "api", "--method", "POST", endpoint, "-F", f"sub_issue_id={child_id}")


def _after_creating(child: int, step: str, hint: str, action: Callable[[], object]) -> None:
    """Run action, turning a failed gh call into a refusal that names the created child and how to resume."""
    try:
        action()
    except subprocess.CalledProcessError as error:
        command: list[str] = error.cmd
        stderr = str(error.stderr or "").strip()
        raise WorkplanError(f"{step} #{child} failed ({' '.join(command)}: {stderr}); {hint}") from error


def apply_sweep_entry(entry: dict[str, object], base: Path) -> None:
    """Write one sweep entry's body and comment to its issue and remove `dovo:ready`, unless its body changed.

    Raises:
        WorkplanError: When the live body's sha256 is not the entry's expected one.
    """
    issue = int(str(entry["issue"]))
    view = view_issue(issue, "body,labels")
    body = str(view["body"])
    problem = sweep_problem(entry, body)
    if problem is not None:
        raise WorkplanError(problem)
    write_body(issue, body, (base / str(entry["body_file"])).read_text(encoding="utf-8"))
    comment = (base / str(entry["comment_file"])).read_text(encoding="utf-8")
    _with_file(comment, lambda path: run("gh", "issue", "comment", str(issue), "--body-file", path))
    labels = [_object(label, "a label").get("name") for label in _list(view.get("labels"))]
    if READY_LABEL in labels:
        run("gh", "issue", "edit", str(issue), "--remove-label", READY_LABEL)


def apply_sweep(path: Path) -> int:
    """Apply every entry of sweep.json and return how many failed; `[]` is an empty sweep.

    An entry whose issue body changed since the sweep was drafted is skipped and
    reported on stderr without counting as a failure. An entry that fails for any
    other reason is reported and counted. Either way the rest are still applied.
    File paths in an entry are relative to the directory holding sweep.json.
    """
    failures = 0
    for raw in _list(_load_json(str(path))):
        entry = _object(raw, "each sweep.json entry")
        try:
            apply_sweep_entry(entry, path.parent)
        except WorkplanError as error:
            sys.stderr.write(f"workplan.py: sweep skipped #{entry.get('issue')}: {error}\n")
        except (subprocess.CalledProcessError, OSError, KeyError, ValueError) as error:
            failures += 1
            sys.stderr.write(f"workplan.py: sweep failed for #{entry.get('issue')}: {error}\n")
    return failures


# --- subcommands --------------------------------------------------------------


def _load_json(path: str) -> object:
    """Return the parsed JSON file at path, or refuse when it is missing or malformed."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkplanError(f"cannot read {path}: {error}") from error


def _rendered(text: str, plan: WorkPlan, contract: dict[str, object] | None, questions: str) -> str:
    """Return the comment text with plan, its slices merged with the rows already there, and contract when given."""
    rows, landed = current_rows(text, plan.slices)
    new = with_workplan(text, replace(plan, slices=rows), landed)
    return new if contract is None else with_contract(new, contract, questions)


def cmd_render(issue: int, plan_path: str, session: str, revision: int) -> None:
    """Write the feature contract and work plan from plan.json into issue's work plan comment, creating it when absent."""
    data = _load_json(plan_path)
    contract, slices = parse_plan(data)
    raw_questions = _list(_object(data, "plan.json").get("open_questions"))
    questions = render_questions([_object(item, "each open question") for item in raw_questions])
    plan = WorkPlan(session, revision, slices)
    refusal = f"#{issue}'s work plan comment kept changing while rendering it; run render again"
    update_workplan(issue, lambda text: _rendered(text, plan, contract, questions), refusal)


def _refreshed(text: str) -> str:
    """Return the comment text with every active slice whose pull request merged marked merged."""
    plan, landed = split_workplan(text)
    return with_workplan(text, refresh_merged(plan, pr_state), landed)


def cmd_next(issue: int) -> dict[str, str]:
    """Pick the next slice, after marking the merged ones, and return the step outputs; no issue is created."""
    refusal = f"#{issue}'s work plan comment kept changing while marking merged slices; run next again"
    plan, _ = split_workplan(update_workplan(issue, _refreshed, refusal))
    row = pick(plan, issue_closed)
    return {
        "slice": row.id,
        "plan_session": plan.plan_session,
        "plan_revision": "" if plan.plan_revision is None else str(plan.plan_revision),
        "remaining": str(remaining(plan, row.id)),
        "closing_lines": closing_lines(issue, plan, row),
    }


def write_holds(path: Path, holds: dict[str, str]) -> None:
    """Write holds to path as `{"holds": [{"id", "label"}]}`, the list a blueprint's `ask` step offers."""
    items = [{"id": slice_id, "label": reason} for slice_id, reason in holds.items()]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"holds": items}, indent=2) + "\n", encoding="utf-8")


def cmd_next_or_hold(issue: int, hold: Path | None) -> dict[str, str]:
    """Run `next`; with a hold path, write its holds and return no outputs when the slices wait on a person.

    Raises:
        WorkplanError: When `next` refuses for any other reason, or waits without a hold path.
    """
    try:
        outputs = cmd_next(issue)
    except WorkplanWait as wait:
        if hold is None:
            raise
        write_holds(hold, wait.holds)
        sys.stderr.write(f"workplan.py: holding: {wait}\n")
        return {}
    if hold is not None:
        write_holds(hold, {})
    return outputs


def _claimed(text: str, slice_id: str, session: str) -> str:
    """Return the comment text with the slice in_progress for session, refusing a slice that is not pending."""
    plan, landed = split_workplan(text)
    row = find_slice(plan, slice_id)
    if row.status == "in_progress" and row.session == session:
        return text
    if row.status != "pending":
        raise WorkplanError(f"{slice_id} is {row.status}, not pending")
    problem = _active_problem(plan)
    if problem is not None:
        raise WorkplanError(problem)
    row.status, row.session = "in_progress", session
    return with_workplan(text, plan, landed)


def cmd_claim(issue: int, slice_id: str, session: str) -> None:
    """Mark the slice in_progress with this code session."""
    refusal = f"#{issue}'s work plan comment kept changing while claiming {slice_id}; run claim again"
    update_workplan(issue, lambda text: _claimed(text, slice_id, session), refusal)


def settle_questions(questions: str, contract: dict[str, object]) -> str:
    """Return the open questions block without the questions contract's decisions now settle.

    A slice's report settles an epic open question by adding a `decisions`
    entry whose `id` is the question's id (such as `OQ-2`). The block is ""
    when no question is left.
    """
    decided = {_text(item, "id") for item in _items(contract, "decisions")}
    lines = questions.split("\n")
    kept = [line for line in lines[1:] if not any(line.startswith(f"- **{qid}**") for qid in decided)]
    return "\n".join([lines[0], *kept]) if kept else ""


def amend_contract(issue: int, text: str, base: dict[str, object] | None, contract: dict[str, object]) -> str:
    """Return the comment text with its `## Feature contract` rendered from contract, checked three ways against base.

    base is the contract this run started from. When the comment still renders
    as base, it takes contract, dropping the open questions contract settles;
    when it already renders as contract, it is returned unchanged.

    Raises:
        WorkplanError: When the comment renders as neither, because someone edited it during the run.
    """
    questions = body_questions(text)
    amended = with_contract(text, contract, settle_questions(questions, contract))
    if lf(amended) == lf(text):
        return text
    if base is not None and lf(with_contract(text, base, questions)) == lf(text):
        return amended
    raise WorkplanError(
        f"#{issue}'s Feature contract was edited during this run: it is neither the contract this run started "
        "from nor the amended one; reconcile it by hand and run land again"
    )


def amend(issue: int, text: str, plan: WorkPlan, args: argparse.Namespace) -> tuple[str, WorkPlan]:
    """Return the comment text and plan following `--plan`, repointed at this session when the report amended it.

    The plan is amended when its slice rows differ from the work plan's, or its
    contract differs from `--base-plan`'s, the epic plan this run started from
    (see `amend_contract`). The open questions the comment lists under
    Decisions are kept, since the code session's plan.json does not carry
    them, except those the amended contract settles.

    Raises:
        WorkplanError: When the plan redefines a slice that has already started (see `check_started_unchanged`).
    """
    contract, plan_slices = parse_plan(_load_json(args.plan))
    raw_base = _object(_load_json(args.base_plan), "the epic plan.json").get("contract")
    base = None if raw_base is None else _object(raw_base, "the epic plan.json contract")
    check_started_unchanged(plan_slices, plan.slices)
    rows = merge_rows(plan_slices, plan.slices)
    amended = text if contract is None else amend_contract(issue, text, base, contract)
    if rows == plan.slices and amended == text:
        return text, plan
    return amended, WorkPlan(args.session, None, rows)


def _landed(issue: int, text: str, args: argparse.Namespace, report: str) -> str:
    """Return the comment text with the slice in_review with its PR, its report added and the plan amended."""
    slice_id: str = args.slice
    session: str = args.session
    pr: int = args.pr
    plan, landed = split_workplan(text)
    row = find_slice(plan, slice_id)
    landable = row.status == "in_progress" or (row.status == "in_review" and row.pr == pr)
    if not landable or row.session != session:
        raise WorkplanError(f"{slice_id} is {row.status} for session {row.session}, not in_progress for {session}")
    amended, plan = amend(issue, text, plan, args)
    row = find_slice(plan, slice_id)
    row.status, row.pr = "in_review", pr
    return with_workplan(amended, plan, add_landed(landed, slice_id, pr, report))


def cmd_land(issue: int, args: argparse.Namespace) -> int:
    """Mark the slice in_review with its PR, record its report, follow an amended plan or contract and apply the sweep.

    Writes `remaining`, the number of other slices not yet merged, to `$DOVO_OUTPUT`.
    """
    try:
        report = Path(args.report).read_text(encoding="utf-8")
    except OSError as error:
        raise WorkplanError(f"cannot read {args.report}: {error}") from error
    refusal = f"#{issue}'s work plan comment kept changing while landing {args.slice}; run land again"
    text = update_workplan(issue, lambda current: _landed(issue, current, args, report), refusal)
    write_outputs({"remaining": str(remaining(split_workplan(text)[0], args.slice))})
    if args.sweep is None:
        return 0
    failures = apply_sweep(Path(args.sweep))
    if failures:
        raise WorkplanError(f"{failures} sweep entries failed; see above")
    return 0


def cmd_split_out(issue: int, slice_id: str) -> dict[str, str]:
    """Give one slice its own issue, linked as a sub-issue of issue, and return it as the `child` output.

    The created issue is recorded on the slice's row before it is linked, so a
    run that fails after creating it resumes by linking that issue, never by
    creating another. It carries the slice's `dovo-size-*` label.
    """
    plan, _ = split_workplan(read_workplan(issue).text)
    row = find_slice(plan, slice_id)
    child = row.child
    if child is None:
        created = create_child(row, child_body(issue, plan, row))
        if row.size is None:
            _no_size_note(row, created)
        hint = f"record #{created} as {row.id}'s child"
        _after_creating(created, "recording", hint, lambda: record_child(issue, row.id, created))
        child = created
    linked = child
    _after_creating(linked, "linking", "run split-out again to link it", lambda: link_child(issue, linked))
    return {"child": str(linked)}


def write_outputs(outputs: dict[str, str]) -> None:
    """Append outputs to the `$DOVO_OUTPUT` file, or print them when it is not set."""
    text = output_lines(outputs)
    target = os.environ.get("DOVO_OUTPUT")
    if not target:
        sys.stdout.write(text)
        return
    with Path(target).open("a", encoding="utf-8") as handle:
        handle.write(text)


def build_parser() -> argparse.ArgumentParser:
    """Return the parser for the render, next, claim, land and split-out subcommands."""
    parser = argparse.ArgumentParser(description="Keep an issue's feature contract and work plan comment current.")
    commands = parser.add_subparsers(dest="command", required=True)
    render = commands.add_parser("render", help="Write the contract and work plan from plan.json.")
    nxt = commands.add_parser("next", help="Pick the next slice and write its outputs to $DOVO_OUTPUT.")
    claim = commands.add_parser("claim", help="Mark a slice in_progress with this code session.")
    land = commands.add_parser("land", help="Mark a slice in_review, record its report and apply the sweep.")
    split = commands.add_parser("split-out", help="Give one slice its own issue, linked as a sub-issue.")
    for sub in (render, nxt, claim, land, split):
        sub.add_argument("--issue", type=int, required=True, help="The issue whose work plan holds the slices.")
    render.add_argument("--plan", required=True, help="Path to plan.json.")
    render.add_argument("--revision", type=int, required=True, help="The plan artifact's revision.")
    nxt.add_argument(
        "--hold",
        type=Path,
        help="Write the slices waiting on a person here and exit 0 without outputs, instead of refusing.",
    )
    for sub in (claim, land, split):
        sub.add_argument("--slice", required=True, help="The slice id, such as S2.")
    for sub in (render, claim, land):
        sub.add_argument("--session", required=True, help="This dovo session's id.")
    land.add_argument("--pr", type=int, required=True, help="The slice's pull request number.")
    land.add_argument("--report", required=True, help="Path to slice-report.md.")
    land.add_argument("--plan", required=True, help="Path to plan.json, amended or not.")
    land.add_argument(
        "--base-plan", required=True, help="Path to the epic plan.json this run started from (.agentic/epic-plan.json)."
    )
    land.add_argument("--sweep", help="Path to sweep.json.")
    return parser


def dispatch(args: argparse.Namespace) -> int:
    """Run the parsed subcommand and return its exit code."""
    issue: int = args.issue
    if args.command == "render":
        cmd_render(issue, args.plan, args.session, args.revision)
    elif args.command == "next":
        outputs = cmd_next_or_hold(issue, args.hold)
        if outputs:
            write_outputs(outputs)
    elif args.command == "claim":
        cmd_claim(issue, args.slice, args.session)
    elif args.command == "split-out":
        write_outputs(cmd_split_out(issue, args.slice))
    else:
        return cmd_land(issue, args)
    return 0


def main() -> int:
    """Run one workplan.py subcommand against the issue's work plan comment."""
    args = build_parser().parse_args()
    try:
        return dispatch(args)
    except WorkplanError as error:
        sys.stderr.write(f"workplan.py: {error}\n")
        return 1
    except subprocess.CalledProcessError as error:
        command: list[str] = error.cmd
        stderr: str = error.stderr
        sys.stderr.write(f"workplan.py: command failed: {' '.join(command)}\n{stderr}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
