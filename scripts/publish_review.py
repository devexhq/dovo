#!/usr/bin/env python3
"""Posts a round's findings as a pull request review, then the pull request's one dovo-review comment.

Reads .agentic/review.json and .agentic/review.md (written by the /dovo-review
skill). Each round's findings are posted as one pull request review: a finding
whose line is on the pull request's diff is an inline comment, on its whole
line range when one hunk holds it, with a suggested fix and a prompt for an AI
agent; the rest go in the review body under an outside-diff block. The post reads, replies on and resolves no review
thread. A retry reuses the viewer's latest `🤖 **Agentic Review**` review on
the reviewed commit only when its body and inline comment bodies equal what
this run would post; otherwise a new review is posted on that commit.
The comment then holds review.md without its finding sections, with a findings
line linking the review as `[review](<URL>)`, and starts with a marker line.
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
import html
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from datetime import datetime
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
REVIEW_HEADING = "🤖 **Agentic Review**"
FINDING_MARKER = "<!-- dovo-finding id={id} severity={severity} rule={rule} -->"
SEVERITY_LABELS = {  # (count of one, any other count)
    "BLOCKER": ("blocking", "blocking"),
    "WARNING": ("warning", "warnings"),
    "SUGGESTION": ("suggestion", "suggestions"),
    "NIT": ("nit", "nits"),
}
FINDING_SECTIONS = ("### Blocking", "### Warnings", "### Suggestions", "### Nits", "### Earlier rounds")
HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,\d+)? @@")
FINDINGS_FIX = "; run /dovo-review again"
FINDING_KEYS = ("severity", "path", "start_line", "line", "rule", "summary", "issue", "fix", "also_affects")
FINDING_MARKER_LINE = re.compile(
    r"^<!-- dovo-finding id=(?P<id>\S+) severity=(?P<severity>BLOCKER|WARNING|SUGGESTION|NIT) rule=(?P<rule>\S+) -->$"
)
ALSO_AFFECTS_ENTRY = re.compile(r"^\S+:[1-9]\d*(?:-[1-9]\d*)?$")
BACKTICK_RUN = re.compile(r"`+")
SEVERITY_NAMES = {"BLOCKER": "Blocker", "WARNING": "Warning", "SUGGESTION": "Suggestion", "NIT": "Nit"}
SEVERITY_DOTS = {"BLOCKER": "🔴", "WARNING": "🟠", "SUGGESTION": "🔵", "NIT": "⚪"}
BUG_SEVERITIES = ("BLOCKER", "WARNING")
PROMPT = (
    "Review the code at the location below. A potential issue has been identified by an AI agent.\n"
    "Verify if this is a real issue. If it is, propose a fix; if not, explain why it's not valid.\n"
    "\n"
    "Location: {location}\n"
    "\n"
    "Potential issue: {issue}"
)
OUTSIDE_DIFF_CAUTION = (
    "> [!CAUTION]\n> Some comments are outside the diff and can't be posted inline due to GitHub limitations."
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
        nodes {
          author { __typename login }
          body
          url
          submittedAt
          lastEditedAt
          commit { oid }
          comments(first: 100) { nodes { body } }
        }
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


class PublishReviewError(RuntimeError):
    """Raised when the review cannot be published to the pull request."""


class Posting(NamedTuple):
    """Everything one post writes, built and checked before the first API write."""

    host: str
    repository: str
    number: int
    payload: dict[str, object] | None  # the review to create; None when there is no finding or one is reused
    reused_url: str | None  # the identical review already on the pull request
    comment_id: int | None  # the viewer's dovo-review comment, or None to create it
    comment_for: Callable[[str | None], str]  # the comment body linking a review URL, or unlinked for None


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


def finding_marker(finding: dict[str, object]) -> str:
    """Return FINDING_MARKER for finding, writing `-` for a None rule."""
    rule = finding.get("rule")
    return FINDING_MARKER.format(
        id=finding.get("id"), severity=finding.get("severity"), rule="-" if rule is None else rule
    )


def parse_finding_marker(line: str) -> dict[str, object] | None:
    """Return `{id, severity, rule}` from a finding marker line, rule None for `-`, or None when it is not one."""
    match = FINDING_MARKER_LINE.match(line)
    if match is None:
        return None
    rule = match["rule"]
    return {"id": match["id"], "severity": match["severity"], "rule": None if rule == "-" else rule}


def finding_block(finding: dict[str, object]) -> str:
    """Return the `#### <id> · `<location>` · <rule>` heading, the Issue and the Fix paragraph, then the finding_marker line after a blank line.

    The location is finding_location's, and is left out with its separator when path is None; the rule part
    is left out when rule is None, and the marker writes `-` for it.
    """
    parts = [str(finding["id"])]
    location, rule = finding_location(finding), finding.get("rule")
    if location is not None:
        parts.append(f"`{location}`")
    if rule is not None:
        parts.append(str(rule))
    return (
        f"#### {' · '.join(parts)}\n\n"
        f"**Issue:** {finding['issue']}\n\n**Fix:** {finding['fix']}\n\n{finding_marker(finding)}"
    )


def finding_location(finding: dict[str, object]) -> str | None:
    """Return `path:line`, `path:<start_line>-<line>` for a range, `path` without a line, or None without a path."""
    path, start, line = finding.get("path"), finding.get("start_line"), finding.get("line")
    if path is None:
        return None
    if line is None:
        return str(path)
    return f"{path}:{line}" if start is None else f"{path}:{start}-{line}"


def agent_prompt(finding: dict[str, object]) -> str:
    """Return PROMPT for finding, its Location `<path>#L<start>-L<line>` (start being start_line, or line when null), or `<path>` when line is null."""
    path, start, line = finding.get("path"), finding.get("start_line"), finding.get("line")
    location = str(path) if line is None else f"{path}#L{line if start is None else start}-L{line}"
    return PROMPT.format(location=location, issue=finding["issue"])


def _fence(text: str) -> str:
    """Return a backtick fence one longer than the longest backtick run in text, and at least three long (OQ-2)."""
    longest = max((len(run) for run in BACKTICK_RUN.findall(text)), default=0)
    return "`" * max(3, longest + 1)


def inline_body(finding: dict[str, object]) -> str:
    """Return #1267's inline finding: heading, Bug or Issue label, severity, Suggested fix, the fenced agent_prompt, Also affects when set, then the marker."""
    severity = str(finding["severity"])
    label = "**Bug:**" if severity in BUG_SEVERITIES else "**Issue:**"
    prompt = agent_prompt(finding)
    fence = _fence(prompt)
    also_affects = finding.get("also_affects")
    entries = [f"`{entry}`" for entry in also_affects] if isinstance(also_affects, list) else []
    also = f"**Also affects:** {', '.join(entries)}\n" if entries else ""
    return (
        f"{REVIEW_HEADING}\n\n{label} {finding['summary']}\n**Severity:** {SEVERITY_NAMES[severity]}\n\n"
        f"<details><summary><b>Suggested fix</b></summary>\n\n{finding['fix']}\n</details>\n\n"
        f"<details><summary><b>Prompt for an AI agent</b></summary>\n\n{fence}text\n{prompt}\n{fence}\n</details>\n\n"
        f"{also}{finding_marker(finding)}"
    )


def outside_entry(finding: dict[str, object]) -> str:
    """Return one collapsed out-of-diff entry: the `<dot> <b><Severity></b> · <summary> · <code><location></code>` summary line, then finding_block in a blockquote.

    The summary and location are html.escape(..., quote=False)d in the summary line only.
    """
    severity = str(finding["severity"])
    parts = [
        f"{SEVERITY_DOTS[severity]} <b>{SEVERITY_NAMES[severity]}</b>",
        html.escape(str(finding["summary"]), quote=False),
    ]
    location = finding_location(finding)
    if location is not None:
        parts.append(f"<code>{html.escape(location, quote=False)}</code>")
    return (
        f"<details>\n<summary>{' · '.join(parts)}</summary><blockquote>\n\n"
        f"{finding_block(finding)}\n\n</blockquote></details>"
    )


def review_body(findings: list[dict[str, object]]) -> str:
    """Return REVIEW_HEADING alone with no finding, else the heading, OUTSIDE_DIFF_CAUTION and the outside-diff block of each outside_entry."""
    if not findings:
        return REVIEW_HEADING
    entries = "".join(f"{outside_entry(finding)}\n\n" for finding in findings)
    return (
        f"{REVIEW_HEADING}\n\n{OUTSIDE_DIFF_CAUTION}\n\n<details>\n"
        f"<summary>⚠️ <b>Outside diff range comments ({len(findings)})</b></summary><blockquote>\n\n"
        f"{entries}</blockquote></details>"
    )


def commentable_hunks(patch: str | None) -> tuple[frozenset[int], ...]:
    """Return each hunk's right-side line numbers (context and added lines), in order, empty when patch is None."""
    hunks: list[set[int]] = []
    current = 0
    for text in (patch or "").split("\n"):
        header = HUNK_HEADER.match(text)
        if header is not None:
            current = int(header["start"])
            hunks.append(set())
        elif hunks and text[:1] in ("+", " "):
            hunks[-1].add(current)
            current += 1
    return tuple(frozenset(hunk) for hunk in hunks)


def inline_comment(finding: dict[str, object], hunks: tuple[frozenset[int], ...]) -> dict[str, object] | None:
    """Return finding's review comment when its line is in one of hunks: multi-line when one hunk holds start_line through line, else single-line on line; None otherwise."""
    path, start, line = finding.get("path"), finding.get("start_line"), finding.get("line")
    if not isinstance(line, int):
        return None
    holding = next((hunk for hunk in hunks if line in hunk), None)
    if holding is None:
        return None
    comment: dict[str, object] = {"path": path, "line": line, "side": "RIGHT", "body": inline_body(finding)}
    if isinstance(start, int) and set(range(start, line + 1)) <= holding:
        return {"path": path, "start_line": start, "start_side": "RIGHT", **comment}
    return comment


def _findings(review: dict[str, object]) -> list[dict[str, object]]:
    """Return review.json's findings, already accepted by findings_problem."""
    findings = review.get("findings")
    return [finding for finding in findings if isinstance(finding, dict)] if isinstance(findings, list) else []


def review_payload(review: dict[str, object], hunks: dict[str, tuple[frozenset[int], ...]]) -> dict[str, object] | None:
    """Return the `COMMENT` review on review's head for its findings, or None when it has none.

    Each finding is the inline_comment its path's hunks give it, or else an entry of the review_body.
    """
    findings = _findings(review)
    if not findings:
        return None
    comments: list[dict[str, object]] = []
    outside: list[dict[str, object]] = []
    for finding in findings:
        path = finding.get("path")
        comment = inline_comment(finding, hunks.get(path, ()) if isinstance(path, str) else ())
        if comment is None:
            outside.append(finding)
        else:
            comments.append(comment)
    return {"commit_id": review["head"], "event": "COMMENT", "body": review_body(outside), "comments": comments}


def findings_line(findings: list[dict[str, object]], review_url: str | None) -> str:
    """Return the dovo-review comment's findings line, or `**Findings:** none` with no finding.

    The line is `**Findings:** <b> blocking · <w> warnings · <s> suggestions · <n> nits`, each label
    singular for a count of one (`1 warning`), then ` · [review](<review_url>)` when review_url is set.
    """
    if not findings:
        return "**Findings:** none"
    parts = []
    for severity, (one, other) in SEVERITY_LABELS.items():
        count = sum(finding.get("severity") == severity for finding in findings)
        parts.append(f"{count} {one if count == 1 else other}")
    if review_url is not None:
        parts.append(f"[review]({review_url})")
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


def thread_comments(thread: dict[str, object]) -> list[dict[str, object]]:
    """Return a THREADS_QUERY or REVIEWS_QUERY node's comment nodes, in order."""
    comments = thread.get("comments")
    nodes = comments.get("nodes") if isinstance(comments, dict) else None
    return [node for node in nodes if isinstance(node, dict)] if isinstance(nodes, list) else []


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


def _range_problem(finding: dict[str, object]) -> str | None:
    """Return Error 4's message when start_line is set but not an integer from 1 to below an integer line; None otherwise."""
    finding_id = finding["id"]
    start, line = finding["start_line"], finding.get("line")
    if start is None or (_is_count(start) and _is_count(line) and 0 < int(str(start)) < int(str(line))):
        return None
    return (
        f"review.json finding {finding_id} has start_line {start!r}, which is not a line before its line{FINDINGS_FIX}"
    )


def _also_affects_problem(finding: dict[str, object]) -> str | None:
    """Return Error 8's message when also_affects is missing or not a list, or holds an entry ALSO_AFFECTS_ENTRY does not match."""
    finding_id = finding["id"]
    entries = finding.get("also_affects")
    if not isinstance(entries, list):
        return f"review.json finding {finding_id} has no also_affects{FINDINGS_FIX}"
    for entry in entries:
        if not isinstance(entry, str) or ALSO_AFFECTS_ENTRY.match(entry) is None:
            return (
                f"review.json finding {finding_id} has also_affects entry {entry!r}, "
                f"which is not path:line or path:start-end{FINDINGS_FIX}"
            )
    return None


def _finding_problem(finding: dict[str, object]) -> str | None:
    """Return why the finding, whose id is already a usable token, cannot be posted, or None when it can."""
    finding_id = finding["id"]
    for key in FINDING_KEYS:
        if key not in finding:
            return f"review.json finding {finding_id} has no {key}{FINDINGS_FIX}"
    for key in ("summary", "issue", "fix"):
        if not _is_text(finding.get(key)):
            return f"review.json finding {finding_id} has no {key}{FINDINGS_FIX}"
    problem = _range_problem(finding) or _also_affects_problem(finding)
    if problem is not None:
        return problem
    expected = {key: finding.get(key) for key in ("id", "severity", "rule")}
    if parse_finding_marker(finding_marker(finding)) != expected:
        return f"review.json finding {finding_id} cannot be written as a finding marker{FINDINGS_FIX}"
    return None


def findings_problem(review: dict[str, object]) -> str | None:
    """Return why review.json's findings cannot be posted, or None when every one can (Error case 4).

    Each finding needs an `id` with no whitespace, every key in FINDING_KEYS, non-empty `summary`, `issue`
    and `fix`, a `start_line` that is null or a line before `line`, an `also_affects` list of `path:line` or
    `path:start-end` entries, and an `id`, `severity` and `rule` that its finding marker parses back to.
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
        problem = _finding_problem(finding)
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
            marker = parse_finding_marker(text.splitlines()[-1]) or {}
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


def _is_heading_review(review: dict[str, object], viewer: str, commit: str) -> bool:
    """Return whether the REVIEWS_QUERY node is viewer's (`author.login`), submitted, on commit and headed REVIEW_HEADING."""
    author = review.get("author")
    reviewed = review.get("commit")
    return (
        (author.get("login") if isinstance(author, dict) else None) == viewer
        and (reviewed.get("oid") if isinstance(reviewed, dict) else None) == commit
        and bool(review.get("submittedAt"))
        and str(review.get("body") or "").splitlines()[:1] == [REVIEW_HEADING]
    )


def latest_review(reviews: list[dict[str, object]], viewer: str, commit: str) -> dict[str, object] | None:
    """Return viewer's latest submitted REVIEWS_QUERY node, by submittedAt, on commit whose body's first line is REVIEW_HEADING, else None."""
    candidates = [review for review in reviews if _is_heading_review(review, viewer, commit)]
    if not candidates:
        return None
    return max(candidates, key=lambda review: datetime.fromisoformat(str(review["submittedAt"])))


def matching_review(
    reviews: list[dict[str, object]], viewer: str, payload: dict[str, object]
) -> dict[str, object] | None:
    """Return latest_review on payload's commit_id when its body and inline comment bodies equal payload's, else None.

    Bodies are compared without surrounding whitespace, and inline comment bodies as sorted lists.
    """
    latest = latest_review(reviews, viewer, str(payload["commit_id"]))
    if latest is None:
        return None
    comments = payload["comments"]
    expected = sorted(str(comment["body"]).strip() for comment in comments) if isinstance(comments, list) else []
    posted = sorted(str(node.get("body") or "").strip() for node in thread_comments(latest))
    same_body = str(latest.get("body") or "").strip() == str(payload["body"]).strip()
    return latest if same_body and posted == expected else None


def list_files(host: str, repository: str, number: int) -> dict[str, tuple[frozenset[int], ...]]:
    """Return each changed file's commentable_hunks, by path.

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
    return {str(file["filename"]): commentable_hunks(file.get("patch")) for file in files}


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
    viewer = run("gh", "api", "--hostname", host, "user", "--jq", ".login")
    comment_id = find_own_comment(list_comments(host, repository, number), viewer)
    reviews = list_pr_nodes(host, repository, number, REVIEWS_QUERY, "reviews")
    payload = review_payload(review, list_files(host, repository, number))
    reused = None if payload is None else matching_review(reviews, viewer, payload)
    reused_url = None if reused is None else str(reused.get("url"))
    payload = None if reused is not None else payload
    findings = _findings(review)

    def comment_for(url: str | None) -> str:
        return comment_body(review, trim_report(report, findings_line(findings, url)), session=session)

    longest_url = reused_url or (f"{pr_url}#pullrequestreview-{LONG_REVIEW_ID}" if payload is not None else None)
    _raise_on(publish_problem(review, comment_for(longest_url), pr_head) or size_problem(payload))
    return Posting(host, repository, number, payload, reused_url, comment_id, comment_for)


def apply(posting: Posting) -> str:
    """Create the review unless reused or not needed, then write the comment.

    Returns the lines to report.
    """
    lines = ""
    url = posting.reused_url
    if url is not None:
        lines += f"Reused pull request review: {url}\n"
    elif posting.payload is not None:
        url = create_review(posting.host, posting.repository, posting.number, posting.payload)
        lines += f"Posted pull request review: {url}\n"
    body = posting.comment_for(url)
    if posting.comment_id is None:
        comment_url = write_comment(
            posting.host, [f"repos/{posting.repository}/issues/{posting.number}/comments"], body
        )
        return f"{lines}Posted review comment: {comment_url}\n"
    endpoint = ["-X", "PATCH", f"repos/{posting.repository}/issues/comments/{posting.comment_id}"]
    return f"{lines}Updated review comment: {write_comment(posting.host, endpoint, body)}\n"


def publish(pr: str, *, session: str | None) -> str:
    """Post this round's pull request review and the review comment on pr, and return the lines to report.

    Raises:
        PublishReviewError: If the review is incomplete, inconsistent, stale or too long to post.
    """
    return apply(prepare(pr, session=session))


def main() -> int:
    """Post this round's findings as a pull request review, then post or update the pull request's one dovo-review comment."""
    parser = argparse.ArgumentParser(
        description=(
            "Post this round's findings as a pull request review, "
            "then post or update the pull request's one dovo-review comment."
        )
    )
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
