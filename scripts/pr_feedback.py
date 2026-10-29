#!/usr/bin/env python3
"""Restores a pull request's last dovo run state and collects its open feedback.

Reads the pull request's dovo-review comment by the authenticated `gh` user,
takes the dovo session on its `Dovo session:` header line, downloads that
session's plan, plan-questions, review and implementation-report artifacts into
.agentic/, checks out the pull request's head commit, records its head branch
in .agentic/pr-branch and its URL in .agentic/pr-url, and writes
.agentic/feedback.json: every unresolved review thread, with its thread id;
every finding in the body of the viewer's pull request review for the round
the dovo-review comment names; every comment and review summary by someone
else touched since the dovo-review comment's last update; and every failed
check on the head commit. Items for dovo findings, the review's body findings
and the viewer's finding threads, carry `finding`: the id, severity, rule and
round of the finding marker that starts them.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from publish_review import (
    REVIEWS_QUERY,
    SESSION_LABEL,
    THREADS_QUERY,
    find_own_comment,
    list_comments,
    list_pr_nodes,
    own_review,
    parse_finding_marker,
    pr_repository,
    review_marker,
    run,
)

AGENTIC_DIR = Path(".agentic")
FEEDBACK_JSON = AGENTIC_DIR / "feedback.json"
PR_BRANCH = AGENTIC_DIR / "pr-branch"
PR_URL = AGENTIC_DIR / "pr-url"
RESTORED_ARTIFACTS = ("plan", "plan-questions", "review", "implementation-report")
SUMMARY_CHARS = 200
NO_AUTHOR_LABELS = {"review_finding": "dovo-review", "check": "ci"}
FAILED_CONCLUSIONS = ("failure", "cancelled", "timed_out")
HEADER_COMMIT = re.compile(r"^Reviewed commit: `(?P<head>[0-9a-f]{40})`$")
HEADER_ROUND = re.compile(r" · Round: (?P<round>[1-9]\d*)$")
BACKTICKED = re.compile(r"`([^`]+)`")
ISSUE_LABEL = "**Issue:**"
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
HTML_TAG = re.compile(r"</?[A-Za-z][^>]*>")
LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
LINE_MARKERS = re.compile(r"^\s*(?:(?:#{1,6}|>|[-*+]|\d+[.)])\s+)*")
INLINE_MARKUP = re.compile(r"`|\*\*|__|~~")
EDGE_EMPHASIS = re.compile(r"(?<!\w)[*_]+(?=\S)|(?<=\S)[*_]+(?!\w)")


class PrFeedbackError(RuntimeError):
    """Raised when the pull request's dovo state cannot be restored or its feedback collected."""


def pr_problem(view: dict[str, object], number: int) -> str | None:
    """Return why pr-fix cannot work on the pull request in view, or None when it can.

    The pull request must be open, and its head must live in the base
    repository, because pr-fix fetches and pushes only through origin.
    """
    if view.get("state") != "OPEN":
        return f"pull request #{number} is not open"
    if view.get("isCrossRepository"):
        return f"pull request #{number} comes from another repository; pr-fix pushes only to origin"
    return None


def session_from_comment(body: str) -> str | None:
    """Return the ID on the header's ``Dovo session: `<id>` `` line, or None when the header has none.

    The header is the lines before the first blank line, so text in the review
    report below it is never read as a session.
    """
    for line in body.splitlines():
        if not line.strip():
            return None
        if line.startswith(SESSION_LABEL):
            session = line.removeprefix(SESSION_LABEL).strip().strip("`")
            return session or None
    return None


def review_comment(comments: list[dict[str, object]], viewer: str, number: int) -> dict[str, object]:
    """Return viewer's dovo-review comment among comments.

    Raises:
        PrFeedbackError: With error 3 when there is no such comment.
    """
    comment_id = find_own_comment(comments, viewer)
    comment = next((comment for comment in comments if comment.get("id") == comment_id), None)
    if comment_id is None or comment is None:
        raise PrFeedbackError(
            f"no dovo-review comment on pull request #{number}; run /dovo-review {number} --post first"
        )
    return comment


def comment_session(comment: dict[str, object], number: int) -> str:
    """Return the session named by the dovo-review comment's header.

    Raises:
        PrFeedbackError: With error 4 when it names none.
    """
    session = session_from_comment(str(comment.get("body", "")))
    if session is None:
        raise PrFeedbackError(f"the dovo-review comment on pull request #{number} names no dovo session")
    return session


def header_review(body: str) -> tuple[str, int] | None:
    """Return the Reviewed commit and Round on the dovo-review comment's header, or None when it names either not.

    The header is the lines before the first blank line, as session_from_comment reads it.
    """
    head = round_number = None
    for line in body.splitlines():
        if not line.strip():
            break
        commit = HEADER_COMMIT.match(line)
        found = HEADER_ROUND.search(line)
        head = commit["head"] if commit is not None else head
        round_number = int(found["round"]) if found is not None else round_number
    return None if head is None or round_number is None else (head, round_number)


def artifact_problem(session: str, name: str) -> str:
    """Return the message for an artifact that could not be downloaded from session."""
    return (
        f"cannot restore artifact '{name}' from dovo session {session}; "
        f"check it with dovo artifacts list --session {session}"
    )


def _plain_line(line: str) -> str:
    """Return line with HTML tags, link targets, block markers and inline emphasis removed, whitespace collapsed."""
    line = HTML_TAG.sub("", line)
    line = LINK.sub(r"\1", line)
    line = LINE_MARKERS.sub("", line)
    line = INLINE_MARKUP.sub("", line)
    line = EDGE_EMPHASIS.sub("", line)
    return " ".join(line.split())


def markdown_summary(body: str) -> str:
    """Return the first line of body that is non-empty once Markdown markup is removed, cut to SUMMARY_CHARS."""
    for line in HTML_COMMENT.sub("", body).splitlines():
        plain = _plain_line(line)
        if plain:
            return plain[:SUMMARY_CHARS]
    return ""


def finding_blocks(body: str) -> list[str]:
    """Return each finding block of body: from a finding marker line to before the next one, trailing space stripped."""
    blocks: list[list[str]] = []
    for line in body.splitlines():
        if parse_finding_marker(line) is not None:
            blocks.append([])
        if blocks:
            blocks[-1].append(line)
    return ["\n".join(block).rstrip() for block in blocks]


def block_summary(block: str) -> str:
    """Return the block's `**Issue:**` paragraph with markup removed, cut to SUMMARY_CHARS."""
    paragraph: list[str] = []
    for line in block.splitlines():
        if paragraph and not line.strip():
            break
        if paragraph or line.startswith(ISSUE_LABEL):
            paragraph.append(line.removeprefix(ISSUE_LABEL))
    return _plain_line(" ".join(paragraph))[:SUMMARY_CHARS]


def block_location(block: str) -> tuple[str | None, int | None]:
    """Return the path and line in the block heading's backticked location, (path, None) without a line, (None, None) without one."""
    heading = next((line for line in block.splitlines() if line.startswith("#### ")), "")
    location = BACKTICKED.search(heading)
    if location is None:
        return None, None
    path, _, line = location[1].rpartition(":")
    return (path, int(line)) if path and line.isdigit() else (location[1], None)


def _author_login(author: object) -> str:
    """Return the GraphQL author's login, with `[bot]` added for a Bot, or `ghost` when GitHub returns none."""
    if not isinstance(author, dict) or not author.get("login"):
        return "ghost"
    login = str(author["login"])
    return f"{login}[bot]" if author.get("__typename") == "Bot" else login


def _comments(thread: dict[str, object]) -> list[dict[str, object]]:
    """Return the thread's comment nodes, in order."""
    comments = thread.get("comments")
    nodes = comments.get("nodes") if isinstance(comments, dict) else None
    return [node for node in nodes if isinstance(node, dict)] if isinstance(nodes, list) else []


def last_finding(comments: list[dict[str, object]], viewer: str) -> dict[str, object] | None:
    """Return parse_finding_marker of the last comment by viewer whose first line is a finding marker, or None."""
    found = None
    for comment in comments:
        lines = str(comment.get("body") or "").splitlines()
        marker = parse_finding_marker(lines[0]) if lines else None
        if marker is not None and _author_login(comment.get("author")) == viewer:
            found = marker
    return found


def finding_items(review: dict[str, object] | None) -> list[dict[str, object]]:
    """Return one `review_finding` item per finding block in the dovo review node's body, ids R1, R2, ...

    Each has `thread` and `author` None, `url` the review's URL, path and line from block_location,
    `summary` from block_summary, `body` the whole block and `finding` from its marker. None gives [].
    """
    if review is None:
        return []
    items: list[dict[str, object]] = []
    for index, block in enumerate(finding_blocks(str(review.get("body") or "")), start=1):
        path, line = block_location(block)
        items.append(
            {
                "id": f"R{index}",
                "source": "review_finding",
                "thread": None,
                "author": None,
                "url": review.get("url"),
                "path": path,
                "line": line,
                "summary": block_summary(block),
                "body": block,
                "finding": parse_finding_marker(block.splitlines()[0]),
            }
        )
    return items


def thread_items(threads: list[dict[str, object]], viewer: str) -> list[dict[str, object]]:
    """Return one `review_thread` item per unresolved thread, ids T1, T2, ..., with `finding` from last_finding.

    Each comes from the thread's first comment; one that is a finding block takes `summary` from its Issue.
    """
    items: list[dict[str, object]] = []
    for thread in threads:
        comments = _comments(thread)
        if thread.get("isResolved") or not comments:
            continue
        comment = comments[0]
        body = str(comment.get("body") or "")
        is_block = parse_finding_marker(body.splitlines()[0] if body else "") is not None
        items.append(
            {
                "id": f"T{len(items) + 1}",
                "source": "review_thread",
                "thread": thread.get("id"),
                "author": _author_login(comment.get("author")),
                "url": comment.get("url"),
                "path": thread.get("path"),
                "line": thread.get("line"),
                "summary": block_summary(body) if is_block else markdown_summary(body),
                "body": body,
                "finding": last_finding(comments, viewer),
            }
        )
    return items


class _Entry(NamedTuple):
    """An issue comment or submitted review, normalised: its author label, URL, body and creation and touch times."""

    author: str
    url: object
    body: str
    created: datetime
    touched: datetime


def _issue_comment_entry(comment: dict[str, object]) -> _Entry:
    """Return a list_comments entry normalised, its REST login taken as is or `ghost` when GitHub returns none."""
    user = comment.get("user")
    return _Entry(
        author=str(user) if user else "ghost",
        url=comment.get("url"),
        body=str(comment.get("body") or ""),
        created=datetime.fromisoformat(str(comment["created_at"])),
        touched=datetime.fromisoformat(str(comment["updated_at"])),
    )


def _review_entry(review: dict[str, object]) -> _Entry | None:
    """Return a REVIEWS_QUERY node normalised, touched at the later of submission and last edit; None when pending."""
    submitted = review.get("submittedAt")
    if not submitted:
        return None
    created = datetime.fromisoformat(str(submitted))
    edited = review.get("lastEditedAt")
    touched = max(created, datetime.fromisoformat(str(edited))) if edited else created
    return _Entry(
        author=_author_login(review.get("author")),
        url=review.get("url"),
        body=str(review.get("body") or ""),
        created=created,
        touched=touched,
    )


def _after(entry: _Entry, viewer: str, since: datetime) -> bool:
    """Return whether entry is by someone other than viewer, touched after since and non-empty once markup is removed."""
    return entry.author != viewer and entry.touched > since and markdown_summary(entry.body) != ""


def comment_items(
    comments: list[dict[str, object]], reviews: list[dict[str, object]], viewer: str, since: str
) -> list[dict[str, object]]:
    """Return one `comment` item per comment and review by someone else touched after since, ids C1, C2, ...

    Each issue comment and submitted review not by viewer, touched after since and non-empty once markup is
    removed, is listed in creation order. comments are list_comments entries (REST: `user`, `url`, `body`, `created_at`, `updated_at`); reviews are
    REVIEWS_QUERY nodes (GraphQL: `author`, `url`, `body`, `submittedAt`, `lastEditedAt`). A comment is created at
    `created_at` and touched at `updated_at`; a review is created at `submittedAt` and touched at the later of
    `submittedAt` and `lastEditedAt`. since is the dovo-review comment's `updated_at`.
    """
    cutoff = datetime.fromisoformat(since)
    entries = [_issue_comment_entry(comment) for comment in comments] + [
        entry for entry in map(_review_entry, reviews) if entry is not None
    ]
    selected = sorted((entry for entry in entries if _after(entry, viewer, cutoff)), key=lambda entry: entry.created)
    return [
        {
            "id": f"C{index}",
            "source": "comment",
            "thread": None,
            "author": entry.author,
            "url": entry.url,
            "path": None,
            "line": None,
            "summary": markdown_summary(entry.body),
            "body": entry.body,
            "finding": None,
        }
        for index, entry in enumerate(selected, start=1)
    ]


def check_items(checks: list[dict[str, object]]) -> list[dict[str, object]]:
    """Return one `check` item per check run whose conclusion is in FAILED_CONCLUSIONS, ordered by name, ids K1, K2, ..."""
    failed = sorted(
        (check for check in checks if check.get("conclusion") in FAILED_CONCLUSIONS),
        key=lambda check: str(check.get("name")),
    )
    return [
        {
            "id": f"K{index}",
            "source": "check",
            "thread": None,
            "author": None,
            "url": check.get("details_url") or check.get("html_url"),
            "path": None,
            "line": None,
            "summary": f"{check.get('name')}: {check.get('conclusion')}",
            "body": f"{check.get('name')}: {check.get('conclusion')}",
            "finding": None,
        }
        for index, check in enumerate(failed, start=1)
    ]


def option_label(item: dict[str, object]) -> str:
    """Return `<id> [<source>, <author>] <summary>`, with the source's NO_AUTHOR_LABELS entry for a null author.

    A dovo finding adds `, <finding id> <severity>` inside the brackets.
    """
    author = item["author"] if item["author"] is not None else NO_AUTHOR_LABELS[str(item["source"])]
    finding = item.get("finding")
    tag = f", {finding['id']} {finding['severity']}" if isinstance(finding, dict) else ""
    return f"{item['id']} [{item['source']}, {author}{tag}] {item['summary']}"


def feedback_document(pr: int, head: str, session: str, items: list[dict[str, object]]) -> dict[str, object]:
    """Return the feedback.json object: pr, head, session and the items, each with its `label` added."""
    return {
        "pr": pr,
        "head": head,
        "session": session,
        "items": [{**item, "label": option_label(item)} for item in items],
    }


def list_checks(host: str, repository: str, head: str) -> list[dict[str, object]]:
    """Return the latest check run per name on commit head, each with name, conclusion, details_url and html_url.

    Runs `gh api --hostname <host> --paginate repos/<repository>/commits/<head>/check-runs
    --jq '.check_runs[] | {name, conclusion, details_url, html_url}'`.
    """
    output = run(
        "gh",
        "api",
        "--hostname",
        host,
        "--paginate",
        f"repos/{repository}/commits/{head}/check-runs",
        "--jq",
        ".check_runs[] | {name, conclusion, details_url, html_url}",
    )
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def open_pull_request(pr: int) -> dict[str, object]:
    """Return pr's number, state, head branch, head commit, URL and whether its head is in another repository.

    Raises:
        PrFeedbackError: With error 2 when the pull request does not exist or is not open, or
            when its head is in another repository.
    """
    fields = "number,state,headRefName,headRefOid,url,isCrossRepository"
    try:
        view = json.loads(run("gh", "pr", "view", str(pr), "--json", fields))
    except subprocess.CalledProcessError as error:
        raise PrFeedbackError(f"pull request #{pr} is not open") from error
    problem = pr_problem(view, pr)
    if problem is not None:
        raise PrFeedbackError(problem)
    return view


def download_artifacts(session: str) -> None:
    """Download every RESTORED_ARTIFACTS entry of session into the working tree.

    Raises:
        PrFeedbackError: With error 5 when an artifact cannot be downloaded.
    """
    for name in RESTORED_ARTIFACTS:
        try:
            run("uv", "run", "dovo", "artifacts", "download", session, name, "--dest", ".")
        except subprocess.CalledProcessError as error:
            raise PrFeedbackError(artifact_problem(session, name)) from error


def check_out_head(number: int, head_branch: str, head: str, url: str) -> None:
    """Detach the worktree at the pull request's head commit, and record its head branch and URL.

    The head branch in .agentic/pr-branch is where the fixes are pushed; the URL
    in .agentic/pr-url names the one pull request the review is posted to.
    """
    run("git", "fetch", "-q", "origin", f"refs/pull/{number}/head")
    run("git", "switch", "-q", "--detach", head)
    PR_BRANCH.write_text(f"{head_branch}\n", encoding="utf-8")
    PR_URL.write_text(f"{url}\n", encoding="utf-8")


def collect(pr: int) -> str:
    """Restore pr's dovo state into .agentic/, check out its head and write feedback.json; return the line to report.

    Raises:
        PrFeedbackError: For errors 2-5 of issue #1124.
        subprocess.CalledProcessError: When a gh request fails, including reading the head commit's checks.
    """
    view = open_pull_request(pr)
    number = int(str(view["number"]))
    head = str(view["headRefOid"])
    url = str(view["url"])
    host, repository = pr_repository(url)
    viewer = run("gh", "api", "--hostname", host, "user", "--jq", ".login")
    comments = list_comments(host, repository, number)
    own = review_comment(comments, viewer, number)
    session = comment_session(own, number)
    download_artifacts(session)
    check_out_head(number, str(view["headRefName"]), head, url)
    reviews = list_pr_nodes(host, repository, number, REVIEWS_QUERY, "reviews")
    # The header names the reviewed commit, which differs from head once the pull request is pushed to.
    header = header_review(str(own.get("body") or ""))
    dovo_review = None if header is None else own_review(reviews, viewer, review_marker(*header))
    items = (
        thread_items(list_pr_nodes(host, repository, number, THREADS_QUERY, "reviewThreads"), viewer)
        + finding_items(dovo_review)
        + comment_items(comments, reviews, viewer, str(own["updated_at"]))
        + check_items(list_checks(host, repository, head))
    )
    document = feedback_document(number, head, session, items)
    FEEDBACK_JSON.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return f"Restored dovo session {session} and collected {len(items)} feedback items for pull request #{number}\n"


def main() -> int:
    """Restore the pull request's dovo state and write .agentic/feedback.json."""
    parser = argparse.ArgumentParser(description="Restore a pull request's dovo state and collect its feedback.")
    parser.add_argument("--pr", type=int, required=True, help="Number of the pull request to address feedback on.")
    args = parser.parse_args()
    pr: int = args.pr

    try:
        sys.stdout.write(collect(pr))
    except PrFeedbackError as error:
        sys.stderr.write(f"pr_feedback.py: {error}\n")
        return 1
    except subprocess.CalledProcessError as error:
        command: list[str] = error.cmd
        stderr: str = error.stderr
        sys.stderr.write(f"pr_feedback.py: command failed: {' '.join(command)}\n{stderr}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
