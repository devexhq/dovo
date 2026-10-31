#!/usr/bin/env python3
"""Keeps an issue's work plan current in the one comment the slice flow posts on the issue.

The work plan comment is the one comment whose first line is exactly
`<!-- dovo:workplan schema_version=1 -->`. The slice flow's plan run writes its
`## Work plan` section with `render`, creating the comment when the issue has
none, and every later run edits it in place. A comment whose marker carries
anything more, such as `maintained=by-hand`, is a person's and is never read or
written. The issue's description is never written, and no other comment is
posted on the issue: the plan, its decisions and its feature contract stay in
the planning and coding sessions' artifacts, which the work plan names. A
section other than `## Work plan` left in the comment by an earlier version is
kept as it is and never read. The work plan is a table listing each slice with
its status, its own issue, its pull request and its code session, plus a
`<!-- dovo:workplan {json} -->` line that this script alone parses and
rewrites; it holds no per-slice prose, so it stays short however many slices
land.

Each code run picks its slice with `next`, then `start`s it: on a plan of two or
more slices, `start` gives the slice its own issue, a sub-issue of the issue in
the short issue format built from the issue's description and plan.json, when
it has none, records it as the slice's `child` and links it. A one-slice plan's
slice is worked on the issue itself. The run takes the slice with `claim` once
its own plan is approved, and records its pull request with `land`, which
repoints the work plan at the code session when the slice amended later slices
or the contract. `split-out` gives a slice its own issue the same way when a
person asks; `split-out --handed-off` also marks the slice `handed_off`, for
work handed to someone outside the flow: `next` never picks it, and it counts
as done once its own issue is closed. A slice's own issue, an existing
sub-issue the plan maps to it (plan.json `slices[].child`) or one `start` or
`split-out` created, is its row's `child`.

Every write re-reads the comment first; when it changed since this run read
it, the change is applied once more to the new text, and a second change
refuses. A write that would make the comment longer than GitHub's 65,536
characters refuses rather than truncating. `render` and `next` refuse an issue
whose description holds v1's work plan.

`next`, `start`, `land`, `show`, `phased` and `ready-state` write their outputs
as `key=value` lines to `$DOVO_OUTPUT`. `next` writes `slice`, `plan_session`,
`plan_revision`, `body_sha` (the body_sha of the description the plan was
written against, or ""), `phased_session` and `landed`, the `<slice>=<session>`
pairs of the slices in review or merged, whose sessions hold their "as landed"
reports. `start` and `land` write `issue`, the issue the slice is worked on
(`start` only), `remaining`, the number of other slices not yet done, and
`closing_lines`, the lines the slice's pull request ends with; `land` computes
them from the plan as landed. `show` writes the work plan's `plan_session` and
`body_sha`, both "" when the issue has no work plan. `render --phased-body-sha`,
run when the planning run phased the plan's one slice, records that session on
its row with the body_sha of the description it was phased against; a pending
row rendered again without it drops both. `phased` writes `phased_session`
again, empty unless that phased plan still holds: the slice has no issue of its
own, no landed slice has amended the plan since, and the description's body_sha
is unchanged. `render --ready-body-sha` and `ready` record the body_sha of the
description the ready check that labelled the slice's issue `dovo:ready` read,
and `ready-state` writes `ready=true` only while that issue is labelled and its
description is still that one. `land`'s `--sweep` corrects other issues'
descriptions, and skips an entry for the issue or the slice's own issue, naming
it on stderr. `pr-links` replaces the closing lines in a pull request's
description that name the issues the given lines name, when they differ from
the ones given, so a slice whose plan changed while it was in review still
closes the right issues. It reads every GitHub closing keyword, never a line
inside a fenced code block, and leaves a closing line naming another issue
alone. With `--hold`, `next` also writes the holds file a blueprint's `ask`
step lists: `{"holds": []}` once a slice is picked, or one hold per slice that
waits on a person (an open blocker, an unmerged slice, a pull request in
review), in which case it exits 0 without outputs instead of refusing.
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

WORKPLAN_HEADING = "## Work plan"
WORKPLAN_MARKER = "<!-- dovo:workplan schema_version=1 -->"
WORKPLAN_COMMENT_RE = re.compile(r"<!-- dovo:workplan (\{.*\}) -->")
COMMENT_ID_RE = re.compile(r"#issuecomment-(\d+)$")
COMMENT_LIMIT = 65_536
READY_LABEL = "dovo:ready"
ACTIVE_STATUSES = ("in_progress", "in_review")
LANDED_STATUSES = ("in_review", "merged")
HANDED_OFF = "handed_off"
V1_WORKPLAN = "<!-- dovo:workplan {"
HEREDOC_DELIMITER = "WORKPLAN_EOF"
NONE_CELL = "—"
SIZES = ("s", "m", "l")
WRITTEN_LINE_RE = re.compile(r"Closes #\d+|Part of #\d+ \(slice \S+ of \d+\)")
CLOSING_LINE_RE = re.compile(
    r"(?i:close[sd]?|fix(?:e[sd])?|resolve[sd]?):?[ \t]+#(?P<closed>\d+)|Part of #(?P<part_of>\d+) \(slice \S+ of \d+\)"
)
FENCE_OPEN = re.compile(r" {0,3}(?P<fence>`{3,}|~{3,})")
ITEM_RE = re.compile(r"- \*\*(?P<id>[OD]-\d+)\*\* (?P<text>.+)")
OUTCOME_ENTRY = re.compile(r"(?P<id>O-\d+)(?: \((?P<part>[^()]+)\))?")


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

    outcomes are the issue's O-n ids the slice delivers. child is the slice's
    own issue, an existing sub-issue the plan maps to it or the one `start` or
    `split-out` created, or None. status is pending, in_progress, in_review,
    merged, or handed_off for a slice handed to someone outside the flow.
    phased_session is the session whose plan and phases artifacts hold the
    slice's approved phased plan, when the planning run phased it (`render
    --phased`), else None; phased_body_sha is the body_sha of the issue's
    description that plan was written against. ready_body_sha is the body_sha
    of the description of the issue the slice is worked on that the ready check
    which labelled it `dovo:ready` read, or None when no check is recorded.
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
    phased_session: str | None = None
    phased_body_sha: str | None = None
    ready_body_sha: str | None = None


@dataclass
class WorkPlan:
    """The parsed `dovo:workplan` JSON line: the plan session to load, every slice row and the description planned against.

    body_sha is the body_sha of the issue's description the plan was written against, or None when not recorded.
    """

    plan_session: str
    plan_revision: int | None
    slices: list[Slice]
    body_sha: str | None = None


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


def _optional_str(value: object) -> str | None:
    """Return value as text, or None when it is null."""
    return None if value is None else str(value)


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
        child=_optional_int(data.get("child"), f"slice {slice_id} child"),
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
    one slice, since an issue sliced into two or more is an epic, whose contract
    names the one owner of each surface its slices share.

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
    """Return row with old's size and child (unless row names them), status, PR, session and ready check.

    A started slice also keeps the phased plan it started from, unless row names one; a pending
    slice takes row's, so planning the issue again drops a phased plan it did not write.
    """
    if old is None:
        return row
    size = row.size if row.size is not None else old.size
    child = row.child if row.child is not None else old.child
    phased, phased_sha = row.phased_session, row.phased_body_sha
    if phased is None and old.status != "pending":
        phased, phased_sha = old.phased_session, old.phased_body_sha
    return replace(
        row,
        size=size,
        status=old.status,
        pr=old.pr,
        session=old.session,
        child=child,
        phased_session=phased,
        phased_body_sha=phased_sha,
        ready_body_sha=old.ready_body_sha if child == old.child else None,
    )


# --- the comment --------------------------------------------------------------


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


def set_section(body: str, heading: str, text: str) -> str:
    """Return body with the section titled heading replaced by text, else appended."""
    span = find_section(body, heading)
    if span is None:
        return body.rstrip("\n") + "\n\n" + text if body.strip() else text
    start, end = span
    tail = body[end:]
    return body[:start] + text + ("\n" + tail if tail else "")


def split_workplan(text: str) -> WorkPlan:
    """Return the work plan parsed from the comment text's `dovo:workplan` JSON line.

    Anything after that line in the section, such as an earlier version's "as landed" reports, is ignored.

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
        revision = _optional_int(data.get("plan_revision"), "plan_revision")
        return WorkPlan(str(data["plan_session"]), revision, slices, _optional_str(data.get("body_sha")))
    except (json.JSONDecodeError, KeyError, ValueError) as error:
        raise WorkplanError(f"the dovo:workplan comment is malformed: {error}") from error


def lf(text: str) -> str:
    """Return text with CRLF line endings, as a GitHub web edit writes them, normalized to LF."""
    return text.replace("\r\n", "\n")


def current_rows(text: str, plan_slices: list[Slice]) -> list[Slice]:
    """Return plan_slices merged with the comment text's work plan rows; text without a `## Work plan` gives them as they are."""
    if find_section(text, WORKPLAN_HEADING) is None:
        return plan_slices
    return merge_rows(plan_slices, split_workplan(text).slices)


def _stored_slice(data: dict[str, object]) -> Slice:
    """Return one slice row from the dovo:workplan JSON line."""
    return Slice(
        id=str(data["id"]),
        title=str(data["title"]),
        depends_on=[str(item) for item in _list(data.get("depends_on"))],
        blocked_by=[int(str(item)) for item in _list(data.get("blocked_by"))],
        closing_docs=data.get("closing_docs") is True,
        outcomes=[str(item) for item in _list(data.get("outcomes"))],
        status=str(data["status"]),
        pr=_optional_int(data.get("pr"), "pr"),
        session=_optional_str(data.get("session")),
        size=_size(data.get("size"), str(data["id"])),
        child=_optional_int(data.get("child"), "child"),
        phased_session=_optional_str(data.get("phased_session")),
        phased_body_sha=_optional_str(data.get("phased_body_sha")),
        ready_body_sha=_optional_str(data.get("ready_body_sha")),
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


def render_workplan(plan: WorkPlan) -> str:
    """Return the `## Work plan` section: the plan session line, one table row per slice and the JSON line.

    Args:
        plan: The work plan to render.

    Returns:
        The section text, ending with a newline.
    """
    revision = "" if plan.plan_revision is None else f" (plan revision {plan.plan_revision})"
    rows = [
        [
            row.id,
            row.title,
            ", ".join(row.outcomes),
            ", ".join(row.depends_on + _refs(row.blocked_by)),
            row.status,
            f"#{row.child}" if row.child is not None else "",
            f"#{row.pr}" if row.pr is not None else "",
            row.session or "",
        ]
        for row in plan.slices
    ]
    table = _table(["Slice", "Scope", "Outcomes", "Depends on", "Status", "Issue", "PR", "Code session"], rows)
    parts = [WORKPLAN_HEADING, f"Plan Session ID: `{plan.plan_session}`{revision}", "", table, ""]
    parts.append(f"<!-- dovo:workplan {_comment_json(plan)} -->")
    return "\n".join(parts) + "\n"


def with_workplan(text: str, plan: WorkPlan) -> str:
    """Return the comment text with its `## Work plan` section replaced by plan."""
    return set_section(text, WORKPLAN_HEADING, render_workplan(plan))


# --- a slice's own issue ------------------------------------------------------


def _section_body(body: str, heading: str) -> str:
    """Return the text under the `## ` section titled heading in body, without the heading, or ""."""
    span = find_section(body, heading)
    if span is None:
        return ""
    _, _, rest = body[span[0] : span[1]].partition("\n")
    return rest.strip("\n")


def _items(body: str, heading: str) -> dict[str, str]:
    """Return the `- **<id>** <text>` items under body's section titled heading, by id."""
    found: dict[str, str] = {}
    for line in _section_body(body, heading).split("\n"):
        match = ITEM_RE.fullmatch(line.rstrip())
        if match is not None:
            found.setdefault(match["id"], match["text"].strip())
    return found


def _outcome_line(entry: str, outcomes: dict[str, str], issue: int) -> tuple[str, str]:
    """Return the outcome id a slice's outcomes entry names and its line in the slice's issue."""
    match = OUTCOME_ENTRY.fullmatch(entry.strip())
    ident = match["id"] if match else entry.strip()
    part = match["part"] if match else None
    text = outcomes.get(ident, f"Outcome {ident} of #{issue}.")
    return ident, f"- **{ident}** {text}" + (f" This slice delivers only its {part} part." if part else "")


def plan_decisions(plan: object) -> dict[str, str]:
    """Return plan.json's `decisions` as their one-sentence text, by id."""
    found: dict[str, str] = {}
    for raw in _list(_object(plan, "plan.json").get("decisions")):
        item = _object(raw, "each plan.json decision")
        ident, text = str(item.get("id") or ""), " ".join(str(item.get("decision") or "").split())
        if ident and text:
            found.setdefault(ident, text)
    return found


def slice_issue_body(issue: int, count: int, row: Slice, description: str, decisions: dict[str, str]) -> str:
    """Return the description of row's own issue, in the short issue format, with no hard line wraps.

    Why is the issue's; Outcomes are the issue's outcomes the slice delivers, with their ids and text;
    Decisions are the issue's, then those of decisions (plan.json's) it does not have; Done when names
    the outcomes.
    """
    body = lf(description)
    why = " ".join(_section_body(body, "## Why").split()) or f"{row.title}."
    outcomes = _items(body, "## Outcomes")
    lines = [_outcome_line(entry, outcomes, issue) for entry in row.outcomes]
    settled = _items(body, "## Decisions")
    for ident, text in decisions.items():
        settled.setdefault(ident, text)
    parts = [
        f"Slice {row.id} of #{issue} ({count} slices): {row.title}. The work plan comment on #{issue} tracks it.",
        "",
        "## Why",
        why,
        "",
        "## Outcomes",
        *([line for _, line in lines] or [f"- {row.title}."]),
    ]
    if settled:
        parts += ["", "## Decisions", *(f"- **{ident}** {text}" for ident, text in settled.items())]
    ids = list(dict.fromkeys(ident for ident, _ in lines))
    done = f"{', '.join(ids)} hold." if ids else f"{row.title}."
    parts += ["", "## Done when", done]
    return "\n".join(parts) + "\n"


def split_out_body(issue: int, plan: WorkPlan, row: Slice) -> str:
    """Return the description `split-out` gives row's own issue: the slice it tracks and the outcomes it delivers."""
    outcomes = ", ".join(row.outcomes) or "none listed"
    return (
        f"Slice {row.id} of #{issue} ({len(plan.slices)} slices), split out to its own issue.\n\n"
        f"Delivers {outcomes} of #{issue}. The work plan comment on #{issue} tracks it.\n"
    )


# --- slice selection ----------------------------------------------------------


def _done(row: Slice, is_closed: Callable[[int], bool]) -> bool:
    """Return whether row needs no more work: merged, or handed off to its own issue, now closed."""
    if row.status == "merged":
        return True
    return row.status == HANDED_OFF and row.child is not None and is_closed(row.child)


def remaining(plan: WorkPlan, slice_id: str, is_closed: Callable[[int], bool]) -> int:
    """Return how many slices other than slice_id are not done yet (see `_done`)."""
    return sum(1 for row in plan.slices if row.id != slice_id and not _done(row, is_closed))


def closing_lines(issue: int, plan: WorkPlan, row: Slice, is_closed: Callable[[int], bool]) -> str:
    """Return the lines a slice's pull request ends with.

    A slice with its own issue first closes it, `Closes #<child>`. Then `Closes #N`
    when every other slice is done, else `Part of #N` naming the slice and how many
    slices the work plan has, as in `Part of #925 (slice S2 of 4)`.
    """
    own = [] if row.child is None else [f"Closes #{row.child}"]
    if remaining(plan, row.id, is_closed) == 0:
        return "\n".join([*own, f"Closes #{issue}"])
    return "\n".join([*own, f"Part of #{issue} (slice {row.id} of {len(plan.slices)})"])


def closing_outputs(issue: int, plan: WorkPlan, row: Slice, is_closed: Callable[[int], bool]) -> dict[str, str]:
    """Return the `remaining` and `closing_lines` outputs for row's pull request."""
    return {
        "remaining": str(remaining(plan, row.id, is_closed)),
        "closing_lines": closing_lines(issue, plan, row, is_closed),
    }


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


def _wait_reason(row: Slice, done: set[str], is_closed: Callable[[int], bool]) -> str | None:
    """Return why a pending slice cannot start yet, or None when it is ready."""
    waiting = [dep for dep in row.depends_on if dep not in done]
    if waiting:
        return f"{row.id} waits on {', '.join(waiting)}"
    still_open = [number for number in row.blocked_by if not is_closed(number)]
    if still_open:
        return f"{row.id} is blocked by open {', '.join(_refs(still_open))}"
    return None


def pick(plan: WorkPlan, is_closed: Callable[[int], bool]) -> Slice:
    """Return the first pending slice whose depends_on slices are done and whose blocked_by issues are closed.

    A handed-off slice is never picked; a slice that depends on it waits until its own issue is closed.

    Raises:
        WorkplanWait: When a slice's pull request is in review, or no pending slice is ready, saying why;
            a person settles either by merging or closing what the slice waits on.
        WorkplanError: When a slice is in progress, or no slice is pending.
    """
    refusal = _active_refusal(plan)
    if refusal is not None:
        raise refusal
    done = {row.id for row in plan.slices if _done(row, is_closed)}
    reasons: dict[str, str] = {}
    for row in plan.slices:
        if row.status != "pending":
            continue
        reason = _wait_reason(row, done, is_closed)
        if reason is None:
            return row
        reasons[row.id] = reason
    if not reasons:
        raise WorkplanError("every slice is merged or handed off")
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


def refuse_v1_workplan(issue: int) -> None:
    """Refuse an issue whose description holds a `<!-- dovo:workplan {` line, the work plan v1 keeps there.

    Raises:
        WorkplanError: When the description holds that line.
    """
    if any(line.lstrip().startswith(V1_WORKPLAN) for line in lf(read_body(issue)).split("\n")):
        raise WorkplanError(
            f"#{issue} was planned with v1's description-based work plan: its description holds a "
            f"`{V1_WORKPLAN}` line, which v2 never reads; work it with the v1 blueprints"
        )


def issue_closed(issue: int) -> bool:
    """Return whether issue is closed."""
    return view_issue(issue, "state")["state"] == "CLOSED"


def issue_labels(issue: int) -> set[str]:
    """Return the names of issue's labels."""
    return {str(_object(raw, "a label").get("name")) for raw in _list(view_issue(issue, "labels").get("labels"))}


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

    Every other comment on the issue, such as one an earlier version posted, is ignored.

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
                "shorten what a person added to it and run the command again"
            )
        if read_workplan(issue) == comment:
            write_workplan(issue, comment.id, new)
            return new
    raise WorkplanError(refusal)


def create_child(row: Slice, body: str) -> int:
    """Create row's own issue with body, without a label, and return its number.

    Raises:
        WorkplanError: When the issue was created but its number cannot be read from gh's output.
    """
    output = _with_file(body, lambda path: run("gh", "issue", "create", "--title", row.title, "--body-file", path))
    try:
        return int(output.splitlines()[-1].rstrip("/").rsplit("/", 1)[-1])
    except (ValueError, IndexError) as error:
        raise WorkplanError(
            f"created a child issue for {row.id} but cannot read its number from gh's output {output!r}; "
            f"record it as {row.id}'s child by hand"
        ) from error


def check_hand_off(row: Slice) -> None:
    """Refuse to hand off row unless it is pending or already handed off.

    Raises:
        WorkplanError: When row has started or merged.
    """
    if row.status not in ("pending", HANDED_OFF):
        raise WorkplanError(f"{row.id} is {row.status}; only a pending slice can be handed off")


def _with_child(text: str, slice_id: str, child: int, *, handed_off: bool) -> str:
    """Return the comment text with child recorded as slice_id's own issue, and the slice handed off when asked.

    A new child has had no ready check, so the slice's recorded one is dropped.
    """
    plan = split_workplan(text)
    row = find_slice(plan, slice_id)
    if row.child != child:
        row.child, row.ready_body_sha = child, None
    if handed_off:
        check_hand_off(row)
        row.status = HANDED_OFF
    return with_workplan(text, plan)


def record_hint(slice_id: str, child: int, handed_off: bool) -> str:
    """Return how a person records child as slice_id's own issue by hand, handed off when asked."""
    return f"record #{child} as {slice_id}'s child" + (" and mark the slice handed_off" if handed_off else "")


def record_child(issue: int, slice_id: str, child: int, *, handed_off: bool) -> None:
    """Record child, just created, as slice_id's own issue in issue's work plan comment, handed off when asked.

    Raises:
        WorkplanError: When the comment changed twice, naming the created child.
    """
    refusal = (
        f"created #{child} but #{issue}'s work plan comment kept changing; {record_hint(slice_id, child, handed_off)}"
    )
    update_workplan(issue, lambda text: _with_child(text, slice_id, child, handed_off=handed_off), refusal)


def hand_off(issue: int, slice_id: str, child: int) -> None:
    """Mark slice_id, whose own issue is child, handed off in issue's work plan comment.

    Raises:
        WorkplanError: When the slice has started, or the comment changed twice.
    """
    refusal = f"#{issue}'s work plan comment kept changing while handing off {slice_id}; run split-out again"
    update_workplan(issue, lambda text: _with_child(text, slice_id, child, handed_off=True), refusal)


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


def ensure_child(issue: int, row: Slice, body: Callable[[], str], *, handed_off: bool, command: str) -> int:
    """Return row's own issue, creating it with body() when it has none, recorded on its row and linked to issue.

    The created issue is recorded on the slice's row before it is linked, so a
    run that fails after creating it resumes by linking that issue, never by
    creating another. It carries no label. With handed_off, the slice is also
    marked `handed_off`. command names the subcommand to run again.
    """
    child = row.child
    if child is None:
        created = create_child(row, body())
        hint = record_hint(row.id, created, handed_off)
        _after_creating(created, "recording", hint, lambda: record_child(issue, row.id, created, handed_off=handed_off))
        child = created
    elif handed_off and row.status != HANDED_OFF:
        hand_off(issue, row.id, child)
    linked = child
    _after_creating(linked, "linking", f"run {command} again to link it", lambda: link_child(issue, linked))
    return linked


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


def apply_sweep(path: Path, worked_on: set[int]) -> int:
    """Apply every entry of sweep.json and return how many failed; `[]` is an empty sweep.

    An entry for one of worked_on, the issues the slice is worked on, whose
    descriptions only a person writes, is skipped and reported on stderr. So is
    an entry whose issue body changed since the sweep was drafted. Neither
    counts as a failure. An entry that fails for any other reason is reported
    and counted. Either way the rest are still applied. File paths in an entry
    are relative to the directory holding sweep.json.
    """
    failures = 0
    for raw in _list(_load_json(str(path))):
        entry = _object(raw, "each sweep.json entry")
        if str(entry.get("issue")) in {str(issue) for issue in worked_on}:
            sys.stderr.write(
                f"workplan.py: sweep skipped #{entry.get('issue')}: the slice is worked on it, "
                "and only a person writes its description\n"
            )
            continue
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


def _rendered(text: str, plan: WorkPlan, ready_body_sha: str | None) -> str:
    """Return the comment text with plan, its slices merged with the rows already there.

    Given ready_body_sha, each slice worked on the issue itself, with no issue of its own, records it.
    """
    rows = current_rows(text, plan.slices)
    if ready_body_sha is not None:
        rows = [replace(row, ready_body_sha=ready_body_sha) if row.child is None else row for row in rows]
    return with_workplan(text, replace(plan, slices=rows))


def phased_slices(data: object, slices: list[Slice], session: str, body_sha: str) -> list[Slice]:
    """Return slices with the plan's one slice recorded as phased in session, against the description body_sha names.

    Raises:
        WorkplanError: When the plan has more than one slice, or its slice lists no phases.
    """
    if len(slices) != 1:
        raise WorkplanError(f"--phased-body-sha needs a plan with one slice, not {len(slices)}")
    raw = _object(_list(_object(data, "plan.json").get("slices"))[0], "each plan.json slice")
    if not _list(raw.get("phases")):
        raise WorkplanError(f"--phased-body-sha needs slice {slices[0].id} to list its phases in plan.json")
    return [replace(slices[0], phased_session=session, phased_body_sha=body_sha)]


def cmd_render(issue: int, args: argparse.Namespace) -> None:
    """Write the work plan from plan.json into issue's work plan comment, creating it when absent.

    Given `--phased-body-sha`, the plan's one slice is recorded as phased in this session (see `phased_slices`).
    `--body-sha` records the description the plan was written against, and `--ready-body-sha` the one the
    ready check that labelled the issue read.
    """
    data = _load_json(args.plan)
    _, slices = parse_plan(data)
    if args.phased_body_sha is not None:
        slices = phased_slices(data, slices, args.session, args.phased_body_sha)
    plan = WorkPlan(args.session, args.revision, slices, args.body_sha)
    refuse_v1_workplan(issue)
    refusal = f"#{issue}'s work plan comment kept changing while rendering it; run render again"
    update_workplan(issue, lambda text: _rendered(text, plan, args.ready_body_sha), refusal)


def _refreshed(text: str) -> str:
    """Return the comment text with every active slice whose pull request merged marked merged."""
    return with_workplan(text, refresh_merged(split_workplan(text), pr_state))


def cmd_next(issue: int) -> dict[str, str]:
    """Pick the next slice, after marking the merged ones, and return the step outputs; no issue is created."""
    refuse_v1_workplan(issue)
    refusal = f"#{issue}'s work plan comment kept changing while marking merged slices; run next again"
    plan = split_workplan(update_workplan(issue, _refreshed, refusal))
    row = pick(plan, issue_closed)
    landed = [f"{done.id}={done.session}" for done in plan.slices if done.status in LANDED_STATUSES and done.session]
    return {
        "slice": row.id,
        "plan_session": plan.plan_session,
        "plan_revision": "" if plan.plan_revision is None else str(plan.plan_revision),
        "body_sha": plan.body_sha or "",
        "phased_session": row.phased_session or "",
        "landed": " ".join(landed),
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


def cmd_start(issue: int, slice_id: str, plan_path: str) -> dict[str, str]:
    """Return the issue slice_id is worked on and its closing lines, giving it its own issue first on a sliced plan.

    On a plan of two or more slices, a slice with no issue of its own gets one, a sub-issue of issue in the
    short issue format (see `slice_issue_body`), built from issue's description and plan.json's decisions.
    A slice that has its own issue keeps it, linked as a sub-issue of issue. A one-slice plan's slice is
    worked on issue itself.
    """
    plan = split_workplan(read_workplan(issue).text)
    row = find_slice(plan, slice_id)
    if row.child is not None or len(plan.slices) >= 2:
        decisions = plan_decisions(_load_json(plan_path))

        def body() -> str:
            return slice_issue_body(issue, len(plan.slices), row, read_body(issue), decisions)

        ensure_child(issue, row, body, handed_off=False, command="start")
        plan = split_workplan(read_workplan(issue).text)
        row = find_slice(plan, slice_id)
    worked_on = issue if row.child is None else row.child
    return {"issue": str(worked_on), **closing_outputs(issue, plan, row, issue_closed)}


def _claimed(text: str, slice_id: str, session: str) -> str:
    """Return the comment text with the slice in_progress for session, refusing a slice that is not pending."""
    plan = split_workplan(text)
    row = find_slice(plan, slice_id)
    if row.status == "in_progress" and row.session == session:
        return text
    if row.status != "pending":
        raise WorkplanError(f"{slice_id} is {row.status}, not pending")
    problem = _active_problem(plan)
    if problem is not None:
        raise WorkplanError(problem)
    row.status, row.session = "in_progress", session
    return with_workplan(text, plan)


def cmd_claim(issue: int, slice_id: str, session: str) -> None:
    """Mark the slice in_progress with this code session."""
    refusal = f"#{issue}'s work plan comment kept changing while claiming {slice_id}; run claim again"
    update_workplan(issue, lambda text: _claimed(text, slice_id, session), refusal)


def amend(plan: WorkPlan, args: argparse.Namespace) -> WorkPlan:
    """Return plan following `--plan`, repointed at this session when the slice amended it.

    The plan is amended when its slice rows differ from the work plan's, or it has a contract that differs
    from `--base-plan`'s, the epic plan this run started from; the next slice is then planned from this
    session's plan artifacts.

    Raises:
        WorkplanError: When the plan redefines a slice that has already started (see `check_started_unchanged`).
    """
    contract, plan_slices = parse_plan(_load_json(args.plan))
    raw_base = _object(_load_json(args.base_plan), "the epic plan.json").get("contract")
    base = None if raw_base is None else _object(raw_base, "the epic plan.json contract")
    check_started_unchanged(plan_slices, plan.slices)
    rows = merge_rows(plan_slices, plan.slices)
    if rows == plan.slices and contract in (None, base):
        return plan
    return replace(plan, plan_session=args.session, plan_revision=None, slices=rows)


def _landed(text: str, args: argparse.Namespace) -> str:
    """Return the comment text with the slice in_review with its PR and the plan amended."""
    slice_id: str = args.slice
    session: str = args.session
    pr: int = args.pr
    plan = split_workplan(text)
    row = find_slice(plan, slice_id)
    landable = row.status == "in_progress" or (row.status == "in_review" and row.pr == pr)
    if not landable or row.session != session:
        raise WorkplanError(f"{slice_id} is {row.status} for session {row.session}, not in_progress for {session}")
    plan = amend(plan, args)
    row = find_slice(plan, slice_id)
    row.status, row.pr = "in_review", pr
    return with_workplan(text, plan)


def cmd_land(issue: int, args: argparse.Namespace) -> int:
    """Mark the slice in_review with its PR, follow an amended plan or contract and apply the sweep.

    Writes `remaining` and `closing_lines` to `$DOVO_OUTPUT`, computed from the plan as landed. The sweep
    never writes issue or the slice's own issue.
    """
    refusal = f"#{issue}'s work plan comment kept changing while landing {args.slice}; run land again"
    plan = split_workplan(update_workplan(issue, lambda current: _landed(current, args), refusal))
    row = find_slice(plan, args.slice)
    write_outputs(closing_outputs(issue, plan, row, issue_closed))
    if args.sweep is None:
        return 0
    failures = apply_sweep(Path(args.sweep), {issue} if row.child is None else {issue, row.child})
    if failures:
        raise WorkplanError(f"{failures} sweep entries failed; see above")
    return 0


def cmd_split_out(issue: int, slice_id: str, *, handed_off: bool) -> dict[str, str]:
    """Give one slice its own issue, linked as a sub-issue of issue, and return it as the `child` output.

    A slice that already has its own issue keeps it (see `ensure_child`). With handed_off, the slice is
    also marked `handed_off`, which only a pending slice can be.
    """
    plan = split_workplan(read_workplan(issue).text)
    row = find_slice(plan, slice_id)
    if handed_off:
        check_hand_off(row)
    child = ensure_child(
        issue, row, lambda: split_out_body(issue, plan, row), handed_off=handed_off, command="split-out"
    )
    return {"child": str(child)}


def phased_plan(plan: WorkPlan, row: Slice, body_sha: str) -> tuple[str, str]:
    """Return the session holding row's approved phased plan when it still holds, else "", with the reason.

    It holds while the slice is worked on the issue it was phased for, the work
    plan still comes from the session that phased it (no landed slice amended
    the plan since), and the issue's description is the one it was phased
    against (body_sha, from `plan.py body-sha`).
    """
    if row.phased_session is None:
        return "", f"{row.id} has no phased plan; it is phased in this run."
    again = f"so {row.id} is phased again in this run."
    if row.child is not None:
        return "", f"{row.id} is now worked on its own issue #{row.child}, not the one it was phased for, {again}"
    if row.phased_session != plan.plan_session:
        return "", f"the work plan now comes from session {plan.plan_session}, not {row.phased_session}, {again}"
    if row.phased_body_sha != body_sha:
        return "", f"the issue's description changed since {row.id} was phased, {again}"
    return (
        row.phased_session,
        f"{row.id} was phased and approved in session {row.phased_session}; planning passes through.",
    )


def cmd_phased(issue: int, slice_id: str, body_sha: str) -> dict[str, str]:
    """Return `phased_session`, the session whose phased plan the slice is coded from, or "" (see `phased_plan`)."""
    plan = split_workplan(read_workplan(issue).text)
    session, reason = phased_plan(plan, find_slice(plan, slice_id), body_sha)
    sys.stdout.write(f"{reason}\n")
    return {"phased_session": session}


def _readied(text: str, slice_id: str, body_sha: str) -> str:
    """Return the comment text with body_sha recorded as the description the slice's issue was readied for."""
    plan = split_workplan(text)
    find_slice(plan, slice_id).ready_body_sha = body_sha
    return with_workplan(text, plan)


def cmd_ready(issue: int, slice_id: str, body_sha: str) -> None:
    """Record body_sha as the description of the slice's issue the ready check that labelled it read."""
    refusal = f"#{issue}'s work plan comment kept changing while recording {slice_id}'s ready check; run ready again"
    update_workplan(issue, lambda text: _readied(text, slice_id, body_sha), refusal)


def cmd_ready_state(issue: int, slice_id: str, body_sha: str) -> dict[str, str]:
    """Return `ready=true` when the slice's issue is labelled dovo:ready for its description now, body_sha.

    The reason is written to stdout.
    """
    row = find_slice(split_workplan(read_workplan(issue).text), slice_id)
    target = issue if row.child is None else row.child
    if READY_LABEL not in issue_labels(target):
        reason, ready = f"Issue {target} is not labelled {READY_LABEL}; checking it.", False
    elif row.ready_body_sha is None:
        reason, ready = f"No ready check of issue {target} is recorded in the work plan; checking it.", False
    elif row.ready_body_sha != body_sha:
        reason, ready = f"Issue {target}'s description changed since the check that labelled it; checking it.", False
    else:
        reason, ready = f"Issue {target} is labelled {READY_LABEL} for its description as it is; not checking it.", True
    sys.stdout.write(f"{reason}\n")
    return {"ready": "true" if ready else "false"}


def cmd_show(issue: int) -> dict[str, str]:
    """Return the work plan's `plan_session` and `body_sha`, both "" when the issue has no work plan comment."""
    comment = read_workplan(issue)
    if comment.id is None:
        return {"plan_session": "", "body_sha": ""}
    plan = split_workplan(comment.text)
    return {"plan_session": plan.plan_session, "body_sha": plan.body_sha or ""}


def closed_issue(line: str) -> int | None:
    """Return the issue a closing line names, or None when line is not one.

    A closing line is, alone on its line, a GitHub closing keyword in any case
    (close, closes, closed, fix, fixes, fixed, resolve, resolves, resolved),
    with or without a colon, and `#<n>`; or `Part of #<n> (slice <id> of <count>)`.
    """
    match = CLOSING_LINE_RE.fullmatch(line.strip())
    return None if match is None else int(match["closed"] or match["part_of"])


def prose_lines(body: str) -> list[tuple[str, bool]]:
    """Return body's lines, each with whether it is prose: outside a fenced code block and not a fence itself.

    Fences follow CommonMark, as plan.py reads them: a block opens on a line
    indented at most 3 spaces that starts with 3 or more backticks or tildes,
    and closes only on a line of the same character, at least as long, indented
    at most 3 spaces and with nothing after it but whitespace. A block left open
    runs to the end of body.
    """
    closing: re.Pattern[str] | None = None
    marked: list[tuple[str, bool]] = []
    for line in lf(body).split("\n"):
        if closing is not None:
            closing = None if closing.fullmatch(line) else closing
            marked.append((line, False))
            continue
        opening = FENCE_OPEN.match(line)
        if opening is not None:
            fence = opening["fence"]
            closing = re.compile(rf" {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*")
        marked.append((line, opening is None))
    return marked


def ending_lines(body: str, issues: set[int]) -> list[str]:
    """Return, in order, the closing lines in body's prose that name one of issues."""
    return [line.strip() for line, prose in prose_lines(body) if prose and closed_issue(line) in issues]


def with_closing_lines(body: str, lines: list[str], issues: set[int]) -> str:
    """Return body without the closing lines in its prose that name one of issues, and lines after one blank line."""
    kept = "\n".join(line for line, prose in prose_lines(body) if not (prose and closed_issue(line) in issues)).rstrip()
    return "\n".join([kept, "", *lines]) + "\n" if kept else "\n".join(lines) + "\n"


def cmd_pr_links(pr: int, closing: str) -> None:
    """End pull request pr's description with closing, one closing line per line, when it ends with others.

    The issues closing names are the plan's issue and the slice's own issue,
    when it has one. Every closing line in the description's prose that names
    one of them is replaced; a closing line naming any other issue, and any line
    inside a fenced code block, is left where it is.

    Raises:
        WorkplanError: When closing is empty or holds a line other than the `Closes #<n>` and
            `Part of #<n> (slice <id> of <count>)` lines `land` writes.
    """
    lines = [line.strip() for line in lf(closing).split("\n") if line.strip()]
    issues = {named for line in lines if (named := closed_issue(line)) is not None}
    wrong = [line for line in lines if not WRITTEN_LINE_RE.fullmatch(line)]
    if not lines or wrong:
        raise WorkplanError(
            f"--closing-lines must be lines such as `Closes #925` or `Part of #925 (slice S2 of 4)`, not {closing!r}"
        )
    body = str(_object(json.loads(run("gh", "pr", "view", str(pr), "--json", "body")), "gh pr view")["body"])
    if ending_lines(body, issues) == lines:
        sys.stdout.write(f"#{pr}'s description already ends with its closing lines.\n")
        return
    replaced = with_closing_lines(body, lines, issues)
    _with_file(replaced, lambda path: run("gh", "pr", "edit", str(pr), "--body-file", path))
    sys.stdout.write(f"#{pr}'s description now ends with: {'; '.join(lines)}.\n")


def write_outputs(outputs: dict[str, str]) -> None:
    """Append outputs to the `$DOVO_OUTPUT` file, or print them when it is not set."""
    text = output_lines(outputs)
    target = os.environ.get("DOVO_OUTPUT")
    if not target:
        sys.stdout.write(text)
        return
    with Path(target).open("a", encoding="utf-8") as handle:
        handle.write(text)


def _render_options(render: argparse.ArgumentParser) -> None:
    """Add render's options to its parser."""
    render.add_argument("--plan", required=True, help="Path to plan.json.")
    render.add_argument("--revision", type=int, required=True, help="The plan artifact's revision.")
    render.add_argument("--body-sha", help="The body_sha of the issue's description the plan was written against.")
    render.add_argument(
        "--ready-body-sha",
        help="The body_sha of the issue's description the ready check that labelled it dovo:ready read.",
    )
    render.add_argument(
        "--phased-body-sha",
        help="Record the plan's one slice as phased and approved in this session, against this body_sha.",
    )


def build_parser() -> argparse.ArgumentParser:
    """Return the parser for the workplan.py subcommands."""
    parser = argparse.ArgumentParser(description="Keep an issue's work plan comment current.")
    commands = parser.add_subparsers(dest="command", required=True)
    render = commands.add_parser("render", help="Write the work plan from plan.json.")
    nxt = commands.add_parser("next", help="Pick the next slice and write its outputs to $DOVO_OUTPUT.")
    start = commands.add_parser("start", help="Write the issue the slice is worked on, giving it its own first.")
    claim = commands.add_parser("claim", help="Mark a slice in_progress with this code session.")
    land = commands.add_parser("land", help="Mark a slice in_review and apply the sweep.")
    split = commands.add_parser("split-out", help="Give one slice its own issue, linked as a sub-issue.")
    phased = commands.add_parser("phased", help="Write the session whose phased plan still holds for the slice.")
    ready = commands.add_parser("ready", help="Record the description the slice's issue was readied for.")
    state = commands.add_parser("ready-state", help="Write ready=true when the slice's issue is ready as it is.")
    show = commands.add_parser("show", help="Write the work plan's plan session and the description it planned.")
    links = commands.add_parser("pr-links", help="Make a pull request's description end with the closing lines.")
    links.add_argument("--pr", type=int, required=True, help="The slice's pull request number.")
    links.add_argument("--closing-lines", required=True, help="The closing lines, one per line, as land writes them.")
    for sub in (render, nxt, start, claim, land, split, phased, ready, state, show):
        sub.add_argument("--issue", type=int, required=True, help="The issue whose work plan holds the slices.")
    _render_options(render)
    for sub in (phased, ready, state):
        sub.add_argument("--body-sha", required=True, help="The body_sha of the slice's issue's description now.")
    nxt.add_argument(
        "--hold",
        type=Path,
        help="Write the slices waiting on a person here and exit 0 without outputs, instead of refusing.",
    )
    for sub in (start, claim, land, split, phased, ready, state):
        sub.add_argument("--slice", required=True, help="The slice id, such as S2.")
    start.add_argument("--plan", required=True, help="Path to the plan.json the work plan's plan session holds.")
    split.add_argument(
        "--handed-off",
        action="store_true",
        help="Also mark the slice handed off to someone outside the flow, so next never picks it.",
    )
    for sub in (render, claim, land):
        sub.add_argument("--session", required=True, help="This dovo session's id.")
    land.add_argument("--pr", type=int, required=True, help="The slice's pull request number.")
    land.add_argument("--plan", required=True, help="Path to plan.json, amended or not.")
    land.add_argument(
        "--base-plan", required=True, help="Path to the epic plan.json this run started from (.agentic/epic-plan.json)."
    )
    land.add_argument("--sweep", help="Path to sweep.json.")
    return parser


def _dispatch_outputs(args: argparse.Namespace) -> dict[str, str] | None:
    """Run a subcommand that only writes outputs and return them, or None for any other subcommand."""
    issue: int = args.issue
    if args.command == "phased":
        return cmd_phased(issue, args.slice, args.body_sha)
    if args.command == "ready-state":
        return cmd_ready_state(issue, args.slice, args.body_sha)
    if args.command == "show":
        return cmd_show(issue)
    if args.command == "start":
        return cmd_start(issue, args.slice, args.plan)
    if args.command == "split-out":
        return cmd_split_out(issue, args.slice, handed_off=args.handed_off)
    if args.command == "next":
        return cmd_next_or_hold(issue, args.hold)
    return None


def dispatch(args: argparse.Namespace) -> int:
    """Run the parsed subcommand and return its exit code."""
    if args.command == "pr-links":
        cmd_pr_links(args.pr, args.closing_lines)
        return 0
    outputs = _dispatch_outputs(args)
    if outputs is not None:
        if outputs:
            write_outputs(outputs)
    elif args.command == "render":
        cmd_render(args.issue, args)
    elif args.command == "claim":
        cmd_claim(args.issue, args.slice, args.session)
    elif args.command == "ready":
        cmd_ready(args.issue, args.slice, args.body_sha)
    else:
        return cmd_land(args.issue, args)
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
