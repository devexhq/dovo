#!/usr/bin/env python3
"""Replies on the review threads a pr-fix triage addressed, and resolves the fixed and dismissed ones.

Reads every item of .agentic/feedback.json, its approved action from
.agentic/triage.json (written by /dovo-code feedback --triage) and, for each
`fix` item, its outcome from .agentic/feedback-outcomes.json (written by
/dovo-code feedback). Every review thread item gets one reply, headed by a
`<!-- dovo-pr-fix -->` marker, saying whether it was fixed (in the commit at
HEAD), not changed or deferred, with its note; fixed and dismissed threads are
then resolved, and deferred ones stay open. Comments and failed checks get no
reply. Out-of-diff findings from the dovo review get one pull request comment,
headed `<!-- dovo-pr-fix-outcomes -->`, with one line per finding giving its id,
location, the reviewed commit the first `R#` item carries and its outcome, so
the pull request needs no dovo-review comment; a retry that finds the viewer's
comment with that exact body posts none. A thread that already holds this exact
reply from the authenticated `gh` user gets no second one, and a resolved thread
is not resolved again, so a retry posts only what is missing. Nothing is posted unless every item has a valid action and
every fix item a valid outcome.

With --render-triage it only checks .agentic/triage.json against
.agentic/feedback.json and renders .agentic/triage.md from it, one line per
item, for the person to approve; it runs no command.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from itertools import chain
from pathlib import Path
from typing import NamedTuple

from pr_feedback import AGENTIC_DIR, BACKTICKED, FEEDBACK_JSON, PR_URL
from publish_review import (
    MAX_COMMENT_CHARS,
    REPLY_MUTATION,
    RESOLVE_MUTATION,
    PublishReviewError,
    already_replied,
    list_comments,
    pr_repository,
    run,
    thread_graphql,
    write_comment,
)

TRIAGE_JSON = AGENTIC_DIR / "triage.json"
TRIAGE_MD = AGENTIC_DIR / "triage.md"
NO_FEEDBACK_LINE = "No open feedback."
OUTCOMES_JSON = AGENTIC_DIR / "feedback-outcomes.json"
REPLY_MARKER = "<!-- dovo-pr-fix -->"
OUTCOMES_MARKER = "<!-- dovo-pr-fix-outcomes -->"
REVIEWED_COMMIT_CHARS = 7
ACTIONS = ("fix", "dismiss", "defer")
FIX_OUTCOMES = ("fixed", "deferred")
ACTION_OUTCOMES = {"dismiss": "dismissed", "defer": "deferred"}
RESOLVED_OUTCOMES = ("fixed", "dismissed")
OUTCOME_LINES = {
    "fixed": "Fixed in {commit}: {note}",
    "dismissed": "Not changed: {note}",
    "deferred": "Deferred: {note}",
}
REPLIES_QUERY = """
query($thread: ID!) {
  node(id: $thread) {
    ... on PullRequestReviewThread {
      isResolved
      comments(last: 100) { nodes { author { login } body } }
    }
  }
}
"""


class ReplyThreadsError(RuntimeError):
    """Raised when the outcomes cannot be posted on the pull request's review threads."""


class Outcome(NamedTuple):
    """One triaged item's outcome, a key of OUTCOME_LINES, and the note posted with it."""

    outcome: str
    note: str


class ThreadReply(NamedTuple):
    """One triaged review thread to reply on: its feedback id, thread node id, first comment URL, outcome and note."""

    item_id: str
    thread: str
    url: str
    outcome: str
    note: str


class FindingOutcome(NamedTuple):
    """One `R#` item's line in the outcome comment: its finding id, its heading's location or None, and its outcome."""

    finding_id: str
    location: str | None
    outcome: Outcome


class Inputs(NamedTuple):
    """The checked inputs of one reply step, read before any command runs."""

    url: str  # the pull request URL, the first line of .agentic/pr-url
    number: int  # feedback.json `pr`
    replies: list[ThreadReply]  # one per review_thread item, in feedback.json order
    findings: list[FindingOutcome]  # one per review_finding item, in feedback.json order
    reviewed: str | None  # the first review_finding item's `commit`, None without one


def outcome_line(outcome: Outcome, commit: str) -> str:
    """Return the OUTCOME_LINES line for outcome, with its note's whitespace collapsed to single spaces."""
    return OUTCOME_LINES[outcome.outcome].format(commit=commit, note=" ".join(outcome.note.split()))


def reply_body(outcome: Outcome, commit: str) -> str:
    """Return REPLY_MARKER, a newline, then outcome_line(outcome, commit)."""
    return f"{REPLY_MARKER}\n{outcome_line(outcome, commit)}"


def _read_json(path: Path, problem: str) -> object:
    """Return path's parsed JSON.

    Raises:
        ReplyThreadsError: With problem when the file is missing or not JSON.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReplyThreadsError(problem) from error


def _object(value: object) -> dict[str, object]:
    """Return value when it is a JSON object, else an empty one."""
    return value if isinstance(value, dict) else {}


def _entries(document: object) -> dict[str, dict[str, object]]:
    """Return document's `items` entries by their string `id`, the first entry winning for a repeated id."""
    items = _object(document).get("items")
    entries: dict[str, dict[str, object]] = {}
    for entry in items if isinstance(items, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            entries.setdefault(str(entry["id"]), entry)
    return entries


def _has_text(value: object) -> bool:
    """Return whether value is a string holding more than whitespace."""
    return isinstance(value, str) and bool(value.strip())


def _entry_id(entry: object) -> object:
    """Return entry's `id`, or None when entry is not an object."""
    return entry.get("id") if isinstance(entry, dict) else None


def _action_problem(entry: dict[str, object]) -> str | None:
    """Return why entry's action is not one of ACTIONS, or None when it is."""
    action = entry.get("action")
    if action in ACTIONS:
        return None
    return f"{TRIAGE_JSON} entry {entry['id']} has action {action!r}, not fix, dismiss or defer"


def _text_problem(entry: dict[str, object]) -> str | None:
    """Return why a dismiss or defer entry has no reply, or a fix entry no reason, or None."""
    if entry.get("action") == "fix":
        return None if _has_text(entry.get("reason")) else f"{TRIAGE_JSON} entry {entry['id']} has no reason"
    return None if _has_text(entry.get("reply")) else f"{TRIAGE_JSON} entry {entry['id']} has no reply"


def _listing_problem(entries: list[object], item_ids: list[str]) -> str | None:
    """Return why entries do not name each id of item_ids exactly once, or None when they do."""
    unknown = [entry for entry in entries if _entry_id(entry) not in item_ids]
    if unknown:
        return f"{TRIAGE_JSON} entry {_entry_id(unknown[0])!s} names no feedback item"
    ids = [str(_entry_id(entry)) for entry in entries]
    repeated = next((item_id for index, item_id in enumerate(ids) if item_id in ids[:index]), None)
    if repeated is not None:
        return f"{TRIAGE_JSON} has more than one entry for {repeated}"
    missing = [item_id for item_id in item_ids if item_id not in ids]
    if missing:
        return f"{TRIAGE_JSON} has no entry for {', '.join(missing)}"
    return None


def triage_problem(triage: object, item_ids: list[str]) -> str | None:
    """Return why triage cannot answer every feedback item in item_ids, or None when it can.

    Checks, in this order, each message naming .agentic/triage.json:
      - `has no entry for <ids>`, for a non-object or a missing or non-list `items`, naming every id,
        or `has no items list` when item_ids is empty;
      - `entry <id> names no feedback item`, for an entry whose `id` is not in item_ids;
      - `has more than one entry for <id>`;
      - `has no entry for <ids>`, naming the missing ids in item_ids order;
      - `entry <id> has action <value!r>, not fix, dismiss or defer`, in entry order;
      - `entry <id> has no reply`, for a dismiss or defer entry whose `reply` is not a non-blank string;
      - `entry <id> has no reason`, for a fix entry whose `reason` is not a non-blank string, since triage.md shows it.
    """
    items = _object(triage).get("items")
    if not isinstance(items, list):
        if not item_ids:
            return f"{TRIAGE_JSON} has no items list"
        return f"{TRIAGE_JSON} has no entry for {', '.join(item_ids)}"
    listing = _listing_problem(items, item_ids)
    if listing is not None:
        return listing
    entries = [entry for entry in items if isinstance(entry, dict)]
    problems = chain((_action_problem(entry) for entry in entries), (_text_problem(entry) for entry in entries))
    return next((problem for problem in problems if problem is not None), None)


def _feedback_items(feedback: object) -> list[dict[str, object]]:
    """Return feedback.json's items, in order."""
    items = _object(feedback).get("items")
    return [item for item in (items if isinstance(items, list) else []) if isinstance(item, dict)]


def _read_triage() -> tuple[dict[str, object], list[dict[str, object]], dict[str, object]]:
    """Return feedback.json, its items and the checked triage.json.

    Raises:
        ReplyThreadsError: For errors 1-2 of issue #1259, or a triage that is missing or not JSON.
    """
    feedback = json.loads(FEEDBACK_JSON.read_text(encoding="utf-8"))
    items = _feedback_items(feedback)
    triage = _read_json(TRIAGE_JSON, f"{TRIAGE_JSON} is missing or not JSON")
    problem = triage_problem(triage, [str(item["id"]) for item in items])
    if problem is not None:
        raise ReplyThreadsError(problem)
    return _object(feedback), items, _object(triage)


def _triage_text(entry: dict[str, object]) -> str:
    """Return entry's reason for a fix entry, else its reply, with whitespace collapsed to single spaces."""
    text = entry.get("reason") if entry.get("action") == "fix" else entry.get("reply")
    return " ".join(str(text).split())


def triage_markdown(items: list[dict[str, object]], triage: dict[str, object]) -> str:
    """Return triage.md: per item in items order, `<label> → <action>: <reply, or reason for fix>`, or NO_FEEDBACK_LINE with no item."""
    if not items:
        return f"{NO_FEEDBACK_LINE}\n"
    entries = _entries(triage)
    lines = []
    for item in items:
        entry = entries[str(item["id"])]
        lines.append(f"{item.get('label')} → {entry['action']}: {_triage_text(entry)}")
    return "".join(f"{line}\n" for line in lines)


def render_triage() -> str:
    """Check triage.json against feedback.json, write TRIAGE_MD from it and return the line to report, running no command.

    Raises:
        ReplyThreadsError: For errors 1-2 of issue #1259, before any file is written.
    """
    _, items, triage = _read_triage()
    TRIAGE_MD.write_text(triage_markdown(items, triage), encoding="utf-8")
    return f"Rendered {len(items)} triage entries to {TRIAGE_MD}\n"


def _outcome_problem(item_id: str, entry: dict[str, object]) -> str | None:
    """Return why entry's outcome or note is unusable, or None when both are."""
    outcome = entry.get("outcome")
    if outcome not in FIX_OUTCOMES:
        return f"{OUTCOMES_JSON} entry {item_id} has outcome {outcome!r}, not fixed or deferred"
    if not _has_text(entry.get("note")):
        return f"{OUTCOMES_JSON} entry {item_id} has no note"
    return None


def outcomes_problem(outcomes: object, fix_ids: list[str]) -> str | None:
    """Return why outcomes cannot answer every fix id, or None when it can.

    Checks, in this order, each message naming .agentic/feedback-outcomes.json:
      - `has a round key`, for an object that holds `round`;
      - `has no outcome for <ids>`, for a non-object, or naming every fix id without an entry;
      - `entry <id> has outcome <value!r>, not fixed or deferred`;
      - `entry <id> has no note`.
    Entries for ids that are not fix ids are ignored, and the first entry wins for a repeated id.
    """
    if not isinstance(outcomes, dict):
        return f"{OUTCOMES_JSON} has no outcome for {', '.join(fix_ids)}"
    if "round" in outcomes:
        return f"{OUTCOMES_JSON} has a round key"
    entries = _entries(outcomes)
    missing = [item_id for item_id in fix_ids if item_id not in entries]
    if missing:
        return f"{OUTCOMES_JSON} has no outcome for {', '.join(missing)}"
    problems = (_outcome_problem(item_id, entries[item_id]) for item_id in fix_ids)
    return next((problem for problem in problems if problem is not None), None)


def item_outcomes(triage: dict[str, object], outcomes: dict[str, object]) -> dict[str, Outcome]:
    """Return each triaged id's Outcome: a fix entry's from outcomes, dismissed or deferred with its reply otherwise."""
    fixed = _entries(outcomes)
    result: dict[str, Outcome] = {}
    for item_id, entry in _entries(triage).items():
        action = str(entry["action"])
        if action == "fix":
            result[item_id] = Outcome(str(fixed[item_id]["outcome"]), str(fixed[item_id]["note"]))
        else:
            result[item_id] = Outcome(ACTION_OUTCOMES[action], str(entry["reply"]))
    return result


def thread_replies(items: list[dict[str, object]], outcomes: dict[str, Outcome]) -> list[ThreadReply]:
    """Return one ThreadReply per `review_thread` item, in items order.

    Raises:
        ReplyThreadsError: When a thread item names no review thread.
    """
    replies: list[ThreadReply] = []
    for item in items:
        if item.get("source") != "review_thread":
            continue
        item_id = str(item["id"])
        thread = item.get("thread")
        if not isinstance(thread, str) or not thread:
            raise ReplyThreadsError(f"{FEEDBACK_JSON} item {item_id} names no review thread")
        outcome = outcomes[item_id]
        replies.append(ThreadReply(item_id, thread, str(item.get("url")), outcome.outcome, outcome.note))
    return replies


def heading_location(body: str) -> str | None:
    """Return the first backticked span (BACKTICKED) of body's first line, the `#### ` heading, or None when it has none."""
    location = BACKTICKED.search(next(iter(body.splitlines()), ""))
    return location[1] if location is not None else None


def finding_commit(items: list[dict[str, object]]) -> str | None:
    """Return the first `review_finding` item's `commit`, or None when there is no such item.

    Raises:
        ReplyThreadsError: `.agentic/feedback.json item <id> names no reviewed commit` when a `review_finding`
            item's `commit` is not a string.
    """
    reviewed: str | None = None
    for item in items:
        if item.get("source") != "review_finding":
            continue
        commit = item.get("commit")
        if not isinstance(commit, str):
            raise ReplyThreadsError(f"{FEEDBACK_JSON} item {item['id']} names no reviewed commit")
        reviewed = commit if reviewed is None else reviewed
    return reviewed


def finding_outcomes(items: list[dict[str, object]], outcomes: dict[str, Outcome]) -> list[FindingOutcome]:
    """Return one FindingOutcome per `review_finding` item, in items order.

    Raises:
        ReplyThreadsError: `.agentic/feedback.json item <id> names no dovo finding` when its `finding` is not an object with an `id`.
    """
    findings: list[FindingOutcome] = []
    for item in items:
        if item.get("source") != "review_finding":
            continue
        item_id = str(item["id"])
        finding = item.get("finding")
        if not isinstance(finding, dict) or not isinstance(finding.get("id"), str):
            raise ReplyThreadsError(f"{FEEDBACK_JSON} item {item_id} names no dovo finding")
        location = heading_location(str(item.get("body") or ""))
        findings.append(FindingOutcome(str(finding["id"]), location, outcomes[item_id]))
    return findings


def outcome_comment(findings: list[FindingOutcome], reviewed: str, commit: str) -> str:
    """Return OUTCOMES_MARKER then one line per finding: `` `<id>` · `<location>` · <reviewed>: <outcome_line> ``, the location part left out with its ` · ` when None."""
    lines = [OUTCOMES_MARKER]
    for finding in findings:
        parts = [f"`{finding.finding_id}`"]
        if finding.location is not None:
            parts.append(f"`{finding.location}`")
        parts.append(reviewed)
        lines.append(f"{' · '.join(parts)}: {outcome_line(finding.outcome, commit)}")
    return "\n".join(lines)


def comment_problem(body: str) -> str | None:
    """Return `the outcome comment is <n> characters; GitHub accepts at most 65536` when body is over MAX_COMMENT_CHARS, else None."""
    if len(body) > MAX_COMMENT_CHARS:
        return f"the outcome comment is {len(body)} characters; GitHub accepts at most {MAX_COMMENT_CHARS}"
    return None


def read_inputs() -> Inputs:
    """Read and check feedback.json, triage.json, the outcomes of its fix items and pr-url, running no command.

    Raises:
        ReplyThreadsError: For errors 1-4 of issue #1259, or a thread item naming no review thread or a
            finding item no reviewed commit.
    """
    feedback, items, triage = _read_triage()
    fix_ids = [item_id for item_id, entry in _entries(triage).items() if entry["action"] == "fix"]
    outcomes: object = {}
    if fix_ids:
        outcomes = _read_json(
            OUTCOMES_JSON, f"{OUTCOMES_JSON} is missing or not JSON; no outcome for {', '.join(fix_ids)}"
        )
        problem = outcomes_problem(outcomes, fix_ids)
        if problem is not None:
            raise ReplyThreadsError(problem)
    by_id = item_outcomes(triage, _object(outcomes))
    replies = thread_replies(items, by_id)
    findings = finding_outcomes(items, by_id)
    reviewed = finding_commit(items)
    url = PR_URL.read_text(encoding="utf-8").splitlines()[0].strip()
    return Inputs(url, int(str(feedback.get("pr"))), replies, findings, reviewed)


def post_reply(host: str, viewer: str, reply: ThreadReply, commit: str) -> tuple[bool, bool]:
    """Reply on reply.thread unless already_replied, then resolve it when its outcome is in RESOLVED_OUTCOMES and it is unresolved; return (replied, resolved).

    Raises:
        ReplyThreadsError: When a gh call fails, with reply.url and gh's stderr.
    """
    try:
        state = json.loads(thread_graphql(host, REPLIES_QUERY, reply.thread, jq=".data.node"))
        body = reply_body(Outcome(reply.outcome, reply.note), commit)
        replied = not already_replied(state["comments"]["nodes"], viewer, body)
        if replied:
            thread_graphql(
                host,
                REPLY_MUTATION,
                reply.thread,
                f"body={body}",
                jq=".data.addPullRequestReviewThreadReply.comment.url",
            )
        resolved = reply.outcome in RESOLVED_OUTCOMES and not state["isResolved"]
        if resolved:
            thread_graphql(host, RESOLVE_MUTATION, reply.thread, jq=".data.resolveReviewThread.thread.isResolved")
    except subprocess.CalledProcessError as error:
        stderr = str(error.stderr or "")
        raise ReplyThreadsError(f"cannot update review thread {reply.url}: {stderr.strip()}") from error
    return replied, resolved


def post_comment(host: str, repository: str, number: int, url: str, body: str, *, posted: bool) -> bool:
    """Write body as a pull request comment unless posted, and return whether it wrote one.

    Raises:
        ReplyThreadsError: `cannot post the outcome comment on <url>: <stderr>` when the gh call fails.
    """
    if posted:
        return False
    try:
        write_comment(host, [f"repos/{repository}/issues/{number}/comments"], body)
    except subprocess.CalledProcessError as error:
        stderr = str(error.stderr or "")
        raise ReplyThreadsError(f"cannot post the outcome comment on {url}: {stderr.strip()}") from error
    return True


def _checked_comment(inputs: Inputs, comments: list[dict[str, object]], viewer: str, commit: str) -> tuple[str, bool]:
    """Return the outcome comment for inputs.findings and whether the viewer already posted it exactly.

    Raises:
        ReplyThreadsError: When the comment is too long.
    """
    body = outcome_comment(inputs.findings, str(inputs.reviewed)[:REVIEWED_COMMIT_CHARS], commit)
    problem = comment_problem(body)
    if problem is not None:
        raise ReplyThreadsError(problem)
    posted = any(
        comment.get("user") == viewer and str(comment.get("body") or "").strip() == body.strip() for comment in comments
    )
    return body, posted


def reply_all() -> str:
    """Post every triaged thread's outcome and the outcome comment, and return the line to report; with nothing to post, run no command.

    Raises:
        ReplyThreadsError: For errors 1-6 of issue #1259.
        PublishReviewError: When .agentic/pr-url names no pull request.
    """
    inputs = read_inputs()
    replied = resolved = commented = 0
    if inputs.replies or inputs.findings:
        host, repository = pr_repository(inputs.url)
        viewer = run("gh", "api", "--hostname", host, "user", "--jq", ".login")
        commit = run("git", "rev-parse", "--short", "HEAD")
        comment: tuple[str, bool] | None = None
        if inputs.findings:
            comments = list_comments(host, repository, inputs.number)
            comment = _checked_comment(inputs, comments, viewer, commit)
        for reply in inputs.replies:
            did_reply, did_resolve = post_reply(host, viewer, reply, commit)
            replied += did_reply
            resolved += did_resolve
        if comment is not None:
            body, posted = comment
            commented = int(post_comment(host, repository, inputs.number, inputs.url, body, posted=posted))
    return (
        f"Replied on {replied} review threads, resolved {resolved} "
        f"and posted {commented} outcome comment for {inputs.url}\n"
    )


def main() -> int:
    """Reply on and resolve the review threads pr-fix triaged, or with --render-triage only render the triage."""
    parser = argparse.ArgumentParser(
        description=(
            "Reply on and resolve the review threads pr-fix triaged, and post the outcomes of its out-of-diff "
            "findings; with --render-triage, only check the triage and render it for approval."
        )
    )
    parser.add_argument(
        "--render-triage",
        action="store_true",
        help="check .agentic/triage.json and render .agentic/triage.md from it, posting nothing",
    )
    args = parser.parse_args()

    try:
        sys.stdout.write(render_triage() if args.render_triage else reply_all())
    except (ReplyThreadsError, PublishReviewError) as error:
        sys.stderr.write(f"reply_threads.py: {error}\n")
        return 1
    except subprocess.CalledProcessError as error:
        command: list[str] = error.cmd
        stderr: str = error.stderr
        sys.stderr.write(f"reply_threads.py: command failed: {' '.join(command)}\n{stderr}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
