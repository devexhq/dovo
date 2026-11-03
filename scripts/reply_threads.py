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
location, the reviewed commit on the dovo-review comment and its outcome; a
retry that finds the viewer's comment with that exact body posts none. A thread
that already holds this exact reply from the authenticated `gh` user gets no
second one, and a resolved thread is not resolved again, so a retry posts only
what is missing. Nothing is posted unless every item has a valid action and
every fix item a valid outcome.

With --render-triage it only checks .agentic/triage.json against
.agentic/feedback.json and renders .agentic/triage.md from it, one line per
item, and .agentic/triage-options.json, the same lines as the options the
person picks the items to fix from; it runs no command. With --apply-picks
<json array of ids> it rewrites .agentic/triage.json to the person's picks:
each picked item becomes a fix, each unpicked fix is deferred with
PASSED_OVER_REPLY and every other item keeps its triaged action; it then
renders both files again and runs no command.
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
    PublishReviewError,
    already_replied,
    find_own_comment,
    list_comments,
    pr_repository,
    run,
    thread_comments,
    write_comment,
)
from review_threads import list_threads, reply_on_thread, resolve_thread

TRIAGE_JSON = AGENTIC_DIR / "triage.json"
TRIAGE_MD = AGENTIC_DIR / "triage.md"
TRIAGE_OPTIONS = AGENTIC_DIR / "triage-options.json"
PASSED_OVER_REPLY = "Not picked for this round of fixes."
NO_FEEDBACK_LINE = "No open feedback."
OUTCOMES_JSON = AGENTIC_DIR / "feedback-outcomes.json"
REPLY_MARKER = "<!-- dovo-pr-fix -->"
OUTCOMES_MARKER = "<!-- dovo-pr-fix-outcomes -->"
REVIEWED_COMMIT_LABEL = "Reviewed commit: "  # the dovo-review comment header line publish_review.comment_body writes
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


class ReplyThreadsError(RuntimeError):
    """Raised when the outcomes cannot be posted on the pull request's review threads."""


class Outcome(NamedTuple):
    """One triaged item's outcome, a key of OUTCOME_LINES, and the note posted with it."""

    outcome: str
    note: str


class ThreadReply(NamedTuple):
    """One triaged review thread to reply on: its feedback id, thread id (its first comment's REST id), first comment URL, outcome and note."""

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


def _triage_lines(items: list[dict[str, object]], triage: dict[str, object]) -> list[tuple[str, str]]:
    """Return per item in items order its id and `<label> → <action>: <reply, or reason for fix>`."""
    entries = _entries(triage)
    lines = []
    for item in items:
        entry = entries[str(item["id"])]
        lines.append((str(item["id"]), f"{item.get('label')} → {entry['action']}: {_triage_text(entry)}"))
    return lines


def triage_markdown(items: list[dict[str, object]], triage: dict[str, object]) -> str:
    """Return triage.md: one _triage_lines line per item, or NO_FEEDBACK_LINE with no item."""
    if not items:
        return f"{NO_FEEDBACK_LINE}\n"
    return "".join(f"{line}\n" for _, line in _triage_lines(items, triage))


def triage_options(items: list[dict[str, object]], triage: dict[str, object]) -> dict[str, object]:
    """Return triage-options.json: an `items` list of `{id, label}`, the label being the item's triage.md line."""
    return {"items": [{"id": item_id, "label": line} for item_id, line in _triage_lines(items, triage)]}


def _write_triage_views(items: list[dict[str, object]], triage: dict[str, object]) -> None:
    """Write TRIAGE_MD and TRIAGE_OPTIONS from the checked triage."""
    TRIAGE_MD.write_text(triage_markdown(items, triage), encoding="utf-8")
    TRIAGE_OPTIONS.write_text(json.dumps(triage_options(items, triage), indent=2) + "\n", encoding="utf-8")


def render_triage() -> str:
    """Check triage.json against feedback.json, write TRIAGE_MD and TRIAGE_OPTIONS and return the line to report.

    Runs no command.

    Raises:
        ReplyThreadsError: For errors 1-2 of issue #1259, before any file is written.
    """
    _, items, triage = _read_triage()
    _write_triage_views(items, triage)
    return f"Rendered {len(items)} triage entries to {TRIAGE_MD}\n"


def _picked_ids(picked: str, item_ids: list[str]) -> list[str]:
    """Return the ids in picked, a JSON array of feedback ids.

    Raises:
        ReplyThreadsError: When picked is not a JSON array of strings, or names an id not in item_ids.
    """
    try:
        ids = json.loads(picked)
    except json.JSONDecodeError as error:
        raise ReplyThreadsError(f"the picked items {picked!r} are not a JSON array") from error
    if not isinstance(ids, list) or not all(isinstance(item_id, str) for item_id in ids):
        raise ReplyThreadsError(f"the picked items {picked!r} are not a JSON array of ids")
    unknown = [item_id for item_id in ids if item_id not in item_ids]
    if unknown:
        raise ReplyThreadsError(f"picked item {unknown[0]} names no feedback item")
    return ids


def picked_entry(entry: dict[str, object], *, picked: bool) -> dict[str, object]:
    """Return entry as the person's pick settles it.

    A picked entry is a fix, keeping a fix's reason or giving the triaged action and reply as the reason.
    An unpicked fix is deferred with PASSED_OVER_REPLY. Any other entry is returned unchanged.
    """
    action = entry["action"]
    if picked and action != "fix":
        reason = f"The person picked it to fix over the triaged {action}: {_triage_text(entry)}"
        return {"id": entry["id"], "action": "fix", "reason": reason}
    if not picked and action == "fix":
        return {"id": entry["id"], "action": "defer", "reply": PASSED_OVER_REPLY}
    return entry


def apply_picks(picked: str) -> str:
    """Rewrite triage.json to the picked items, write TRIAGE_MD and TRIAGE_OPTIONS and return the line to report.

    Runs no command.

    Raises:
        ReplyThreadsError: For an invalid triage or picked list, before any file is written.
    """
    _, items, triage = _read_triage()
    ids = _picked_ids(picked, [str(item["id"]) for item in items])
    entries = _entries(triage)
    settled: dict[str, object] = {
        "items": [picked_entry(entries[str(item["id"])], picked=item["id"] in ids) for item in items]
    }
    TRIAGE_JSON.write_text(json.dumps(settled, indent=2) + "\n", encoding="utf-8")
    _write_triage_views(items, settled)
    return f"Picked {len(ids)} of {len(items)} triage entries to fix in {TRIAGE_JSON}\n"


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


def reviewed_commit(body: str) -> str | None:
    """Return the sha on the header's ``Reviewed commit: `<sha>` `` line, or None; only lines before the first blank line are read."""
    for line in body.splitlines():
        if not line.strip():
            return None
        if line.startswith(REVIEWED_COMMIT_LABEL):
            return line.removeprefix(REVIEWED_COMMIT_LABEL).strip().strip("`") or None
    return None


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
        ReplyThreadsError: For errors 1-4 of issue #1259, or a thread item naming no review thread.
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
    url = PR_URL.read_text(encoding="utf-8").splitlines()[0].strip()
    return Inputs(url, int(str(feedback.get("pr"))), replies, findings)


class _Pull(NamedTuple):
    """The pull request a reply step posts on: its host, `owner/repo`, number and the viewer's login."""

    host: str
    repository: str
    number: int
    viewer: str


def post_reply(pull: _Pull, reply: ThreadReply, thread: dict[str, object] | None, commit: str) -> tuple[bool, bool]:
    """Reply on thread unless already_replied, then resolve it when its outcome is in RESOLVED_OUTCOMES and it is unresolved; return (replied, resolved).

    thread is reply.thread's list_threads node, read before any reply of this step was posted.

    Raises:
        ReplyThreadsError: When the thread is gone or a gh call fails, with reply.url and gh's stderr.
    """
    if thread is None:
        raise ReplyThreadsError(f"cannot update review thread {reply.url}: it is no longer on the pull request")
    try:
        body = reply_body(Outcome(reply.outcome, reply.note), commit)
        replied = not already_replied(thread_comments(thread), pull.viewer, body)
        if replied:
            reply_on_thread(pull.host, pull.repository, pull.number, reply.thread, body)
        resolved = reply.outcome in RESOLVED_OUTCOMES and not thread.get("isResolved")
        if resolved:
            resolve_thread(pull.host, pull.repository, pull.number, reply.thread)
    except subprocess.CalledProcessError as error:
        stderr = str(error.stderr or "")
        raise ReplyThreadsError(f"cannot update review thread {reply.url}: {stderr.strip()}") from error
    except LookupError as error:
        raise ReplyThreadsError(f"cannot update review thread {reply.url}: {error}") from error
    return replied, resolved


def post_replies(pull: _Pull, replies: list[ThreadReply], commit: str) -> tuple[int, int]:
    """Post each of replies with post_reply, in order, on threads listed once before the first; return how many were replied on and resolved.

    With no reply, nothing is listed.
    """
    if not replies:
        return 0, 0
    threads = {str(thread["id"]): thread for thread in list_threads(pull.host, pull.repository, pull.number)}
    replied = resolved = 0
    for reply in replies:
        did_reply, did_resolve = post_reply(pull, reply, threads.get(reply.thread), commit)
        replied += did_reply
        resolved += did_resolve
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
        ReplyThreadsError: When the dovo-review comment names no reviewed commit, or the comment is too long.
    """
    comment_id = find_own_comment(comments, viewer)
    review = next((comment for comment in comments if comment_id is not None and comment.get("id") == comment_id), None)
    reviewed = reviewed_commit(str(review.get("body") or "")) if review is not None else None
    if reviewed is None:
        raise ReplyThreadsError(f"the dovo-review comment on pull request #{inputs.number} names no reviewed commit")
    body = outcome_comment(inputs.findings, reviewed[:REVIEWED_COMMIT_CHARS], commit)
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
        replied, resolved = post_replies(_Pull(host, repository, inputs.number, viewer), inputs.replies, commit)
        if comment is not None:
            body, posted = comment
            commented = int(post_comment(host, repository, inputs.number, inputs.url, body, posted=posted))
    return (
        f"Replied on {replied} review threads, resolved {resolved} "
        f"and posted {commented} outcome comment for {inputs.url}\n"
    )


def main() -> int:
    """Reply on and resolve the review threads pr-fix triaged, or only render the triage or apply the picks to it."""
    parser = argparse.ArgumentParser(
        description=(
            "Reply on and resolve the review threads pr-fix triaged, and post the outcomes of its out-of-diff "
            "findings; with --render-triage, only check the triage and render it for the person to pick from; "
            "with --apply-picks, only settle the triage to the picked items."
        )
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--render-triage",
        action="store_true",
        help="check .agentic/triage.json and render .agentic/triage.md and triage-options.json, posting nothing",
    )
    modes.add_argument(
        "--apply-picks",
        metavar="IDS",
        help="JSON array of the feedback ids the person picked to fix; rewrites .agentic/triage.json, posting nothing",
    )
    args = parser.parse_args()

    try:
        if args.render_triage:
            report = render_triage()
        elif args.apply_picks is not None:
            report = apply_picks(args.apply_picks)
        else:
            report = reply_all()
        sys.stdout.write(report)
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
