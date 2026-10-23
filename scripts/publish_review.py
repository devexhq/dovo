#!/usr/bin/env python3
"""Posts or updates the pull request's one dovo-review comment.

Reads .agentic/review.json and .agentic/review.md (written by the /dovo-review
skill) and publishes them as a single PR comment that starts with a marker line.
Every later run edits that same comment instead of adding another. The header
under the marker is stamped from review.json, never from the report's prose, and
the comment is refused unless the pull request head is the commit the review
covered.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

AGENTIC_DIR = Path(".agentic")
MARKER = "<!-- dovo-review -->"
MAX_COMMENT_CHARS = 65_536
STAMPED_KEYS = ("head", "verdict", "action", "round")
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


def comment_body(review: dict[str, object], report: str) -> str:
    """Return the comment: the marker, a header stamped from review.json, a blank line, then review.md.

    The header is exactly:
        <!-- dovo-review -->
        Reviewed commit: `<head>`
        Verdict: <verdict> · Action: <action> · Round: <round>

    Args:
        review: The parsed review.json, holding every STAMPED_KEYS entry.
        report: The text of review.md.

    Returns:
        The full comment body.
    """
    header = (
        f"{MARKER}\n"
        f"Reviewed commit: `{review['head']}`\n"
        f"Verdict: {review['verdict']} · Action: {review['action']} · Round: {review['round']}\n"
    )
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


def publish_problem(review: dict[str, object], body: str, pr_head: str) -> str | None:
    """Return why body, built from review, must not be posted to a PR at pr_head, or None when it may be.

    Args:
        review: The parsed review.json.
        body: The comment body to post; an empty string skips only the size check.
        pr_head: The full SHA of the pull request's head commit.

    Returns:
        The first problem found, or None when the comment may be posted.
    """
    for key in STAMPED_KEYS:
        if key not in review:
            return f"review.json has no {key!r}; run /dovo-review again"
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
    except json.JSONDecodeError as exc:
        raise PublishReviewError(f"{REVIEW_JSON} is not valid JSON: {exc}") from exc
    if not isinstance(review, dict):
        raise PublishReviewError(f"{REVIEW_JSON} must hold a JSON object.")
    return review, REVIEW_MD.read_text(encoding="utf-8")


def list_comments(number: int) -> list[dict[str, object]]:
    """Return every comment on the pull request, one dict per comment."""
    output = run(
        "gh",
        "api",
        "--paginate",
        f"repos/{{owner}}/{{repo}}/issues/{number}/comments",
        "--jq",
        ".[] | {id, body, user: .user.login}",
    )
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def write_comment(number: int, comment_id: int | None, body: str) -> str:
    """Create the comment, or edit comment_id in place, and return the comment's URL."""
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as handle:
        handle.write(body)
    body_file = Path(handle.name)
    try:
        if comment_id is None:
            endpoint = ["gh", "api", f"repos/{{owner}}/{{repo}}/issues/{number}/comments"]
        else:
            endpoint = ["gh", "api", "-X", "PATCH", f"repos/{{owner}}/{{repo}}/issues/comments/{comment_id}"]
        return run(*endpoint, "-F", f"body=@{body_file}", "--jq", ".html_url")
    finally:
        body_file.unlink(missing_ok=True)


def publish(pr: str) -> str:
    """Post or update the review comment on pr and return the line to report.

    Raises:
        PublishReviewError: If the review is incomplete, stale or too long to post.
    """
    review, report = read_review()
    view = json.loads(run("gh", "pr", "view", pr, "--json", "number,headRefOid"))
    number: int = view["number"]
    pr_head: str = view["headRefOid"]
    # comment_body needs every stamped key, so check the review before building the body.
    body = ""
    problem = publish_problem(review, body, pr_head)
    if problem is None:
        body = comment_body(review, report)
        problem = publish_problem(review, body, pr_head)
    if problem is not None:
        raise PublishReviewError(problem)
    viewer = run("gh", "api", "user", "--jq", ".login")
    comment_id = find_own_comment(list_comments(number), viewer)
    url = write_comment(number, comment_id, body)
    return f"{'Updated' if comment_id is not None else 'Posted'} review comment: {url}\n"


def main() -> int:
    """Publish .agentic/review.md to the pull request as its one dovo-review comment."""
    parser = argparse.ArgumentParser(description="Post or update the pull request's one dovo-review comment.")
    parser.add_argument("--pr", required=True, help="Pull request number, URL or branch.")
    args = parser.parse_args()
    pr: str = args.pr

    try:
        sys.stdout.write(publish(pr))
    except PublishReviewError as exc:
        sys.stderr.write(f"publish_review.py: {exc}\n")
        return 1
    except subprocess.CalledProcessError as exc:
        cmd: list[str] = exc.cmd
        stderr: str = exc.stderr
        sys.stderr.write(f"publish_review.py: command failed: {' '.join(cmd)}\n{stderr}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
