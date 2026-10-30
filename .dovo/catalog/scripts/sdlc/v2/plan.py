#!/usr/bin/env python3
"""Checks the slice plan the issue-code-v2 blueprint's planning steps write, and keeps its decisions on the issue.

`check-questions` runs after `fold-answers`, which settles every open question
as a decision. It refuses a plan whose `open_questions` is not `[]`, or whose
`decisions` holds an entry that is not a well-formed decision (the shape is
`Decision` in plan_formats.py) or reuses another's id. A plan that passes
prints nothing and exits 0.

`decisions-comment` runs once the plan is approved. It posts one comment
listing the plan's decisions to the issue, headed by a
`<!-- dovo:decisions ... -->` marker naming the slice, the session and
`body_sha`, the sha256 of the part of the issue's description a person wrote
(`body_sha` below). The comment is dated with the latest decision's date. Then
it hides, as outdated, each earlier dovo:decisions comment of another session
whose every decision this plan replaces. It never edits a comment, and a plan
with no decisions posts nothing.

`prior-decisions` runs before planning. It writes to prior-decisions.json the
decisions in the issue's dovo:decisions comments that are not hidden and that
no other decision replaces (the shape is `PriorDecision` in plan_formats.py):
each is `given` when its comment's body_sha matches the description now, and
`to_confirm` otherwise.

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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from plan_formats import SESSION_ID, Decision, FormatError, PriorDecision, prior_decisions_json

PLAN_JSON = Path(".agentic/plan.json")
PRIOR_JSON = Path(".agentic/prior-decisions.json")
MARKER = "<!-- dovo:decisions schema_version=1 slice={slice} session={session} body_sha={body_sha} -->"
MARKER_RE = re.compile(
    r"<!-- dovo:decisions schema_version=1 slice=(?P<slice>\S+) session=(?P<session>\S+)"
    r" body_sha=(?P<body_sha>[0-9a-f]{64}) -->"
)
BULLET_RE = re.compile(r"- \*\*(?P<id>D-\d+)\*\* (?P<text>.+)")
REPLACES_RE = re.compile(r"Replaces (?P<id>D-\d+) \(session (?P<session>[^\s()]+)\)\.$")
GENERATED_SECTIONS = frozenset({"## Feature contract", "## Work plan"})
SETTLED_LABELS = {"assumed_accepted": "Assumed answer, accepted", "planner": "Planner, approved with the plan"}
MINIMIZE = "mutation($id: ID!) { minimizeComment(input: {subjectId: $id, classifier: OUTDATED}) { clientMutationId } }"


class PlanError(RuntimeError):
    """Raised when a subcommand refuses; the message is the one-line reason."""


@dataclass(frozen=True)
class PostedComment:
    """One of the issue's dovo:decisions comments that is not hidden, with its decision bullets as (id, text)."""

    node_id: str
    slice: str
    session: str
    body_sha: str
    bullets: tuple[tuple[str, str], ...]


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


def check_questions(path: Path) -> None:
    """Refuse the plan at path unless every open question is settled as a well-formed decision.

    Raises:
        PlanError: open_questions is not [], or decisions is missing, malformed or reuses an id.
    """
    plan = load_plan(path)
    questions = _list(plan, "open_questions", path)
    if questions:
        named = ", ".join(_question_id(item, position) for position, item in enumerate(questions, start=1))
        raise PlanError(f"{path} still lists open questions {named}; fold-answers must settle each one as a decision.")
    load_decisions(plan, path)


# --- the dovo:decisions comment ------------------------------------------------


def body_sha(body: str) -> str:
    """Return the sha256 hex of the part of an issue's description a person wrote.

    Line endings become LF, each `## Feature contract` and `## Work plan` section
    (from its heading to the next `## ` heading or the end) is cut, every line
    loses its trailing whitespace, and the text loses its leading and trailing
    newlines, so a work plan rewrite or a stray space never marks a decision stale.
    """
    kept: list[str] = []
    cutting = False
    for line in body.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.startswith("## "):
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
    """Return raw as a dovo:decisions comment, or None when it is hidden or another comment."""
    if not isinstance(raw, dict) or raw.get("isMinimized"):
        return None
    body = str(raw.get("body") or "")
    marker = MARKER_RE.match(body)
    if marker is None:
        return None
    lines = (BULLET_RE.fullmatch(line.rstrip()) for line in body.splitlines())
    bullets = tuple((match["id"], match["text"]) for match in lines if match is not None)
    return PostedComment(str(raw.get("id")), marker["slice"], marker["session"], marker["body_sha"], bullets)


def view_issue(issue: int) -> tuple[str, list[PostedComment]]:
    """Return issue's description and its dovo:decisions comments that are not hidden.

    Raises:
        PlanError: gh printed something other than a JSON object.
    """
    try:
        view = json.loads(run("gh", "issue", "view", str(issue), "--json", "body,comments"))
    except ValueError as error:
        raise PlanError(f"gh issue view {issue} printed no JSON: {error}.") from error
    if not isinstance(view, dict):
        raise PlanError(f"gh issue view {issue} printed no JSON object.")
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


def decisions_comment(path: Path, issue: int, slice_id: str, session: str) -> None:
    """Post the plan's decisions to issue, then hide the earlier comments they wholly replace.

    Raises:
        PlanError: The plan or its decisions are malformed, or slice_id or session is not one token.
    """
    decisions = load_decisions(load_plan(path), path)
    _token(slice_id, "Slice")
    _token(session, "Session")
    if not decisions:
        return
    body, posted = view_issue(issue)
    comment = render_comment(decisions, slice_id, session, body_sha(body))
    _with_file(comment, lambda file: run("gh", "issue", "comment", str(issue), "--body-file", file))
    for earlier in outdated(posted, decisions, session):
        _hide(earlier)


def outdated(posted: list[PostedComment], decisions: list[Decision], session: str) -> list[PostedComment]:
    """Return the comments of other sessions than session whose every decision one of decisions replaces."""
    replaced = {(item.replaces.id, item.replaces.session) for item in decisions if item.replaces is not None}
    return [
        earlier
        for earlier in posted
        if earlier.session != session
        and earlier.bullets
        and all((ident, earlier.session) in replaced for ident, _ in earlier.bullets)
    ]


def prior_decisions(issue: int, out: Path) -> None:
    """Write to out the decisions of issue's dovo:decisions comments that no other decision replaces."""
    body, posted = view_issue(issue)
    sha = body_sha(body)
    replaced = {pair for comment in posted for _, text in comment.bullets if (pair := _replaced(text)) is not None}
    decisions = [
        PriorDecision(ident, text, comment.slice, comment.session, "given" if comment.body_sha == sha else "to_confirm")
        for comment in posted
        for ident, text in comment.bullets
        if (ident, comment.session) not in replaced
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(prior_decisions_json(issue, decisions), indent=2) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    """Return the parser for the check-questions, decisions-comment and prior-decisions subcommands."""
    parser = argparse.ArgumentParser(description="Check the slice plan in .agentic/plan.json and keep its decisions.")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-questions", help="Refuse a plan with an open question or a malformed decision.")
    check.add_argument("--plan", type=Path, default=PLAN_JSON, help="The plan.json to check.")
    comment = commands.add_parser("decisions-comment", help="Post the approved plan's decisions to the issue.")
    comment.add_argument("--plan", type=Path, default=PLAN_JSON, help="The approved plan.json.")
    comment.add_argument("--issue", type=int, required=True, help="The issue to post the comment on.")
    comment.add_argument("--slice", required=True, help="The slice the plan phases.")
    comment.add_argument("--session", required=True, help="The planning session's id.")
    prior = commands.add_parser("prior-decisions", help="Write the decisions earlier sessions posted to the issue.")
    prior.add_argument("--issue", type=int, required=True, help="The issue whose comments to read.")
    prior.add_argument("--out", type=Path, default=PRIOR_JSON, help="The prior-decisions.json to write.")
    return parser


def dispatch(args: argparse.Namespace) -> None:
    """Run the subcommand args names."""
    if args.command == "check-questions":
        check_questions(args.plan)
    elif args.command == "decisions-comment":
        decisions_comment(args.plan, args.issue, args.slice, args.session)
    else:
        prior_decisions(args.issue, args.out)


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
