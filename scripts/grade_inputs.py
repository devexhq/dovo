#!/usr/bin/env python3
"""Collects the measurable facts the /dovo-grade skill scores a pull request from.

Reads the pull request and its check runs on the head commit. When the
authenticated `gh` user's dovo-review comment names a dovo session, downloads
that session's plan, plan-questions, review and implementation-report artifacts
into .agentic/grade-run/ and reads the session's step history and, for an
issue-code session, its slice's phase count from the plan. Extracts the
head commit's files into .agentic/grade-pr/ and its diff into
.agentic/grade-pr.diff, so the grader reads the pull request without checking
it out and nothing from it runs. Writes .agentic/grade-inputs.json.
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import sys
import tarfile
from collections import Counter
from pathlib import Path

from pr_feedback import FAILED_CONCLUSIONS, list_checks
from publish_review import find_own_comment, list_comments, pr_repository, run, session_from_comment

AGENTIC_DIR = Path(".agentic")
INPUTS_JSON = AGENTIC_DIR / "grade-inputs.json"
PR_DIR = AGENTIC_DIR / "grade-pr"
PR_DIFF = AGENTIC_DIR / "grade-pr.diff"
RUN_DIR = AGENTIC_DIR / "grade-run"
RESTORED_ARTIFACTS = ("plan", "plan-questions", "review", "implementation-report")
PR_FIELDS = "number,url,title,body,state,headRefOid,baseRefName,additions,deletions,changedFiles,files"


def check_summary(checks: list[dict[str, object]]) -> dict[str, object]:
    """Return the head commit's check runs as a total and the names of the failed and still pending ones."""
    failed = sorted(str(check["name"]) for check in checks if check.get("conclusion") in FAILED_CONCLUSIONS)
    pending = sorted(str(check["name"]) for check in checks if check.get("conclusion") is None)
    return {"total": len(checks), "failed": failed, "pending": pending}


def review_summary(review: dict[str, object] | None, pr_head: str) -> dict[str, object] | None:
    """Return the restored review's verdict, counts and coverage verdict tally, or None without a review.

    `current` says whether the review covered the pull request's head commit;
    a review of an earlier commit says nothing about the code being graded.
    """
    if review is None:
        return None
    coverage = review.get("coverage")
    rows = coverage if isinstance(coverage, list) else []
    tally = Counter(str(row.get("verdict")) for row in rows if isinstance(row, dict))
    return {
        "head": review.get("head"),
        "current": review.get("head") == pr_head,
        "verdict": review.get("verdict"),
        "action": review.get("action"),
        "round": review.get("round"),
        "counts": review.get("counts"),
        "coverage": dict(sorted(tally.items())),
    }


def _loop_iterations(steps: list[dict[str, object]], loop_id: str) -> int | None:
    """Return how many iterations the loop step loop_id ran, or None when the session has no such loop."""
    loop = next((step for step in steps if step.get("kind") == "loop" and step.get("step_id") == loop_id), None)
    iterations = loop.get("iterations") if loop is not None else None
    return iterations if isinstance(iterations, int) else None


def _steps(payload: dict[str, object]) -> list[dict[str, object]]:
    """Return the session's step and loop entries, dropping anything that is not an object."""
    raw_steps = payload.get("steps")
    return [step for step in raw_steps if isinstance(step, dict)] if isinstance(raw_steps, list) else []


def _slice_phases(blueprint: object, plan: dict[str, object] | None) -> int | None:
    """Return the phase count of an issue-code session's slice plan, or None for another blueprint or no plan.

    An issue-code session runs one code-loop iteration per phase of its slice,
    and its slice plan's top-level `phases` is that slice's phase count.
    """
    phases = plan.get("phases") if blueprint == "issue-code" and plan is not None else None
    return phases if isinstance(phases, int) and not isinstance(phases, bool) else None


def process_summary(payload: dict[str, object], plan: dict[str, object] | None) -> dict[str, object]:
    """Return a dovo session's blueprint, outcome, loop iterations, slice phases and retried or failed steps.

    Args:
        payload: The payload of `dovo session show <id> --format json`.
        plan: The session's restored plan.json, or None when none was restored.
    """
    session = payload.get("session")
    session = session if isinstance(session, dict) else {}
    steps = _steps(payload)
    attempts = [step for step in steps if step.get("kind") == "step"]
    blueprint = session.get("blueprint_name")
    return {
        "blueprint": blueprint,
        "status": session.get("status"),
        "slice_phases": _slice_phases(blueprint, plan),
        "code_loop_iterations": _loop_iterations(steps, "code-loop"),
        "review_loop_iterations": _loop_iterations(steps, "review-loop"),
        "retried_steps": sorted({str(step["step_id"]) for step in attempts if (step.get("attempts") or 1) > 1}),
        "failed_steps": sorted({str(step["step_id"]) for step in attempts if step.get("state") == "failed"}),
    }


def gates(checks: dict[str, object], review: dict[str, object] | None) -> dict[str, object]:
    """Return the facts that cap a grade: whether a check failed, and the open blockers of a current review.

    `open_blockers` is None when no review covered the head commit.
    """
    counts = review.get("counts") if review is not None and review["current"] else None
    blocking = counts.get("blocking") if isinstance(counts, dict) else None
    return {"checks_failed": bool(checks["failed"]), "open_blockers": blocking}


def pr_summary(view: dict[str, object]) -> dict[str, object]:
    """Return the pull request fields the grader reads, with each changed file's path and line counts."""
    files = view.get("files")
    changed = [
        {"path": item.get("path"), "additions": item.get("additions"), "deletions": item.get("deletions")}
        for item in (files if isinstance(files, list) else [])
        if isinstance(item, dict)
    ]
    keys = ("number", "url", "title", "body", "state", "baseRefName", "additions", "deletions", "changedFiles")
    return {**{key: view.get(key) for key in keys}, "head": view.get("headRefOid"), "files": changed}


def restore_run(session: str) -> tuple[list[str], dict[str, object] | None]:
    """Download session's artifacts into RUN_DIR and return the names restored and the session's step history.

    A session that lacks an artifact (a pr-fix run has no plan of its own) is
    graded without it, so a failed download is skipped, not raised.
    """
    shutil.rmtree(RUN_DIR, ignore_errors=True)
    RUN_DIR.mkdir(parents=True)
    restored = []
    for name in RESTORED_ARTIFACTS:
        try:
            run("uv", "run", "dovo", "artifacts", "download", session, name, "--dest", str(RUN_DIR))
        except subprocess.CalledProcessError:
            continue
        restored.append(name)
    try:
        shown = json.loads(run("uv", "run", "dovo", "session", "show", session, "--format", "json"))
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return restored, None
    payload = shown.get("payload") if isinstance(shown, dict) else None
    plan = _restored_json(restored, "plan-questions", "plan.json")
    return restored, process_summary(payload, plan) if isinstance(payload, dict) else None


def extract_head(number: int, head: str) -> None:
    """Write the head commit's files into PR_DIR and the pull request's diff into PR_DIFF, checking nothing out."""
    run("git", "fetch", "-q", "origin", f"refs/pull/{number}/head")
    archive = subprocess.run(("git", "archive", "--format=tar", head), check=True, capture_output=True).stdout
    shutil.rmtree(PR_DIR, ignore_errors=True)
    PR_DIR.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(PR_DIR, filter="data")
    PR_DIFF.write_text(run("gh", "pr", "diff", str(number)) + "\n", encoding="utf-8")


def _restored_json(restored: list[str], artifact: str, filename: str) -> dict[str, object] | None:
    """Return the restored artifact's .agentic/<filename>, or None when it was not restored or is not a JSON object."""
    path = RUN_DIR / ".agentic" / filename
    if artifact not in restored or not path.exists():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return document if isinstance(document, dict) else None


def collect(pr: str) -> str:
    """Write .agentic/grade-inputs.json for pr and return the line to report."""
    view = json.loads(run("gh", "pr", "view", pr, "--json", PR_FIELDS))
    number = int(view["number"])
    head = str(view["headRefOid"])
    host, repository = pr_repository(str(view["url"]))
    viewer = run("gh", "api", "--hostname", host, "user", "--jq", ".login")
    comments = list_comments(host, repository, number)
    comment_id = find_own_comment(comments, viewer)
    body = next((str(c.get("body", "")) for c in comments if comment_id is not None and c.get("id") == comment_id), "")
    session = session_from_comment(body)
    restored, process = restore_run(session) if session is not None else ([], None)
    extract_head(number, head)
    checks = check_summary(list_checks(host, repository, head))
    review = review_summary(_restored_json(restored, "review", "review.json"), head)
    document = {
        "pr": pr_summary(view),
        "checks": checks,
        "run": {"session": session, "restored": restored, "process": process},
        "review": review,
        "gates": gates(checks, review),
    }
    AGENTIC_DIR.mkdir(exist_ok=True)
    INPUTS_JSON.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    run_note = f"dovo session {session}" if session is not None else "no dovo run"
    return f"Collected grade inputs for pull request #{number} at {head[:12]} ({run_note})\n"


def main() -> int:
    """Collect the pull request's grade inputs into .agentic/grade-inputs.json."""
    parser = argparse.ArgumentParser(description="Collect the facts /dovo-grade scores a pull request from.")
    parser.add_argument("--pr", required=True, help="Pull request number, URL or branch.")
    args = parser.parse_args()
    pr: str = args.pr
    try:
        sys.stdout.write(collect(pr))
    except subprocess.CalledProcessError as error:
        command: list[str] = error.cmd
        stderr = error.stderr.decode() if isinstance(error.stderr, bytes) else str(error.stderr)
        sys.stderr.write(f"grade_inputs.py: command failed: {' '.join(command)}\n{stderr}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
