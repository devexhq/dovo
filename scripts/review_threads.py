#!/usr/bin/env python3
"""Reads a pull request's review threads, replies on them and resolves them.

Threads are rebuilt from the pull request's review comments, read through
GitHub's REST API: a comment with no `in_reply_to_id` starts a thread and every
reply names that first comment. A thread's id is its first comment's REST id,
as a string. Replies are posted through the REST API too. Only whether a
thread is resolved, and resolving it, go through GraphQL, because GitHub's
REST API offers neither; resolved_roots and resolve_thread are the only
functions that do.

Each thread is a dict:
    {"id", "isResolved", "path", "line", "originalStartLine", "originalLine",
     "comments": {"nodes": [{"author": {"login"}, "body", "url", "originalCommit": {"oid"}}, ...]}}
with comments oldest first and REST logins, so a bot's login ends in `[bot]`.

Run as a script, it prints the threads of `--pr <number, URL or branch>` as a JSON array.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

from publish_review import PublishReviewError, pr_repository, pull_request, run, write_comment

STATES_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $endCursor) {
        pageInfo { hasNextPage endCursor }
        nodes { id isResolved comments(first: 1) { nodes { databaseId } } }
      }
    }
  }
}
"""
RESOLVE_MUTATION = """
mutation($thread: ID!) {
  resolveReviewThread(input: {threadId: $thread}) { thread { isResolved } }
}
"""
COMMENT_FIELDS = (
    ".[] | {id, in_reply_to_id, path, line, original_line, original_start_line, original_commit_id, "
    "login: .user.login, body, url: .html_url}"
)


def list_review_comments(host: str, repository: str, number: int) -> list[dict[str, object]]:
    """Return every review comment on the pull request, oldest first, each with COMMENT_FIELDS' keys."""
    output = run(
        "gh",
        "api",
        "--hostname",
        host,
        "--paginate",
        f"repos/{repository}/pulls/{number}/comments",
        "--jq",
        COMMENT_FIELDS,
    )
    comments = [json.loads(line) for line in output.splitlines() if line.strip()]
    return sorted(comments, key=lambda comment: int(comment["id"]))


def _comment_node(comment: dict[str, object]) -> dict[str, object]:
    """Return a REST review comment as a thread's comment node."""
    return {
        "author": {"login": comment.get("login")},
        "body": comment.get("body"),
        "url": comment.get("url"),
        "originalCommit": {"oid": comment.get("original_commit_id")},
    }


def group_threads(comments: list[dict[str, object]], resolved: set[int]) -> list[dict[str, object]]:
    """Return one thread per first comment of comments, in order, with its replies and isResolved from resolved.

    comments are list_review_comments entries; a reply whose first comment is missing is left out.
    """
    replies: dict[object, list[dict[str, object]]] = {}
    for comment in comments:
        if comment.get("in_reply_to_id") is not None:
            replies.setdefault(comment["in_reply_to_id"], []).append(comment)
    return [
        {
            "id": str(root["id"]),
            "isResolved": int(str(root["id"])) in resolved,
            "path": root.get("path"),
            "line": root.get("line"),
            "originalStartLine": root.get("original_start_line"),
            "originalLine": root.get("original_line"),
            "comments": {"nodes": [_comment_node(node) for node in [root, *replies.get(root["id"], [])]]},
        }
        for root in comments
        if root.get("in_reply_to_id") is None
    ]


def _thread_states(host: str, repository: str, number: int) -> list[dict[str, object]]:
    """Return each review thread's GraphQL `id`, `isResolved` and first comment's REST id as `root`."""
    owner, name = repository.split("/", 1)
    output = run(
        "gh",
        "api",
        "graphql",
        "--hostname",
        host,
        "--paginate",
        "-f",
        f"query={STATES_QUERY}",
        "-f",
        f"owner={owner}",
        "-f",
        f"name={name}",
        "-F",
        f"number={number}",
        "--jq",
        ".data.repository.pullRequest.reviewThreads.nodes[] | {id, isResolved, root: .comments.nodes[0].databaseId}",
    )
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def resolved_roots(host: str, repository: str, number: int) -> set[int]:
    """Return the REST ids of the first comments of the pull request's resolved review threads."""
    return {int(str(state["root"])) for state in _thread_states(host, repository, number) if state.get("isResolved")}


def resolve_thread(host: str, repository: str, number: int, thread: str) -> None:
    """Resolve the review thread whose first comment has the REST id thread.

    Raises:
        LookupError: When the pull request has no such thread.
    """
    states = _thread_states(host, repository, number)
    node = next((state["id"] for state in states if str(state.get("root")) == thread), None)
    if node is None:
        raise LookupError(f"pull request #{number} has no review thread starting at comment {thread}")
    run(
        "gh",
        "api",
        "graphql",
        "--hostname",
        host,
        "-f",
        f"query={RESOLVE_MUTATION}",
        "-f",
        f"thread={node}",
        "--jq",
        ".data.resolveReviewThread.thread.isResolved",
    )


def list_threads(host: str, repository: str, number: int) -> list[dict[str, object]]:
    """Return the pull request's review threads, each marked resolved or not."""
    return group_threads(list_review_comments(host, repository, number), resolved_roots(host, repository, number))


def reply_on_thread(host: str, repository: str, number: int, thread: str, body: str) -> str:
    """Post body as a reply on the review thread whose first comment has the REST id thread; return its URL."""
    return write_comment(host, ["-X", "POST", f"repos/{repository}/pulls/{number}/comments/{thread}/replies"], body)


def main() -> int:
    """Print the review threads of --pr as a JSON array."""
    parser = argparse.ArgumentParser(description="Print a pull request's review threads as JSON.")
    parser.add_argument("--pr", required=True, help="Pull request number, URL or branch.")
    args = parser.parse_args()
    pr: str = args.pr
    try:
        number, _, url = pull_request(pr)
        host, repository = pr_repository(url)
        threads = list_threads(host, repository, number)
    except PublishReviewError as error:
        sys.stderr.write(f"review_threads.py: {error}\n")
        return 1
    except subprocess.CalledProcessError as error:
        command: list[str] = error.cmd
        stderr: str = error.stderr
        sys.stderr.write(f"review_threads.py: command failed: {' '.join(command)}\n{stderr}\n")
        return 1
    sys.stdout.write(json.dumps(threads, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
