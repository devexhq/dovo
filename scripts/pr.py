#!/usr/bin/env python3
"""Commits, pushes, and opens/updates a PR from the .agentic/ handoff files.

Reads .agentic/commit-msg, .agentic/pr-title, and .agentic/pr-description
(written by the /pr-prep skill) and turns them into a commit of any
uncommitted change, a pushed branch, and a GitHub pull request via `gh`. A
branch whose changes are already committed is pushed as it is.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

AGENTIC_DIR = Path(".agentic")
COMMIT_MSG_FILE = AGENTIC_DIR / "commit-msg"
PR_TITLE_FILE = AGENTIC_DIR / "pr-title"
PR_DESCRIPTION_FILE = AGENTIC_DIR / "pr-description"


class PrPrepError(RuntimeError):
    """Raised when a precondition for commit/push/PR creation is not met."""


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a subprocess command and capture its output as text.

    Args:
        args: Command and arguments to execute.
        check: Whether to raise on a non-zero exit code.

    Returns:
        The completed process, with stdout/stderr captured as text.
    """
    return subprocess.run(args, check=check, capture_output=True, text=True)


def read_handoff_file(path: Path) -> str:
    """Read and validate one of the .agentic/ handoff files.

    Args:
        path: Path to the handoff file.

    Returns:
        The file's stripped text content.

    Raises:
        PrPrepError: If the file is missing or empty.
    """
    if not path.exists():
        raise PrPrepError(f"Missing {path}. Run the /pr-prep skill first to generate it.")
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        raise PrPrepError(f"{path} is empty. Run the /pr-prep skill first to populate it.")
    return content


def get_current_branch() -> str:
    """Return the current git branch name."""
    return run("git", "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def get_default_branch() -> str:
    """Return the repository's default branch name via `gh`.

    Raises:
        PrPrepError: If the default branch cannot be determined.
    """
    result = run("gh", "repo", "view", "--json", "defaultBranchRef", "-q", ".defaultBranchRef.name", check=False)
    branch = result.stdout.strip()
    if result.returncode != 0 or not branch:
        raise PrPrepError(f"Could not determine the default branch via gh: {result.stderr.strip()}")
    return branch


def slugify(title: str, max_words: int = 6) -> str:
    """Turn a PR title into a short kebab-case branch suffix.

    Args:
        title: The PR title to slugify.
        max_words: Maximum number of words to keep.

    Returns:
        A lowercase, hyphenated slug.
    """
    words = re.findall(r"[a-zA-Z0-9]+", title.lower())
    return "-".join(words[:max_words]) or "changes"


def get_branch_username() -> str:
    """Derive a branch-naming username from git config."""
    email = run("git", "config", "user.email", check=False).stdout.strip()
    if email and "@" in email:
        return email.split("@", 1)[0]
    name = run("git", "config", "user.name", check=False).stdout.strip()
    return re.sub(r"\s+", "-", name.lower()) or "agent"


def ensure_feature_branch(default_branch: str, pr_title: str) -> str:
    """Create and check out a feature branch if HEAD is on the default branch.

    Args:
        default_branch: The repository's default branch name.
        pr_title: The drafted PR title, used to derive the branch name.

    Returns:
        The branch name that will be committed to.
    """
    current = get_current_branch()
    if current != default_branch:
        return current
    branch = f"{get_branch_username()}/{slugify(pr_title)}"
    run("git", "checkout", "-b", branch)
    sys.stdout.write(f"Created branch {branch} (was on {default_branch}).\n")
    return branch


def stage_changes(paths: list[str]) -> None:
    """Stage changes for commit.

    Args:
        paths: Specific pathspecs to stage. An empty list stages everything.
    """
    if paths:
        run("git", "add", "--", *paths)
    else:
        run("git", "add", "-A")


def has_staged_changes() -> bool:
    """Return whether anything is currently staged for commit."""
    result = run("git", "diff", "--cached", "--quiet", check=False)
    return result.returncode != 0


def commit(commit_msg: str) -> str:
    """Create a commit from the staged changes.

    Args:
        commit_msg: Full commit message (title + optional body).

    Returns:
        The short SHA of the new commit.
    """
    run("git", "commit", "-m", commit_msg)
    return run("git", "rev-parse", "--short", "HEAD").stdout.strip()


def has_commits_ahead(base: str) -> bool:
    """Return whether HEAD has commits that origin/<base> does not."""
    return run("git", "rev-list", "--count", f"origin/{base}..HEAD").stdout.strip() != "0"


def commit_staged(commit_msg: str, branch: str, base: str) -> None:
    """Commit the staged changes, or accept a branch that already has commits ahead of base.

    Args:
        commit_msg: Full commit message (title + optional body).
        branch: The branch being committed to, for the progress line.
        base: The PR's base branch.

    Raises:
        PrPrepError: If nothing is staged and the branch has no commits ahead of base.
    """
    if has_staged_changes():
        sha = commit(commit_msg)
        sys.stdout.write(f"Committed {sha} on {branch}.\n")
    elif not has_commits_ahead(base):
        raise PrPrepError(f"Nothing to commit and no commits ahead of origin/{base}.")


def push_branch(branch: str) -> None:
    """Push the branch, setting the upstream if it has none yet."""
    upstream = run("git", "rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}", check=False)
    if upstream.returncode == 0:
        run("git", "push")
    else:
        run("git", "push", "--set-upstream", "origin", branch)


def find_existing_pr(branch: str) -> str | None:
    """Return the URL of an open PR for this branch, if one exists."""
    result = run("gh", "pr", "view", branch, "--json", "url", "-q", ".url", check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def create_pr(branch: str, base: str, title: str, description: str) -> str:
    """Open a new PR and return its URL."""
    result = run(
        "gh",
        "pr",
        "create",
        "--base",
        base,
        "--head",
        branch,
        "--title",
        title,
        "--body",
        description,
    )
    return result.stdout.strip().splitlines()[-1]


def cleanup_handoff_files() -> None:
    """Remove the .agentic/ handoff files now that they've been consumed."""
    for path in (COMMIT_MSG_FILE, PR_TITLE_FILE, PR_DESCRIPTION_FILE):
        path.unlink(missing_ok=True)


def main() -> int:
    """Commit staged/uncommitted changes and open or update the PR."""
    parser = argparse.ArgumentParser(description="Commit, push, and open a PR from .agentic/ handoff files.")
    parser.add_argument(
        "paths",
        nargs="*",
        help="Specific pathspecs to stage. Defaults to staging every change (git add -A).",
    )
    parser.add_argument("--base", help="Base branch for the PR. Defaults to the repository's default branch.")
    parser.add_argument(
        "--keep-files",
        action="store_true",
        help="Keep the .agentic/ handoff files after a successful run instead of deleting them.",
    )
    args = parser.parse_args()
    paths: list[str] = args.paths
    base: str | None = args.base
    keep_files: bool = args.keep_files

    try:
        commit_msg = read_handoff_file(COMMIT_MSG_FILE)
        pr_title = read_handoff_file(PR_TITLE_FILE)
        pr_description = read_handoff_file(PR_DESCRIPTION_FILE)

        default_branch = get_default_branch()
        resolved_base = base or default_branch
        branch = ensure_feature_branch(default_branch, pr_title)

        stage_changes(paths)
        commit_staged(commit_msg, branch, resolved_base)

        push_branch(branch)
        sys.stdout.write(f"Pushed {branch} to origin.\n")

        existing_url = find_existing_pr(branch)
        if existing_url:
            sys.stdout.write(f"Open PR already exists: {existing_url}\n")
        else:
            url = create_pr(branch, resolved_base, pr_title, pr_description)
            sys.stdout.write(f"Opened PR: {url}\n")
    except PrPrepError as exc:
        sys.stderr.write(f"pr.py: {exc}\n")
        return 1
    except subprocess.CalledProcessError as exc:
        cmd: list[str] = exc.cmd
        stderr: str = exc.stderr
        sys.stderr.write(f"pr.py: command failed: {' '.join(cmd)}\n{stderr}\n")
        return 1

    if not keep_files:
        cleanup_handoff_files()

    return 0


if __name__ == "__main__":
    sys.exit(main())
