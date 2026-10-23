#!/usr/bin/env python3
"""Replies on the review threads a pr-fix round addressed, and resolves the fixed ones.

Reads the selected feedback ids from --selected, each selected item from
.agentic/feedback.json and its outcome from .agentic/feedback-outcomes.json
(written by /dovo-code feedback). Every selected review thread gets one reply,
headed by a `<!-- dovo-pr-fix round <n> -->` marker, saying whether it was fixed
(in the commit at HEAD), disputed or deferred, with the fixer's note; fixed
threads are then resolved. A thread that already holds this exact reply (marker
and outcome line) from the authenticated `gh` user gets no second one, so a
retry posts only what is missing, while a reused round number with a changed
outcome still gets its reply. Nothing is posted unless every selected id has a valid outcome.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import NamedTuple

from pr_feedback import AGENTIC_DIR, FEEDBACK_JSON, PR_URL
from publish_review import PublishReviewError, pr_repository, run

OUTCOMES_JSON = AGENTIC_DIR / "feedback-outcomes.json"
OUTCOME_LINES = {
    "fixed": "Fixed in {commit}: {note}",
    "disputed": "Not changed: {note}",
    "deferred": "Deferred: {note}",
}
NO_THREADS = "No review threads selected\n"
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
REPLY_MUTATION = """
mutation($thread: ID!, $body: String!) {
  addPullRequestReviewThreadReply(input: {pullRequestReviewThreadId: $thread, body: $body}) { comment { url } }
}
"""
RESOLVE_MUTATION = """
mutation($thread: ID!) {
  resolveReviewThread(input: {threadId: $thread}) { thread { isResolved } }
}
"""


class ReplyThreadsError(RuntimeError):
    """Raised when the outcomes cannot be posted on the pull request's review threads."""


class ThreadReply(NamedTuple):
    """One selected review thread to reply on: its feedback id, thread node id, first comment URL, outcome and note."""

    item_id: str
    thread: str
    url: str
    outcome: str
    note: str


def reply_marker(round_number: int) -> str:
    """Return `<!-- dovo-pr-fix round <round_number> -->`."""
    return f"<!-- dovo-pr-fix round {round_number} -->"


def reply_body(round_number: int, outcome: str, note: str, commit: str) -> str:
    """Return the marker line, then the OUTCOME_LINES line for outcome with note's whitespace collapsed to single spaces."""
    line = OUTCOME_LINES[outcome].format(commit=commit, note=" ".join(note.split()))
    return f"{reply_marker(round_number)}\n{line}"


def _is_round(value: object) -> bool:
    """Return whether value is a positive integer and not a bool."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _entries(outcomes: dict[str, object]) -> dict[str, dict[str, object]]:
    """Return outcomes' `items` entries by their string `id`, the first entry winning for a repeated id."""
    items = outcomes.get("items")
    entries: dict[str, dict[str, object]] = {}
    for entry in items if isinstance(items, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            entries.setdefault(str(entry["id"]), entry)
    return entries


def _entry_problem(item_id: str, entry: dict[str, object]) -> str | None:
    """Return why entry's outcome or note is unusable, or None when both are."""
    outcome = entry.get("outcome")
    if outcome not in OUTCOME_LINES:
        return f"{OUTCOMES_JSON} entry {item_id} has outcome {outcome!r}, not fixed, disputed or deferred"
    note = entry.get("note")
    if not isinstance(note, str) or not note.strip():
        return f"{OUTCOMES_JSON} entry {item_id} has no note"
    return None


def outcomes_problem(outcomes: object, selected: list[str]) -> str | None:
    """Return why outcomes cannot answer every selected id, or None when it can.

    outcomes must hold a positive integer `round` (not a bool, as
    publish_review._is_count checks) and an `items` list whose
    entries each have an `id`, an `outcome` in OUTCOME_LINES and a non-empty
    string `note`; every selected id must have an entry. Entries for ids
    that were not selected are ignored.
    """
    if not isinstance(outcomes, dict):
        return f"{OUTCOMES_JSON} has no outcome for {', '.join(selected)}"
    if not _is_round(outcomes.get("round")):
        return f"{OUTCOMES_JSON} round {outcomes.get('round')!r} is not a positive integer"
    entries = _entries(outcomes)
    missing = [item_id for item_id in selected if item_id not in entries]
    if missing:
        return f"{OUTCOMES_JSON} has no outcome for {', '.join(missing)}"
    problems = (_entry_problem(item_id, entries[item_id]) for item_id in selected)
    return next((problem for problem in problems if problem is not None), None)


def already_replied(comments: list[dict[str, object]], viewer: str, body: str) -> bool:
    """Return whether a comment by viewer (GraphQL `author.login`) has body as its text, ignoring surrounding whitespace.

    Matching the whole reply, not only its round marker, keeps a round number
    reused by a later pr-fix run from hiding a changed outcome.
    """
    for comment in comments:
        author = comment.get("author")
        login = author.get("login") if isinstance(author, dict) else None
        if login == viewer and str(comment.get("body") or "").strip() == body:
            return True
    return False


def _feedback_items(feedback: dict[str, object]) -> dict[str, dict[str, object]]:
    """Return feedback.json's items by id."""
    items = feedback.get("items")
    return {str(item["id"]): item for item in (items if isinstance(items, list) else []) if isinstance(item, dict)}


def _selected_threads(feedback: dict[str, object], selected: list[str]) -> list[dict[str, object]]:
    """Return the selected `review_thread` items of feedback, in selection order."""
    items = _feedback_items(feedback)
    return [
        items[item_id] for item_id in selected if item_id in items and items[item_id].get("source") == "review_thread"
    ]


def thread_replies(feedback: dict[str, object], outcomes: dict[str, object], selected: list[str]) -> list[ThreadReply]:
    """Return one ThreadReply per selected `review_thread` item of feedback, in selection order.

    Raises:
        ReplyThreadsError: When a selected thread item names no review thread.
    """
    entries = _entries(outcomes)
    replies: list[ThreadReply] = []
    for item in _selected_threads(feedback, selected):
        item_id = str(item["id"])
        thread = item.get("thread")
        if not isinstance(thread, str) or not thread:
            raise ReplyThreadsError(f"{FEEDBACK_JSON} item {item_id} names no review thread")
        entry = entries[item_id]
        replies.append(ThreadReply(item_id, thread, str(item.get("url")), str(entry["outcome"]), str(entry["note"])))
    return replies


def read_outcomes(selected: list[str]) -> dict[str, object]:
    """Read OUTCOMES_JSON and return it.

    Raises:
        ReplyThreadsError: When the file is missing or not JSON, naming every selected id,
            or when outcomes_problem reports a problem.
    """
    try:
        outcomes = json.loads(OUTCOMES_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReplyThreadsError(
            f"{OUTCOMES_JSON} is missing or not JSON; no outcome for {', '.join(selected)}"
        ) from error
    problem = outcomes_problem(outcomes, selected)
    if problem is not None:
        raise ReplyThreadsError(problem)
    return outcomes


def _graphql(host: str, query: str, thread: str, *fields: str, jq: str) -> str:
    """Run `gh api graphql` on host with query, the thread variable and the extra `-f` fields, filtered by jq."""
    extra = [arg for field in fields for arg in ("-f", field)]
    return run(
        "gh", "api", "graphql", "--hostname", host, "-f", f"query={query}", "-f", f"thread={thread}", *extra, "--jq", jq
    )


def post_outcome(host: str, viewer: str, reply: ThreadReply, round_number: int, commit: str) -> tuple[bool, bool]:
    """Reply on reply.thread unless already_replied, then resolve it when fixed and unresolved; return (replied, resolved).

    Raises:
        ReplyThreadsError: When a gh call fails, with reply.url and gh's stderr.
    """
    try:
        state = json.loads(_graphql(host, REPLIES_QUERY, reply.thread, jq=".data.node"))
        body = reply_body(round_number, reply.outcome, reply.note, commit)
        replied = not already_replied(state["comments"]["nodes"], viewer, body)
        if replied:
            _graphql(
                host,
                REPLY_MUTATION,
                reply.thread,
                f"body={body}",
                jq=".data.addPullRequestReviewThreadReply.comment.url",
            )
        resolved = reply.outcome == "fixed" and not state["isResolved"]
        if resolved:
            _graphql(host, RESOLVE_MUTATION, reply.thread, jq=".data.resolveReviewThread.thread.isResolved")
    except subprocess.CalledProcessError as error:
        stderr = str(error.stderr or "")
        raise ReplyThreadsError(f"cannot update review thread {reply.url}: {stderr.strip()}") from error
    return replied, resolved


def reply_all(selected: list[str]) -> str:
    """Post every selected thread's outcome and return the line to report.

    An empty selection reads no file; a selection with no review_thread item
    reads only FEEDBACK_JSON. Either posts nothing.

    Raises:
        ReplyThreadsError: For errors 1-2 of issue #1126.
        PublishReviewError: When .agentic/pr-url names no pull request.
    """
    if not selected:
        return NO_THREADS
    feedback = json.loads(FEEDBACK_JSON.read_text(encoding="utf-8"))
    if not _selected_threads(feedback, selected):
        return NO_THREADS
    outcomes = read_outcomes(selected)
    replies = thread_replies(feedback, outcomes, selected)
    url = PR_URL.read_text(encoding="utf-8").splitlines()[0].strip()
    host, _ = pr_repository(url)
    viewer = run("gh", "api", "--hostname", host, "user", "--jq", ".login")
    commit = run("git", "rev-parse", "--short", "HEAD")
    round_number = int(str(outcomes["round"]))
    replied = resolved = 0
    for reply in replies:
        did_reply, did_resolve = post_outcome(host, viewer, reply, round_number, commit)
        replied += did_reply
        resolved += did_resolve
    return f"Replied on {replied} review threads and resolved {resolved} for {url}\n"


def _selection(value: str) -> list[str] | None:
    """Return value parsed as a JSON array of strings, or None when it is not one."""
    try:
        selected = json.loads(value)
    except json.JSONDecodeError:
        return None
    if not isinstance(selected, list) or not all(isinstance(item_id, str) for item_id in selected):
        return None
    return selected


def main() -> int:
    """Reply on and resolve the review threads this pr-fix round addressed."""
    parser = argparse.ArgumentParser(description="Reply on and resolve the review threads a pr-fix round addressed.")
    parser.add_argument("--selected", required=True, help="JSON array of the feedback ids the round selected.")
    args = parser.parse_args()
    selected = _selection(args.selected)
    if selected is None:
        sys.stderr.write("reply_threads.py: --selected must be a JSON array of feedback ids\n")
        return 1

    try:
        sys.stdout.write(reply_all(selected))
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
