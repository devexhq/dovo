#!/usr/bin/env python3
"""Collects a pull request's open feedback from GitHub.

Needs no dovo session and downloads nothing: the pull request need not carry a
dovo-review comment. Copies this checkout's pr-fix scripts and dovo-code rules
into .agentic/pr-fix/, checks out the pull request's head commit, records its
head branch in .agentic/pr-branch and its URL in .agentic/pr-url, writes the
title and body of each issue it closes, or its own when it closes none, to
.agentic/pr-context.md, and writes .agentic/feedback.json: every unresolved review thread, with its thread id;
every finding in the body of the authenticated `gh` user's latest submitted
`🤖 **Agentic Review**`, on any commit; every comment and review summary by
someone else; and every failed check on the head commit. Items for dovo
findings, the review's body findings and the viewer's finding threads, carry
`finding`: the id, severity and rule of the finding marker on their last line.
Every item carries `commit`: the reviewed commit on the review's body findings,
null on every other item.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from itertools import takewhile
from pathlib import Path
from typing import NamedTuple

from publish_review import (
    REVIEWS_QUERY,
    THREADS_QUERY,
    latest_review,
    list_comments,
    list_pr_nodes,
    parse_finding_marker,
    pr_repository,
    run,
    thread_comments,
)

AGENTIC_DIR = Path(".agentic")
FEEDBACK_JSON = AGENTIC_DIR / "feedback.json"
TRIAGE_JSON = AGENTIC_DIR / "triage.json"
PR_BRANCH = AGENTIC_DIR / "pr-branch"
PR_URL = AGENTIC_DIR / "pr-url"
PR_CONTEXT = AGENTIC_DIR / "pr-context.md"
PR_FIX_DIR = AGENTIC_DIR / "pr-fix"
BASE_COPIES = {  # source, relative to the repository root this script is in -> name in PR_FIX_DIR
    "scripts/pr_feedback.py": "pr_feedback.py",
    "scripts/reply_threads.py": "reply_threads.py",
    "scripts/publish_review.py": "publish_review.py",
    ".agents/skills/dovo-code/SKILL.md": "dovo-code.md",
}
SUMMARY_CHARS = 200
NO_AUTHOR_LABELS = {"review_finding": "dovo-review", "check": "ci"}
FAILED_CONCLUSIONS = ("failure", "cancelled", "timed_out")
BACKTICKED = re.compile(r"`([^`]+)`")
LINE_RANGE = re.compile(r"^(?P<path>.+):(?:\d+-)?(?P<end>\d+)$")
ISSUE_LABEL = "**Issue:**"
BUG_LABEL = "**Bug:**"
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
HTML_TAG = re.compile(r"</?[A-Za-z][^>]*>")
LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
LINE_MARKERS = re.compile(r"^\s*(?:(?:#{1,6}|>|[-*+]|\d+[.)])\s+)*")
INLINE_MARKUP = re.compile(r"`|\*\*|__|~~")
EDGE_EMPHASIS = re.compile(r"(?<!\w)[*_]+(?=\S)|(?<=\S)[*_]+(?!\w)")


class PrFeedbackError(RuntimeError):
    """Raised when the pull request's feedback cannot be collected."""


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


def _last_line(text: str) -> str:
    """Return the last non-blank line of text, or an empty string when it has none."""
    return next((line for line in reversed(text.splitlines()) if line.strip()), "")


def finding_blocks(body: str) -> list[str]:
    """Return each finding block of body: from a `#### ` heading line through the next finding marker line.

    A `#### ` line inside a block is the finding's own text, not a new heading.
    """
    blocks: list[str] = []
    current: list[str] | None = None
    for line in body.splitlines():
        if current is None and line.startswith("#### "):
            current = []
        if current is None:
            continue
        current.append(line)
        if parse_finding_marker(line) is not None:
            blocks.append("\n".join(current))
            current = None
    return blocks


def labelled_text(block: str, labels: tuple[str, ...], *, paragraph: bool) -> str:
    """Return the text after the first line starting with one of labels, through its paragraph when paragraph is set, markup removed and cut to SUMMARY_CHARS."""
    lines = block.splitlines()
    start = next((index for index, line in enumerate(lines) if line.startswith(labels)), None)
    if start is None:
        return ""
    label = next(label for label in labels if lines[start].startswith(label))
    text = [lines[start].removeprefix(label)]
    if paragraph:
        text += takewhile(str.strip, lines[start + 1 :])
    return _plain_line(" ".join(text))[:SUMMARY_CHARS]


def block_location(block: str) -> tuple[str | None, int | None]:
    """Return the path and line in the block heading's backticked location, (path, None) without a line, (None, None) without one.

    A `path:<start>-<end>` range gives (path, end).
    """
    heading = next((line for line in block.splitlines() if line.startswith("#### ")), "")
    location = BACKTICKED.search(heading)
    if location is None:
        return None, None
    ranged = LINE_RANGE.match(location[1])
    return (ranged["path"], int(ranged["end"])) if ranged is not None else (location[1], None)


def _author_login(author: object) -> str:
    """Return the GraphQL author's login, with `[bot]` added for a Bot, or `ghost` when GitHub returns none."""
    if not isinstance(author, dict) or not author.get("login"):
        return "ghost"
    login = str(author["login"])
    return f"{login}[bot]" if author.get("__typename") == "Bot" else login


def thread_finding(comments: list[dict[str, object]], viewer: str) -> dict[str, object] | None:
    """Return parse_finding_marker of the last non-blank line of the thread's first comment when viewer wrote it, else None."""
    if not comments or _author_login(comments[0].get("author")) != viewer:
        return None
    return parse_finding_marker(_last_line(str(comments[0].get("body") or "")))


def finding_items(review: dict[str, object] | None) -> list[dict[str, object]]:
    """Return one `review_finding` item per finding block in the dovo review node's body, ids R1, R2, ...

    Each has `thread` and `author` None, `url` the review's URL, path and line from block_location,
    `summary` from its `**Issue:**` paragraph, `body` the whole block, `finding` from its marker and
    `commit` the review's `commit.oid`. None gives [].
    """
    if review is None:
        return []
    reviewed = review.get("commit")
    commit = reviewed.get("oid") if isinstance(reviewed, dict) else None
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
                "summary": labelled_text(block, (ISSUE_LABEL,), paragraph=True),
                "body": block,
                "finding": parse_finding_marker(_last_line(block)),
                "commit": commit,
            }
        )
    return items


def thread_items(threads: list[dict[str, object]], viewer: str) -> list[dict[str, object]]:
    """Return one `review_thread` item per unresolved thread, ids T1, T2, ..., with `finding` from thread_finding.

    Each comes from the thread's first comment; one whose last line is a finding marker, whoever wrote it,
    takes `summary` from its `**Bug:**` or `**Issue:**` line when it has one.
    """
    items: list[dict[str, object]] = []
    for thread in threads:
        comments = thread_comments(thread)
        if thread.get("isResolved") or not comments:
            continue
        comment = comments[0]
        body = str(comment.get("body") or "")
        is_block = parse_finding_marker(_last_line(body)) is not None
        label_line = labelled_text(body, (BUG_LABEL, ISSUE_LABEL), paragraph=False) if is_block else ""
        items.append(
            {
                "id": f"T{len(items) + 1}",
                "source": "review_thread",
                "thread": thread.get("id"),
                "author": _author_login(comment.get("author")),
                "url": comment.get("url"),
                "path": thread.get("path"),
                "line": thread.get("line"),
                "summary": label_line or markdown_summary(body),
                "body": body,
                "finding": thread_finding(comments, viewer),
                "commit": None,
            }
        )
    return items


class _Entry(NamedTuple):
    """An issue comment or submitted review, normalised: its author label, URL, body and creation time."""

    author: str
    url: object
    body: str
    created: datetime


def _issue_comment_entry(comment: dict[str, object]) -> _Entry:
    """Return a list_comments entry normalised, its REST login taken as is or `ghost` when GitHub returns none."""
    user = comment.get("user")
    return _Entry(
        author=str(user) if user else "ghost",
        url=comment.get("url"),
        body=str(comment.get("body") or ""),
        created=datetime.fromisoformat(str(comment["created_at"])),
    )


def _review_entry(review: dict[str, object]) -> _Entry | None:
    """Return a REVIEWS_QUERY node normalised, created at its submission; None when pending."""
    submitted = review.get("submittedAt")
    if not submitted:
        return None
    return _Entry(
        author=_author_login(review.get("author")),
        url=review.get("url"),
        body=str(review.get("body") or ""),
        created=datetime.fromisoformat(str(submitted)),
    )


def _after(entry: _Entry, viewer: str) -> bool:
    """Return whether entry is by someone other than viewer and non-empty once markup is removed."""
    return entry.author != viewer and markdown_summary(entry.body) != ""


def comment_items(
    comments: list[dict[str, object]], reviews: list[dict[str, object]], viewer: str
) -> list[dict[str, object]]:
    """Return one `comment` item per comment and review by someone else, ids C1, C2, ...

    Each issue comment and submitted review not by viewer and non-empty once markup is removed is listed in
    creation order. comments are list_comments entries (REST: `user`, `url`, `body`, `created_at`, `updated_at`); reviews are
    REVIEWS_QUERY nodes (GraphQL: `author`, `url`, `body`, `submittedAt`, `lastEditedAt`). A comment is created at
    `created_at`; a review is created at `submittedAt`.
    """
    entries = [_issue_comment_entry(comment) for comment in comments] + [
        entry for entry in map(_review_entry, reviews) if entry is not None
    ]
    selected = sorted((entry for entry in entries if _after(entry, viewer)), key=lambda entry: entry.created)
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
            "commit": None,
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
            "commit": None,
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


def feedback_document(pr: int, head: str, items: list[dict[str, object]]) -> dict[str, object]:
    """Return the feedback.json object: pr, head and the items, each with its `label` added."""
    return {
        "pr": pr,
        "head": head,
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
    """Return pr's `gh pr view` fields: its state, head, URL, title, body, author and closing issue references.

    The fields are number, state, headRefName, headRefOid, url, isCrossRepository, title, body,
    author and closingIssuesReferences.

    Raises:
        PrFeedbackError: With error 2 when the pull request does not exist or is not open, or
            when its head is in another repository.
    """
    fields = "number,state,headRefName,headRefOid,url,isCrossRepository,title,body,author,closingIssuesReferences"
    try:
        view = json.loads(run("gh", "pr", "view", str(pr), "--json", fields))
    except subprocess.CalledProcessError as error:
        raise PrFeedbackError(f"pull request #{pr} is not open") from error
    problem = pr_problem(view, pr)
    if problem is not None:
        raise PrFeedbackError(problem)
    return view


def check_out_head(number: int, head_branch: str, head: str, url: str) -> None:
    """Detach the worktree at the pull request's head commit, and record its head branch and URL.

    The head branch in .agentic/pr-branch is where the fixes are pushed; the URL
    in .agentic/pr-url names the one pull request the review is posted to.
    """
    run("git", "fetch", "-q", "origin", f"refs/pull/{number}/head")
    run("git", "switch", "-q", "--detach", head)
    AGENTIC_DIR.mkdir(exist_ok=True)
    PR_BRANCH.write_text(f"{head_branch}\n", encoding="utf-8")
    PR_URL.write_text(f"{url}\n", encoding="utf-8")


def copy_base() -> None:
    """Replace PR_FIX_DIR with a copy of each BASE_COPIES source, taken from this script's checkout."""
    root = Path(__file__).resolve().parent.parent
    shutil.rmtree(PR_FIX_DIR, ignore_errors=True)
    PR_FIX_DIR.mkdir(parents=True)
    for source, name in BASE_COPIES.items():
        shutil.copyfile(root / source, PR_FIX_DIR / name)


def _context_part(kind: str, entry: dict[str, object]) -> str:
    """Return one pr-context.md part: the `## <kind>: <title>` heading, its author and URL, and its body when it has one."""
    part = f"## {kind}: {entry.get('title')}\nAuthor: {_author_login(entry.get('author'))} · {entry.get('url')}\n"
    body = str(entry.get("body") or "").strip()
    return f"{part}\n{body}\n" if body else part


def pr_context(view: dict[str, object], issues: list[dict[str, object]]) -> str:
    """Return pr-context.md: one part per closing issue in issues, or the pull request's own part when there is none."""
    parts = [_context_part("Issue", issue) for issue in issues] or [_context_part("Pull request", view)]
    return f"# Context for pull request #{view.get('number')}\n\n" + "\n".join(parts)


def write_pr_context(view: dict[str, object]) -> None:
    """Write PR_CONTEXT from each closing issue `gh issue view` can read, skipping one it cannot."""
    references = view.get("closingIssuesReferences")
    issues: list[dict[str, object]] = []
    for reference in references if isinstance(references, list) else []:
        try:
            issues.append(
                json.loads(run("gh", "issue", "view", str(reference["url"]), "--json", "title,body,author,url"))
            )
        except subprocess.CalledProcessError:
            continue
    PR_CONTEXT.write_text(pr_context(view, issues), encoding="utf-8")


def collect(pr: int) -> str:
    """Copy the base's pr-fix files, check out pr's head and write pr-context.md and feedback.json; return the line to report.

    An earlier attempt's triage.json is removed first, so render-triage never shows its decisions for approval.
    The copies are taken after the pull request check and before `git switch`, so a refused pull request
    leaves no .agentic/pr-fix/ and the copies come from the run's base, not the head.
    The `R#` items come from the viewer's latest Agentic Review on any commit, whatever the dovo-review comment says.

    Raises:
        PrFeedbackError: For error 2 of issue #1124.
        subprocess.CalledProcessError: When a gh request other than reading a closing issue fails, including
            reading the head commit's checks.
    """
    TRIAGE_JSON.unlink(missing_ok=True)
    view = open_pull_request(pr)
    copy_base()
    number = int(str(view["number"]))
    head = str(view["headRefOid"])
    url = str(view["url"])
    host, repository = pr_repository(url)
    viewer = run("gh", "api", "--hostname", host, "user", "--jq", ".login")
    comments = list_comments(host, repository, number)
    check_out_head(number, str(view["headRefName"]), head, url)
    write_pr_context(view)
    reviews = list_pr_nodes(host, repository, number, REVIEWS_QUERY, "reviews")
    items = (
        thread_items(list_pr_nodes(host, repository, number, THREADS_QUERY, "reviewThreads"), viewer)
        + finding_items(latest_review(reviews, viewer, None))
        + comment_items(comments, reviews, viewer)
        + check_items(list_checks(host, repository, head))
    )
    document = feedback_document(number, head, items)
    FEEDBACK_JSON.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return f"Collected {len(items)} feedback items for pull request #{number}\n"


def main() -> int:
    """Collect the pull request's feedback and write .agentic/feedback.json."""
    parser = argparse.ArgumentParser(description="Collect a pull request's open feedback.")
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
