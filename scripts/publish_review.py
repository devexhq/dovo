#!/usr/bin/env python3
"""Posts a round's findings as a pull request review, then the pull request's one dovo-review comment.

Reads .agentic/review.json and .agentic/review.md (written by the /dovo-review
skill). Each round's findings are posted as one pull request review: a finding
on a line of the pull request's diff is an inline comment there, and the rest
go in the review body. A finding whose `thread` is set continues on that
review thread with a marked reply instead, unresolving it; a finding whose
`thread` is not on the pull request is posted as new, and one naming a thread
of the pull request that is not the viewer's finding thread is refused. Every
earlier-round dovo finding thread no finding continues is resolved. A retry for
the same commit and round reuses that review and posts no reply twice.
The comment then holds review.md without its finding sections, with a findings
line linking the review, and starts with a marker line.
Every later run edits that same comment instead of adding another. The header
under the marker is stamped from review.json, never from the report's prose, and
the comment is refused unless review.json's verdict, action and blocking count
agree and the pull request head is the commit the review covered. With
--session, the header also names the dovo session that produced the review, so
pr-fix can restore that run's state. Every API call targets the pull request's
own repository.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlparse

AGENTIC_DIR = Path(".agentic")
MARKER = "<!-- dovo-review -->"
SESSION_LABEL = "Dovo session: "
MAX_COMMENT_CHARS = 65_536
STAMPED_KEYS = ("head", "verdict", "action", "round")
REQUIRED_KEYS = (*STAMPED_KEYS, "counts")
VERDICTS = ("APPROVE", "CHANGES_REQUIRED")
ACTIONS = ("none", "fix", "replan", "needs_human")
APPROVED_ACTIONS = ("none", "needs_human")
CHANGES_REQUIRED_ACTIONS = ("fix", "replan", "needs_human")
REVIEW_JSON = AGENTIC_DIR / "review.json"
REVIEW_MD = AGENTIC_DIR / "review.md"
REVIEW_MARKER = "<!-- dovo-review-findings head={head} round={round} -->"
FINDING_MARKER = "<!-- dovo-finding id={id} severity={severity} rule={rule} round={round} -->"
SEVERITY_LABELS = {  # (count of one, any other count)
    "BLOCKER": ("blocking", "blocking"),
    "WARNING": ("warning", "warnings"),
    "SUGGESTION": ("suggestion", "suggestions"),
    "NIT": ("nit", "nits"),
}
FINDING_SECTIONS = ("### Blocking", "### Warnings", "### Suggestions", "### Nits", "### Earlier rounds")
HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,\d+)? @@")
FINDINGS_FIX = "; run /dovo-review again"
FINDING_MARKER_LINE = re.compile(
    r"^<!-- dovo-finding id=(?P<id>\S+) severity=(?P<severity>BLOCKER|WARNING|SUGGESTION|NIT)"
    r" rule=(?P<rule>\S+) round=(?P<round>[1-9]\d*) -->$"
)
LONG_REVIEW_ID = "9" * 20
THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $endCursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          isResolved
          path
          line
          comments(first: 100) { nodes { author { __typename login } body url } }
        }
      }
    }
  }
}
"""
REVIEWS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviews(first: 100, after: $endCursor) {
        pageInfo { hasNextPage endCursor }
        nodes { author { __typename login } body url submittedAt lastEditedAt }
      }
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
UNRESOLVE_MUTATION = """
mutation($thread: ID!) {
  unresolveReviewThread(input: {threadId: $thread}) { thread { isResolved } }
}
"""


class PublishReviewError(RuntimeError):
    """Raised when the review cannot be published to the pull request."""


class Posting(NamedTuple):
    """Everything one post writes, built and checked before the first API write."""

    host: str
    repository: str
    number: int
    payload: dict[str, object] | None  # the review to create; None when the round needs none
    reused_url: str | None  # this round's review already on the pull request
    comment_id: int | None  # the viewer's dovo-review comment, or None to create it
    comment_for: Callable[[str | None], str]  # the comment body linking a review URL, or unlinked for None
    viewer: str  # the authenticated `gh` user's login
    continued: list[tuple[str, str, bool]]  # (thread id, continuation body, whether the thread is resolved)
    replies: dict[str, list[dict[str, object]]]  # each continued thread's comments, for already_replied
    resolve: list[str]  # earlier-round finding threads no finding continues
    detached: list[tuple[str, str]]  # (finding id, the thread it named) for each finding posted as new


def run(*args: str) -> str:
    """Run a command, raising on a non-zero exit, and return its stripped stdout.

    Args:
        args: Command and arguments to execute.

    Returns:
        The command's stdout with surrounding whitespace removed.
    """
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()


def comment_body(review: dict[str, object], report: str, *, session: str | None) -> str:
    """Return the comment: the marker, a header stamped from review.json, a blank line, then review.md.

    The header is exactly:
        <!-- dovo-review -->
        Reviewed commit: `<head>`
        Verdict: <verdict> · Action: <action> · Round: <round>
        Dovo session: `<session>`     (only when session is given)

    Args:
        review: The parsed review.json, holding every STAMPED_KEYS entry.
        report: The text of review.md.
        session: The dovo session ID to stamp, or None for a three-line header.

    Returns:
        The full comment body.
    """
    header = (
        f"{MARKER}\n"
        f"Reviewed commit: `{review['head']}`\n"
        f"Verdict: {review['verdict']} · Action: {review['action']} · Round: {review['round']}\n"
    )
    if session is not None:
        header += f"{SESSION_LABEL}`{session}`\n"
    return f"{header}\n{report}"


def find_own_comment(comments: list[dict[str, object]], viewer: str, *, marker: str = MARKER) -> int | None:
    """Return the id of the first comment whose first line is marker and whose user is viewer, else None.

    Args:
        comments: The PR's comments, each with `id`, `user` (a login) and `body`.
        viewer: The login of the authenticated `gh` user.
        marker: The comment's first line: the dovo-review marker unless another publisher passes its own.

    Returns:
        The id of the viewer's marked comment, or None when there is none.
    """
    for comment in comments:
        body = comment.get("body")
        comment_id = comment.get("id")
        if comment.get("user") != viewer or not isinstance(body, str) or not isinstance(comment_id, int):
            continue
        if body.splitlines()[:1] == [marker]:
            return comment_id
    return None


def review_marker(head: str, round_number: int) -> str:
    """Return `<!-- dovo-review-findings head=<head> round=<round_number> -->`."""
    return REVIEW_MARKER.format(head=head, round=round_number)


def finding_marker(finding: dict[str, object], round_number: int) -> str:
    """Return FINDING_MARKER for finding in round_number, writing `-` for a None rule."""
    rule = finding.get("rule")
    return FINDING_MARKER.format(
        id=finding.get("id"), severity=finding.get("severity"), rule="-" if rule is None else rule, round=round_number
    )


def parse_finding_marker(line: str) -> dict[str, object] | None:
    """Return `{id, severity, rule, round}` from a finding marker line, rule None for `-`, or None when it is not one."""
    match = FINDING_MARKER_LINE.match(line)
    if match is None:
        return None
    rule = match["rule"]
    return {
        "id": match["id"],
        "severity": match["severity"],
        "rule": None if rule == "-" else rule,
        "round": int(match["round"]),
    }


def finding_block(finding: dict[str, object], round_number: int) -> str:
    """Return the finding_marker line, the `#### <id> · `<location>` · <rule>` heading, the Issue and the Fix paragraph.

    The location is `path:line`, `path` when line is None, and left out with its separator when path is None;
    the rule part is left out when rule is None, and the marker writes `-` for it.
    """
    parts = [str(finding["id"])]
    path, line, rule = finding.get("path"), finding.get("line"), finding.get("rule")
    if path is not None:
        parts.append(f"`{path}`" if line is None else f"`{path}:{line}`")
    if rule is not None:
        parts.append(str(rule))
    return (
        f"{finding_marker(finding, round_number)}\n#### {' · '.join(parts)}\n\n"
        f"**Issue:** {finding['issue']}\n\n**Fix:** {finding['fix']}"
    )


def commentable_lines(patch: str | None) -> frozenset[int]:
    """Return the right-side line numbers of patch's hunks (context and added lines), empty when patch is None."""
    lines: set[int] = set()
    current: int | None = None
    for text in (patch or "").splitlines():
        header = HUNK_HEADER.match(text)
        if header is not None:
            current = int(header["start"])
        elif current is not None and text[:1] in ("+", " "):
            lines.add(current)
            current += 1
    return frozenset(lines)


def _is_commentable(finding: dict[str, object], lines: dict[str, frozenset[int]]) -> bool:
    """Return whether finding's line is a commentable line of its path's diff."""
    path, line = finding.get("path"), finding.get("line")
    return isinstance(path, str) and isinstance(line, int) and line in lines.get(path, frozenset())


def split_findings(
    findings: list[dict[str, object]], lines: dict[str, frozenset[int]]
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Return (inline, body): findings whose line is commentable in their path's diff, then the rest, in order."""
    inline = [finding for finding in findings if _is_commentable(finding, lines)]
    return inline, [finding for finding in findings if not _is_commentable(finding, lines)]


def _findings(review: dict[str, object]) -> list[dict[str, object]]:
    """Return review.json's findings, already accepted by findings_problem."""
    findings = review.get("findings")
    return [finding for finding in findings if isinstance(finding, dict)] if isinstance(findings, list) else []


def review_payload(review: dict[str, object], lines: dict[str, frozenset[int]]) -> dict[str, object] | None:
    """Return the `COMMENT` review on review's head for its new findings, or None when it has none to post.

    A finding whose `thread` is set continues that thread instead, and counts as inline. The body is the
    review marker, `**Findings posted: <n>** (<i> inline, <b> below)`, then each body finding's block after
    a blank line; each inline finding is `{path, line, side: "RIGHT", body: <block>}`.
    """
    findings = _findings(review)
    new = [finding for finding in findings if finding.get("thread") is None]
    if not new:
        return None
    round_number = int(str(review["round"]))
    inline, body = split_findings(new, lines)
    text = (
        f"{review_marker(str(review['head']), round_number)}\n"
        f"**Findings posted: {len(findings)}** ({len(findings) - len(body)} inline, {len(body)} below)"
    )
    text += "".join(f"\n\n{finding_block(finding, round_number)}" for finding in body)
    comments = [
        {
            "path": finding["path"],
            "line": finding["line"],
            "side": "RIGHT",
            "body": finding_block(finding, round_number),
        }
        for finding in inline
    ]
    return {"commit_id": review["head"], "event": "COMMENT", "body": text, "comments": comments}


def findings_line(findings: list[dict[str, object]], round_number: int, review_url: str | None) -> str:
    """Return the dovo-review comment's findings line, or `**Findings:** none` with no finding.

    The line is `**Findings:** <b> blocking · <w> warnings · <s> suggestions · <n> nits`, each label
    singular for a count of one (`1 warning`), then ` · [round <n> review](<review_url>)` when
    review_url is set.
    """
    if not findings:
        return "**Findings:** none"
    parts = []
    for severity, (one, other) in SEVERITY_LABELS.items():
        count = sum(finding.get("severity") == severity for finding in findings)
        parts.append(f"{count} {one if count == 1 else other}")
    if review_url is not None:
        parts.append(f"[round {round_number} review]({review_url})")
    return f"**Findings:** {' · '.join(parts)}"


def trim_report(report: str, line: str) -> str:
    """Return report without the FINDING_SECTIONS, line taking the place of the first one removed.

    Each section runs from its heading line to the next `### ` heading or the end; with none present,
    line is appended after a blank line.
    """
    kept: list[str] = []
    removing = placed = False
    for text in report.splitlines(keepends=True):
        if text.startswith("### "):
            removing = text.rstrip() in FINDING_SECTIONS
            if removing and not placed:
                kept.append(f"{line}\n\n")
                placed = True
        if not removing:
            kept.append(text)
    if not placed:
        return f"{report.rstrip()}\n\n{line}\n"
    return "".join(kept).rstrip() + "\n"


def continuation_body(finding: dict[str, object], round_number: int) -> str:
    """Return the finding's marker line, then the line `Still open in round <round_number> as <id>.`."""
    return f"{finding_marker(finding, round_number)}\nStill open in round {round_number} as {finding['id']}."


def _thread_comments(thread: dict[str, object]) -> list[dict[str, object]]:
    """Return a THREADS_QUERY node's comment nodes, in order."""
    comments = thread.get("comments")
    nodes = comments.get("nodes") if isinstance(comments, dict) else None
    return [node for node in nodes if isinstance(node, dict)] if isinstance(nodes, list) else []


def _opening_marker(thread: dict[str, object]) -> tuple[object, dict[str, object] | None]:
    """Return the login of the thread's first comment and parse_finding_marker of its first line."""
    comments = _thread_comments(thread)
    if not comments:
        return None, None
    author = comments[0].get("author")
    login = author.get("login") if isinstance(author, dict) else None
    lines = str(comments[0].get("body") or "").splitlines()
    return login, parse_finding_marker(lines[0]) if lines else None


def finding_threads(threads: list[dict[str, object]], viewer: str) -> dict[str, dict[str, object]]:
    """Return the threads whose first comment is by viewer and starts with a finding marker, by thread id."""
    found: dict[str, dict[str, object]] = {}
    for thread in threads:
        login, marker = _opening_marker(thread)
        if login == viewer and marker is not None:
            found[str(thread["id"])] = thread
    return found


def thread_problem(
    review: dict[str, object], threads: list[dict[str, object]], dovo_threads: dict[str, dict[str, object]], number: int
) -> str | None:
    """Return Error 3's message for the first finding whose thread is in threads but not in dovo_threads, or None.

    A thread not in threads at all is not refused: detach_unlisted posts that finding as new.
    """
    listed = {thread.get("id") for thread in threads}
    for finding in _findings(review):
        thread = finding.get("thread")
        if thread is not None and thread in listed and thread not in dovo_threads:
            return (
                f"review.json finding {finding['id']} names thread {thread}, "
                f"which is not a dovo finding thread on pull request #{number}"
            )
    return None


def detach_unlisted(
    review: dict[str, object], threads: list[dict[str, object]]
) -> tuple[dict[str, object], list[tuple[str, str]]]:
    """Return a copy of review with `thread` None on each finding whose thread is not in threads.

    Also returns those findings' (id, thread), in order, so each can be reported as posted new.
    """
    listed = {thread.get("id") for thread in threads}
    findings: list[dict[str, object]] = []
    detached: list[tuple[str, str]] = []
    for finding in _findings(review):
        thread = finding.get("thread")
        if thread is not None and thread not in listed:
            detached.append((str(finding["id"]), str(thread)))
            finding = {**finding, "thread": None}
        findings.append(finding)
    return {**review, "findings": findings}, detached


def threads_to_resolve(review: dict[str, object], dovo_threads: dict[str, dict[str, object]]) -> list[str]:
    """Return the unresolved dovo_threads whose first marker's round is below review's round and that no finding continues."""
    continued = {finding.get("thread") for finding in _findings(review)}
    round_number = int(str(review["round"]))
    resolve: list[str] = []
    for thread_id, thread in dovo_threads.items():
        marker = _opening_marker(thread)[1] or {}
        earlier = int(str(marker.get("round", round_number))) < round_number
        if earlier and not thread.get("isResolved") and thread_id not in continued:
            resolve.append(thread_id)
    return resolve


def _continuations(
    review: dict[str, object], dovo_threads: dict[str, dict[str, object]]
) -> list[tuple[str, str, bool]]:
    """Return (thread id, continuation body, whether resolved) for each finding of review that continues a dovo thread."""
    round_number = int(str(review["round"]))
    return [
        (thread, continuation_body(finding, round_number), bool(dovo_threads[thread].get("isResolved")))
        for finding in _findings(review)
        if isinstance(thread := finding.get("thread"), str)
    ]


def _is_count(value: object) -> bool:
    """Return whether value is a non-negative integer and not a bool."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _blocking(review: dict[str, object]) -> object:
    """Return review.json's `counts.blocking`, or None when `counts` is not an object."""
    counts = review["counts"]
    return counts.get("blocking") if isinstance(counts, dict) else None


def _shape_problem(review: dict[str, object]) -> str | None:
    """Return why review.json's round or blocking count is not a usable number, or None when both are."""
    round_number = review["round"]
    if not _is_count(round_number) or round_number == 0:
        return f"review.json round {round_number!r} is not a positive integer"
    blocking = _blocking(review)
    if not _is_count(blocking):
        return f"review.json counts.blocking {blocking!r} is not a non-negative integer"
    return None


def record_problem(review: dict[str, object]) -> str | None:
    """Return why review.json cannot stamp a truthful header, or None when it can.

    The header shows the verdict, action and round, so they must be well formed
    and agree with each other and with the blocking count, as the dovo-review
    skill defines them.

    Args:
        review: The parsed review.json.

    Returns:
        The first problem found, or None when the record is consistent.
    """
    for key in REQUIRED_KEYS:
        if key not in review:
            return f"review.json has no {key!r}; run /dovo-review again"
    verdict = review["verdict"]
    if verdict not in VERDICTS:
        return f"review.json verdict {verdict!r} is not APPROVE or CHANGES_REQUIRED"
    if review["action"] not in ACTIONS:
        return f"review.json action {review['action']!r} is not none, fix, replan or needs_human"
    problem = _shape_problem(review)
    if problem is not None:
        return problem
    approved = verdict == "APPROVE"
    blocking = _blocking(review)
    if approved != (blocking == 0):
        return f"review.json says {verdict} with {blocking} blocking findings"
    if review["action"] not in (APPROVED_ACTIONS if approved else CHANGES_REQUIRED_ACTIONS):
        return f"review.json action {review['action']!r} contradicts verdict {verdict}"
    return None


def report_problem(review: dict[str, object], report: str) -> str | None:
    """Return why review.md's own Verdict or Reviewed commit line disagrees with review.json, or None.

    The comment shows both the stamped header and review.md, so a stale or
    hand-edited review.md must not post a second, different verdict.

    Args:
        review: The parsed review.json, already accepted by record_problem.
        report: The text of review.md.

    Returns:
        The first disagreement found, or None when both lines agree.
    """
    head = review["head"]
    expected = {
        "**Verdict:**": (
            f"**Verdict:** {str(review['verdict']).replace('_', ' ')}",
            f"**Action:** {review['action']}",
        ),
        "**Reviewed commit:**": (f"`{head}`" if head is not None else "uncommitted changes",),
    }
    lines = report.splitlines()
    for label, fragments in expected.items():
        line = next((line for line in lines if line.startswith(label)), None)
        if line is None:
            return f"review.md has no {label.strip('*:')} line; run /dovo-review again"
        if not all(fragment in line for fragment in fragments):
            return f"review.md {label.strip('*:')} line disagrees with review.json: {line!r}"
    return None


def _is_text(value: object) -> bool:
    """Return whether value is a string holding more than whitespace."""
    return isinstance(value, str) and bool(value.strip())


def _finding_problem(finding: dict[str, object], round_number: int) -> str | None:
    """Return why the finding, whose id is already a usable token, cannot be posted, or None when it can."""
    finding_id = finding["id"]
    for key in ("issue", "fix"):
        if not _is_text(finding.get(key)):
            return f"review.json finding {finding_id} has no {key}{FINDINGS_FIX}"
    if "thread" not in finding or not (finding["thread"] is None or _is_text(finding["thread"])):
        return f"review.json finding {finding_id} has no thread{FINDINGS_FIX}"
    expected = {key: finding.get(key) for key in ("id", "severity", "rule")} | {"round": round_number}
    if parse_finding_marker(finding_marker(finding, round_number)) != expected:
        return f"review.json finding {finding_id} cannot be written as a finding marker{FINDINGS_FIX}"
    return None


def findings_problem(review: dict[str, object]) -> str | None:
    """Return why review.json's findings cannot be posted, or None when every one can (Error case 4).

    Each finding needs an `id` with no whitespace, non-empty `issue` and `fix`, a `thread` key holding
    null or a thread id, and an `id`, `severity` and `rule` that its finding marker parses back to.
    """
    findings = review.get("findings")
    if not isinstance(findings, list):
        return f"review.json findings is not a list{FINDINGS_FIX}"
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            return f"review.json findings[{index}] is not an object{FINDINGS_FIX}"
        finding_id = finding.get("id")
        if not isinstance(finding_id, str) or finding_id.split() != [finding_id]:
            return f"review.json findings[{index}] has no id{FINDINGS_FIX}"
        problem = _finding_problem(finding, int(str(review["round"])))
        if problem is not None:
            return problem
    return None


def size_problem(payload: dict[str, object] | None) -> str | None:
    """Return why the review body or one inline comment of payload is over MAX_COMMENT_CHARS, or None."""
    if payload is None:
        return None
    body = str(payload["body"])
    if len(body) > MAX_COMMENT_CHARS:
        return f"the review body is {len(body)} characters; GitHub accepts at most {MAX_COMMENT_CHARS}"
    comments = payload["comments"]
    for comment in comments if isinstance(comments, list) else []:
        text = str(comment["body"])
        if len(text) > MAX_COMMENT_CHARS:
            marker = parse_finding_marker(text.splitlines()[0]) or {}
            return (
                f"the inline comment for finding {marker.get('id')} is {len(text)} characters; "
                f"GitHub accepts at most {MAX_COMMENT_CHARS}"
            )
    return None


def publish_problem(review: dict[str, object], body: str, pr_head: str) -> str | None:
    """Return why body, built from review, must not be posted to a PR at pr_head, or None when it may be.

    Args:
        review: The parsed review.json.
        body: The comment body to post; an empty string skips only the size check.
        pr_head: The full SHA of the pull request's head commit.

    Returns:
        The first problem found, or None when the comment may be posted.
    """
    problem = record_problem(review) or findings_problem(review)
    if problem is not None:
        return problem
    head = review["head"]
    if head is None:
        return "the review covered uncommitted changes; commit them and review again"
    if head != pr_head:
        return f"the review covered {head}, but the pull request head is {pr_head}"
    if len(body) > MAX_COMMENT_CHARS:
        return f"the comment is {len(body)} characters; GitHub accepts at most {MAX_COMMENT_CHARS}"
    return None


def read_review() -> tuple[dict[str, object], str]:
    """Read .agentic/review.json and .agentic/review.md.

    Returns:
        The parsed review.json and the text of review.md.

    Raises:
        PublishReviewError: If either file is missing, empty or malformed.
    """
    for path in (REVIEW_JSON, REVIEW_MD):
        if not path.exists() or not path.read_text(encoding="utf-8").strip():
            raise PublishReviewError(f"Missing or empty {path}. Run /dovo-review first.")
    try:
        review = json.loads(REVIEW_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise PublishReviewError(f"{REVIEW_JSON} is not valid JSON: {error}") from error
    if not isinstance(review, dict):
        raise PublishReviewError(f"{REVIEW_JSON} must hold a JSON object.")
    return review, REVIEW_MD.read_text(encoding="utf-8")


def pr_repository(url: str) -> tuple[str, str]:
    """Return the host and `owner/repo` of the pull request at url.

    Every API call targets this repository, so the comment always lands on the
    pull request whose head was checked, even when --pr names a PR in another
    repository than the current checkout.

    Args:
        url: The pull request's web URL, such as https://github.com/owner/repo/pull/7.

    Returns:
        The host and the `owner/repo` path.

    Raises:
        PublishReviewError: If url is not a pull request URL.
    """
    parts = urlparse(url)
    segments = parts.path.strip("/").split("/")
    if not parts.hostname or len(segments) != 4 or segments[2] != "pull":
        raise PublishReviewError(f"{url!r} is not a pull request URL")
    return parts.hostname, f"{segments[0]}/{segments[1]}"


def list_comments(host: str, repository: str, number: int) -> list[dict[str, object]]:
    """Return every comment on the pull request, one dict per comment.

    Each dict has its id, body, author login, URL and creation and update times.
    """
    output = run(
        "gh",
        "api",
        "--hostname",
        host,
        "--paginate",
        f"repos/{repository}/issues/{number}/comments",
        "--jq",
        ".[] | {id, body, user: .user.login, url: .html_url, created_at, updated_at}",
    )
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def own_review(reviews: list[dict[str, object]], viewer: str, marker: str) -> dict[str, object] | None:
    """Return the first REVIEWS_QUERY node by viewer (`author.login`) whose body's first line is marker, else None."""
    for review in reviews:
        author = review.get("author")
        login = author.get("login") if isinstance(author, dict) else None
        if login == viewer and str(review.get("body") or "").splitlines()[:1] == [marker]:
            return review
    return None


def list_files(host: str, repository: str, number: int) -> dict[str, frozenset[int]]:
    """Return each changed file's commentable_lines, by path.

    Runs `gh api --hostname <host> --paginate repos/<repository>/pulls/<number>/files --jq '.[] | {filename, patch}'`.
    """
    output = run(
        "gh",
        "api",
        "--hostname",
        host,
        "--paginate",
        f"repos/{repository}/pulls/{number}/files",
        "--jq",
        ".[] | {filename, patch}",
    )
    files = [json.loads(line) for line in output.splitlines() if line.strip()]
    return {str(file["filename"]): commentable_lines(file.get("patch")) for file in files}


def write_comment(host: str, endpoint: list[str], body: str) -> str:
    """Send body to the comment endpoint (method flags and path) on host and return the comment's URL."""
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as handle:
        handle.write(body)
    body_file = Path(handle.name)
    try:
        return run("gh", "api", "--hostname", host, *endpoint, "-F", f"body=@{body_file}", "--jq", ".html_url")
    finally:
        body_file.unlink(missing_ok=True)


def create_review(host: str, repository: str, number: int, payload: dict[str, object]) -> str:
    """POST payload to repos/<repository>/pulls/<number>/reviews through `gh api --input <file>`; return its html_url."""
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as handle:
        json.dump(payload, handle)
    payload_file = Path(handle.name)
    try:
        return run(
            "gh",
            "api",
            "--hostname",
            host,
            "-X",
            "POST",
            f"repos/{repository}/pulls/{number}/reviews",
            "--input",
            str(payload_file),
            "--jq",
            ".html_url",
        )
    finally:
        payload_file.unlink(missing_ok=True)


def list_pr_nodes(host: str, repository: str, number: int, query: str, connection: str) -> list[dict[str, object]]:
    """Return every node of the pull request's GraphQL connection, one dict per node, through paginated query."""
    owner, name = repository.split("/", 1)
    output = run(
        "gh",
        "api",
        "graphql",
        "--hostname",
        host,
        "--paginate",
        "-f",
        f"query={query}",
        "-f",
        f"owner={owner}",
        "-f",
        f"name={name}",
        "-F",
        f"number={number}",
        "--jq",
        f".data.repository.pullRequest.{connection}.nodes[]",
    )
    return [json.loads(line) for line in output.splitlines() if line.strip()]


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


def thread_graphql(host: str, query: str, thread: str, *fields: str, jq: str) -> str:
    """Run `gh api graphql` on host with query, the thread variable and the extra `-f` fields, filtered by jq."""
    extra = [arg for field in fields for arg in ("-f", field)]
    return run(
        "gh", "api", "graphql", "--hostname", host, "-f", f"query={query}", "-f", f"thread={thread}", *extra, "--jq", jq
    )


def _pull_request(pr: str) -> tuple[int, str, str]:
    """Return pr's number, head commit and URL."""
    view = json.loads(run("gh", "pr", "view", pr, "--json", "number,headRefOid,url"))
    return int(view["number"]), str(view["headRefOid"]), str(view["url"])


def _raise_on(problem: str | None) -> None:
    """Raise PublishReviewError with problem unless it is None."""
    if problem is not None:
        raise PublishReviewError(problem)


def prepare(pr: str, *, session: str | None) -> Posting:
    """Read the review and the pull request, run every check, and return what to write.

    Raises:
        PublishReviewError: If the review is incomplete, inconsistent, stale or too long to post.
    """
    review, report = read_review()
    number, pr_head, pr_url = _pull_request(pr)
    host, repository = pr_repository(pr_url)
    # comment_body and the finding blocks index checked keys, so check the review before building either.
    _raise_on(publish_problem(review, "", pr_head) or report_problem(review, report))
    round_number = int(str(review["round"]))
    viewer = run("gh", "api", "--hostname", host, "user", "--jq", ".login")
    comment_id = find_own_comment(list_comments(host, repository, number), viewer)
    threads = list_pr_nodes(host, repository, number, THREADS_QUERY, "reviewThreads")
    dovo_threads = finding_threads(threads, viewer)
    _raise_on(thread_problem(review, threads, dovo_threads, number))
    review, detached = detach_unlisted(review, threads)
    continued = _continuations(review, dovo_threads)
    replies = {thread: _thread_comments(dovo_threads[thread]) for thread, _, _ in continued}
    reviews = list_pr_nodes(host, repository, number, REVIEWS_QUERY, "reviews")
    reused = own_review(reviews, viewer, review_marker(pr_head, round_number))
    reused_url = None if reused is None else str(reused.get("url"))
    payload = None if reused is not None else review_payload(review, list_files(host, repository, number))
    findings = _findings(review)

    def comment_for(url: str | None) -> str:
        return comment_body(review, trim_report(report, findings_line(findings, round_number, url)), session=session)

    longest_url = reused_url or (f"{pr_url}#pullrequestreview-{LONG_REVIEW_ID}" if payload is not None else None)
    _raise_on(publish_problem(review, comment_for(longest_url), pr_head) or size_problem(payload))
    return Posting(
        host,
        repository,
        number,
        payload,
        reused_url,
        comment_id,
        comment_for,
        viewer,
        continued,
        replies,
        threads_to_resolve(review, dovo_threads),
        detached,
    )


def apply(posting: Posting) -> str:
    """Create the review unless reused or not needed, continue and resolve threads, then write the comment.

    Returns the lines to report.
    """
    lines = ""
    url = posting.reused_url
    if url is not None:
        lines += f"Reused pull request review: {url}\n"
    elif posting.payload is not None:
        url = create_review(posting.host, posting.repository, posting.number, posting.payload)
        lines += f"Posted pull request review: {url}\n"
    for finding_id, thread in posting.detached:
        lines += f"Posted finding {finding_id} as new: thread {thread} is not on pull request #{posting.number}\n"
    replied, resolved = continue_findings(posting.host, posting.viewer, posting)
    if replied or resolved:
        lines += f"Continued {replied} finding threads and resolved {resolved}\n"
    body = posting.comment_for(url)
    if posting.comment_id is None:
        comment_url = write_comment(
            posting.host, [f"repos/{posting.repository}/issues/{posting.number}/comments"], body
        )
        return f"{lines}Posted review comment: {comment_url}\n"
    endpoint = ["-X", "PATCH", f"repos/{posting.repository}/issues/comments/{posting.comment_id}"]
    return f"{lines}Updated review comment: {write_comment(posting.host, endpoint, body)}\n"


def continue_findings(host: str, viewer: str, posting: Posting) -> tuple[int, int]:
    """Reply on and unresolve each continued thread, then resolve posting.resolve; return (replies posted, threads resolved).

    A thread that already holds the exact continuation reply by viewer gets no second one.
    """
    replied = 0
    for thread, body, is_resolved in posting.continued:
        if not already_replied(posting.replies[thread], viewer, body):
            thread_graphql(
                host, REPLY_MUTATION, thread, f"body={body}", jq=".data.addPullRequestReviewThreadReply.comment.url"
            )
            replied += 1
        if is_resolved:
            thread_graphql(host, UNRESOLVE_MUTATION, thread, jq=".data.unresolveReviewThread.thread.isResolved")
    for thread in posting.resolve:
        thread_graphql(host, RESOLVE_MUTATION, thread, jq=".data.resolveReviewThread.thread.isResolved")
    return replied, len(posting.resolve)


def publish(pr: str, *, session: str | None) -> str:
    """Post this round's pull request review and the review comment on pr, and return the lines to report.

    Raises:
        PublishReviewError: If the review is incomplete, inconsistent, stale or too long to post.
    """
    return apply(prepare(pr, session=session))


def main() -> int:
    """Publish .agentic/review.md to the pull request as its one dovo-review comment."""
    parser = argparse.ArgumentParser(description="Post or update the pull request's one dovo-review comment.")
    parser.add_argument("--pr", required=True, help="Pull request number, URL or branch.")
    parser.add_argument("--session", metavar="SESSION", help="Dovo session ID to stamp on the comment header.")
    args = parser.parse_args()
    pr: str = args.pr
    session: str | None = args.session
    if session is not None and not session.strip():
        sys.stderr.write("publish_review.py: --session must not be empty\n")
        return 1

    try:
        sys.stdout.write(publish(pr, session=session))
    except PublishReviewError as error:
        sys.stderr.write(f"publish_review.py: {error}\n")
        return 1
    except subprocess.CalledProcessError as error:
        command: list[str] = error.cmd
        stderr: str = error.stderr
        sys.stderr.write(f"publish_review.py: command failed: {' '.join(command)}\n{stderr}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
