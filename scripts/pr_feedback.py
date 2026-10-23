#!/usr/bin/env python3
"""Restores a pull request's last dovo run state and collects its open feedback.

Reads the pull request's dovo-review comment by the authenticated `gh` user,
takes the dovo session on its `Dovo session:` header line, downloads that
session's plan, plan-questions, review and implementation-report artifacts into
.agentic/, checks out the pull request's head commit, and writes
.agentic/feedback.json: every unresolved review thread, then, when the restored
review asks for changes, its blocking and warning findings.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from publish_review import SESSION_LABEL, find_own_comment, list_comments, pr_repository, run

AGENTIC_DIR = Path(".agentic")
FEEDBACK_JSON = AGENTIC_DIR / "feedback.json"
PR_BRANCH = AGENTIC_DIR / "pr-branch"
REVIEW_JSON = AGENTIC_DIR / "review.json"
RESTORED_ARTIFACTS = ("plan", "plan-questions", "review", "implementation-report")
FINDING_SEVERITIES = ("BLOCKER", "WARNING")
SUMMARY_CHARS = 200
NO_AUTHOR_LABEL = "dovo-review"
THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $endCursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          isResolved
          path
          line
          comments(first: 1) { nodes { author { __typename login } body url } }
        }
      }
    }
  }
}
"""
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
HTML_TAG = re.compile(r"</?[A-Za-z][^>]*>")
LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
LINE_MARKERS = re.compile(r"^\s*(?:(?:#{1,6}|>|[-*+]|\d+[.)])\s+)*")
INLINE_MARKUP = re.compile(r"`|\*\*|__|~~")
EDGE_EMPHASIS = re.compile(r"(?<!\w)[*_]+(?=\S)|(?<=\S)[*_]+(?!\w)")


class PrFeedbackError(RuntimeError):
    """Raised when the pull request's dovo state cannot be restored or its feedback collected."""


def pr_problem(view: dict[str, object], number: int) -> str | None:
    """Return `pull request #<number> is not open` unless view's state is OPEN, else None."""
    if view.get("state") == "OPEN":
        return None
    return f"pull request #{number} is not open"


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


def comment_session(comments: list[dict[str, object]], viewer: str, number: int) -> str:
    """Return the session named by viewer's dovo-review comment.

    Raises:
        PrFeedbackError: With error 3 when there is no such comment, or error 4 when it names no session.
    """
    comment_id = find_own_comment(comments, viewer)
    comment = next((comment for comment in comments if comment.get("id") == comment_id), None)
    if comment_id is None or comment is None:
        raise PrFeedbackError(
            f"no dovo-review comment on pull request #{number}; run /dovo-review {number} --post first"
        )
    session = session_from_comment(str(comment.get("body", "")))
    if session is None:
        raise PrFeedbackError(f"the dovo-review comment on pull request #{number} names no dovo session")
    return session


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


def thread_summary(body: str) -> str:
    """Return the first line of body that is non-empty once Markdown markup is removed, cut to SUMMARY_CHARS."""
    for line in HTML_COMMENT.sub("", body).splitlines():
        plain = _plain_line(line)
        if plain:
            return plain[:SUMMARY_CHARS]
    return ""


def _author_login(author: object) -> str:
    """Return the GraphQL author's login, with `[bot]` added for a Bot, or `ghost` when GitHub returns none."""
    if not isinstance(author, dict) or not author.get("login"):
        return "ghost"
    login = str(author["login"])
    return f"{login}[bot]" if author.get("__typename") == "Bot" else login


def _first_comment(thread: dict[str, object]) -> dict[str, object] | None:
    """Return the thread's first comment node, or None when it has none."""
    comments = thread.get("comments")
    nodes = comments.get("nodes") if isinstance(comments, dict) else None
    if not isinstance(nodes, list) or not nodes or not isinstance(nodes[0], dict):
        return None
    return nodes[0]


def thread_items(threads: list[dict[str, object]]) -> list[dict[str, object]]:
    """Return one `review_thread` item per unresolved thread, from its first comment, with ids T1, T2, ... in order."""
    items: list[dict[str, object]] = []
    for thread in threads:
        comment = _first_comment(thread)
        if thread.get("isResolved") or comment is None:
            continue
        body = str(comment.get("body") or "")
        items.append(
            {
                "id": f"T{len(items) + 1}",
                "source": "review_thread",
                "author": _author_login(comment.get("author")),
                "url": comment.get("url"),
                "path": thread.get("path"),
                "line": thread.get("line"),
                "summary": thread_summary(body),
                "body": body,
            }
        )
    return items


def finding_items(review: dict[str, object]) -> list[dict[str, object]]:
    """Return one `review_finding` item per BLOCKER or WARNING finding when the verdict is CHANGES_REQUIRED, ids R1, R2, ...; else []."""
    findings = review.get("findings")
    if review.get("verdict") != "CHANGES_REQUIRED" or not isinstance(findings, list):
        return []
    selected = [f for f in findings if isinstance(f, dict) and f.get("severity") in FINDING_SEVERITIES]
    return [
        {
            "id": f"R{index}",
            "source": "review_finding",
            "author": None,
            "url": None,
            "path": finding.get("path"),
            "line": finding.get("line"),
            "summary": finding.get("summary"),
            "body": f"{finding.get('id')}: {finding.get('summary')}",
        }
        for index, finding in enumerate(selected, start=1)
    ]


def option_label(item: dict[str, object]) -> str:
    """Return `<id> [<source>, <author>] <summary>`, with NO_AUTHOR_LABEL for a null author."""
    author = item["author"] if item["author"] is not None else NO_AUTHOR_LABEL
    return f"{item['id']} [{item['source']}, {author}] {item['summary']}"


def feedback_document(pr: int, head: str, session: str, items: list[dict[str, object]]) -> dict[str, object]:
    """Return the feedback.json object: pr, head, session and the items, each with its `label` added."""
    return {
        "pr": pr,
        "head": head,
        "session": session,
        "items": [{**item, "label": option_label(item)} for item in items],
    }


def list_threads(host: str, repository: str, number: int) -> list[dict[str, object]]:
    """Return every review thread on the pull request, one dict per thread, through paginated GraphQL."""
    owner, name = repository.split("/", 1)
    output = run(
        "gh",
        "api",
        "graphql",
        "--hostname",
        host,
        "--paginate",
        "-f",
        f"query={THREADS_QUERY}",
        "-f",
        f"owner={owner}",
        "-f",
        f"name={name}",
        "-F",
        f"number={number}",
        "--jq",
        ".data.repository.pullRequest.reviewThreads.nodes[]",
    )
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def open_pull_request(pr: int) -> dict[str, object]:
    """Return pr's number, state, head branch, head commit and URL.

    Raises:
        PrFeedbackError: With error 2 when the pull request does not exist or is not open.
    """
    try:
        view = json.loads(run("gh", "pr", "view", str(pr), "--json", "number,state,headRefName,headRefOid,url"))
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


def check_out_head(number: int, head_branch: str, head: str) -> None:
    """Detach the worktree at the pull request's head commit and record its head branch in .agentic/pr-branch."""
    run("git", "fetch", "-q", "origin", f"refs/pull/{number}/head")
    run("git", "switch", "-q", "--detach", head)
    PR_BRANCH.write_text(f"{head_branch}\n", encoding="utf-8")


def collect(pr: int) -> str:
    """Restore pr's dovo state into .agentic/, check out its head and write feedback.json; return the line to report.

    Raises:
        PrFeedbackError: For errors 2-5 of issue #1124.
    """
    view = open_pull_request(pr)
    number = int(str(view["number"]))
    head = str(view["headRefOid"])
    host, repository = pr_repository(str(view["url"]))
    viewer = run("gh", "api", "--hostname", host, "user", "--jq", ".login")
    session = comment_session(list_comments(host, repository, number), viewer, number)
    download_artifacts(session)
    check_out_head(number, str(view["headRefName"]), head)
    review = json.loads(REVIEW_JSON.read_text(encoding="utf-8"))
    items = thread_items(list_threads(host, repository, number)) + finding_items(review)
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
