#!/usr/bin/env python3
"""Posts or updates the pull request's one dovo-review comment.

Reads .agentic/review.json and .agentic/review.md (written by the /dovo-review
skill) and publishes them as a single PR comment that starts with a marker line.
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
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

AGENTIC_DIR = Path(".agentic")
MARKER = "<!-- dovo-review -->"
SESSION_LABEL = "Dovo session: "
MAX_COMMENT_CHARS = 65_536
STAMPED_KEYS = ("head", "verdict", "action", "round")
REQUIRED_KEYS = (*STAMPED_KEYS, "counts")
VERDICTS = ("APPROVE", "CHANGES_REQUIRED")
ACTIONS = ("none", "fix", "replan", "needs_human")
REVIEW_JSON = AGENTIC_DIR / "review.json"
REVIEW_MD = AGENTIC_DIR / "review.md"


class PublishReviewError(RuntimeError):
    """Raised when the review cannot be published to the pull request."""


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


def find_own_comment(comments: list[dict[str, object]], viewer: str) -> int | None:
    """Return the id of the first comment whose first line is MARKER and whose user is viewer, else None.

    Args:
        comments: The PR's comments, each with `id`, `user` (a login) and `body`.
        viewer: The login of the authenticated `gh` user.

    Returns:
        The id of the viewer's marked comment, or None when there is none.
    """
    for comment in comments:
        body = comment.get("body")
        comment_id = comment.get("id")
        if comment.get("user") != viewer or not isinstance(body, str) or not isinstance(comment_id, int):
            continue
        if body.splitlines()[:1] == [MARKER]:
            return comment_id
    return None


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
    if approved != (review["action"] == "none"):
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


def publish_problem(review: dict[str, object], body: str, pr_head: str) -> str | None:
    """Return why body, built from review, must not be posted to a PR at pr_head, or None when it may be.

    Args:
        review: The parsed review.json.
        body: The comment body to post; an empty string skips only the size check.
        pr_head: The full SHA of the pull request's head commit.

    Returns:
        The first problem found, or None when the comment may be posted.
    """
    problem = record_problem(review)
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
    """Return every comment on the pull request, one dict per comment."""
    output = run(
        "gh",
        "api",
        "--hostname",
        host,
        "--paginate",
        f"repos/{repository}/issues/{number}/comments",
        "--jq",
        ".[] | {id, body, user: .user.login}",
    )
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def write_comment(host: str, endpoint: list[str], body: str) -> str:
    """Send body to the comment endpoint (method flags and path) on host and return the comment's URL."""
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as handle:
        handle.write(body)
    body_file = Path(handle.name)
    try:
        return run("gh", "api", "--hostname", host, *endpoint, "-F", f"body=@{body_file}", "--jq", ".html_url")
    finally:
        body_file.unlink(missing_ok=True)


def publish(pr: str, *, session: str | None) -> str:
    """Post or update the review comment on pr, stamped with session when given, and return the line to report.

    Raises:
        PublishReviewError: If the review is incomplete, inconsistent, stale or too long to post.
    """
    review, report = read_review()
    view = json.loads(run("gh", "pr", "view", pr, "--json", "number,headRefOid,url"))
    number: int = view["number"]
    pr_head: str = view["headRefOid"]
    host, repository = pr_repository(view["url"])
    # comment_body needs every stamped key, so check the review before building the body.
    body = ""
    problem = publish_problem(review, body, pr_head) or report_problem(review, report)
    if problem is None:
        body = comment_body(review, report, session=session)
        problem = publish_problem(review, body, pr_head)
    if problem is not None:
        raise PublishReviewError(problem)
    viewer = run("gh", "api", "--hostname", host, "user", "--jq", ".login")
    comment_id = find_own_comment(list_comments(host, repository, number), viewer)
    if comment_id is None:
        url = write_comment(host, [f"repos/{repository}/issues/{number}/comments"], body)
        return f"Posted review comment: {url}\n"
    url = write_comment(host, ["-X", "PATCH", f"repos/{repository}/issues/comments/{comment_id}"], body)
    return f"Updated review comment: {url}\n"


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
