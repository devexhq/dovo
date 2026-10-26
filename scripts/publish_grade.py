#!/usr/bin/env python3
"""Checks a /dovo-grade grade and posts or updates the pull request's one dovo-grade comment.

Reads .agentic/grade.json and .agentic/grade.md (written by the /dovo-grade
skill) and .agentic/grade-inputs.json (written by scripts/grade_inputs.py).
The score is never taken on trust: it is recomputed here from the dimension
scores, the rubric's weights and the caps, and a grade whose numbers, gates or
graded commit disagree with that computation or with the inputs is refused.
With --check, only validates and prints the expected score. Otherwise posts the
grade as a single PR comment that starts with a marker line, and every later
run edits that same comment instead of adding another. With --session, the
header also names the dovo session that produced the grade.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from publish_review import (
    MAX_COMMENT_CHARS,
    SESSION_LABEL,
    find_own_comment,
    list_comments,
    pr_repository,
    run,
    write_comment,
)

AGENTIC_DIR = Path(".agentic")
MARKER = "<!-- dovo-grade -->"
RUBRIC = "dovo-grade@1"
GRADE_JSON = AGENTIC_DIR / "grade.json"
GRADE_MD = AGENTIC_DIR / "grade.md"
INPUTS_JSON = AGENTIC_DIR / "grade-inputs.json"
WEIGHTS = {
    "correctness": 25,
    "contract_fidelity": 15,
    "test_quality": 15,
    "architecture_fit": 15,
    "simplicity_scope": 10,
    "docs": 5,
    "reviewability": 5,
    "process_efficiency": 10,
}
LETTERS = ((90, "A"), (80, "B"), (70, "C"), (60, "D"))
CAP_LIMITS = {"checks_failed": 59, "open_blockers": 69, "correctness": 79}
REQUIRED_KEYS = (
    "rubric",
    "pr",
    "head",
    "dimensions",
    "gates",
    "raw_score",
    "score",
    "grade",
    "caps",
    "summary",
    "levers",
)
MAX_SCORE = 4


class PublishGradeError(RuntimeError):
    """Raised when the grade is inconsistent or cannot be published to the pull request."""


def letter(score: int) -> str:
    """Return the letter for a 0-100 score: A from 90, B from 80, C from 70, D from 60, otherwise F."""
    return next((grade for floor, grade in LETTERS if score >= floor), "F")


def raw_score(scores: dict[str, int | None]) -> int:
    """Return the weighted mean of the scored dimensions on a 0-100 scale, rounded half up.

    A dimension scored None is left out and the remaining weights are
    renormalised, so a pull request is never marked down for a fact that does
    not exist, such as process efficiency without a dovo run.

    Raises:
        PublishGradeError: When no dimension is scored.
    """
    scored = {name: score for name, score in scores.items() if score is not None}
    if not scored:
        raise PublishGradeError("grade.json scores no dimension")
    # Integer arithmetic: a float mean misrounds exact halves, such as 57.5 to 57.
    numerator = 100 * sum(WEIGHTS[name] * score for name, score in scored.items())
    denominator = MAX_SCORE * sum(WEIGHTS[name] for name in scored)
    return (2 * numerator + denominator) // (2 * denominator)


def applied_caps(gates: dict[str, object], correctness: int | None) -> list[str]:
    """Return the caps that apply, in CAP_LIMITS order: a failed check, an open blocker, correctness of 1 or less."""
    blockers = gates.get("open_blockers")
    applies = {
        "checks_failed": gates.get("checks_failed") is True,
        "open_blockers": isinstance(blockers, int) and blockers > 0,
        "correctness": correctness is not None and correctness <= 1,
    }
    return [name for name in CAP_LIMITS if applies[name]]


def expected(scores: dict[str, int | None], gates: dict[str, object]) -> dict[str, object]:
    """Return the raw_score, score, grade and caps that scores and gates produce."""
    raw = raw_score(scores)
    caps = applied_caps(gates, scores.get("correctness"))
    score = min([raw, *(CAP_LIMITS[cap] for cap in caps)])
    return {"raw_score": raw, "score": score, "grade": letter(score), "caps": caps}


def _dimension_problem(dimension: object, name: str) -> str | None:
    """Return why one dimensions[] entry does not hold the rubric's dimension name, or None when it does."""
    if not isinstance(dimension, dict) or dimension.get("id") != name:
        return f"grade.json dimensions must list {', '.join(WEIGHTS)} in that order"
    if dimension.get("weight") != WEIGHTS[name]:
        return f"grade.json weight for {name} is {dimension.get('weight')!r}; {RUBRIC} weighs it {WEIGHTS[name]}"
    score = dimension.get("score")
    if score is not None and (isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= MAX_SCORE):
        return f"grade.json score for {name} is {score!r}; it must be an integer from 0 to {MAX_SCORE} or null"
    evidence = dimension.get("evidence")
    if not isinstance(evidence, str) or not evidence.strip():
        return f"grade.json {name} has no evidence; every score, and every null, cites why"
    return None


def scores_of(grade: dict[str, object]) -> tuple[dict[str, int | None], str | None]:
    """Return the dimension scores by name, and why the dimensions are malformed, or None when they are not."""
    dimensions = grade.get("dimensions")
    if not isinstance(dimensions, list) or len(dimensions) != len(WEIGHTS):
        return {}, f"grade.json dimensions must list {', '.join(WEIGHTS)} in that order"
    for dimension, name in zip(dimensions, WEIGHTS, strict=True):
        problem = _dimension_problem(dimension, name)
        if problem is not None:
            return {}, problem
    return {str(d["id"]): d["score"] for d in dimensions if isinstance(d, dict)}, None


def _inputs_problem(grade: dict[str, object], inputs: dict[str, object]) -> str | None:
    """Return why grade.json does not grade the pull request and commit in grade-inputs.json, or None."""
    pr = inputs.get("pr")
    pr = pr if isinstance(pr, dict) else {}
    if grade["pr"] != pr.get("number") or grade["head"] != pr.get("head"):
        return f"grade.json grades #{grade['pr']} at {grade['head']}, but the inputs are #{pr.get('number')} at {pr.get('head')}"
    if grade["gates"] != inputs.get("gates"):
        return f"grade.json gates {grade['gates']!r} differ from the inputs' {inputs.get('gates')!r}"
    return None


def grade_problem(grade: dict[str, object], inputs: dict[str, object]) -> str | None:
    """Return why grade.json cannot be published, or None when its score is the one the rubric computes.

    Args:
        grade: The parsed grade.json.
        inputs: The parsed grade-inputs.json the grade was scored from.

    Returns:
        The first problem found, or None when the grade is consistent.
    """
    for key in REQUIRED_KEYS:
        if key not in grade:
            return f"grade.json has no {key!r}; run /dovo-grade again"
    if grade["rubric"] != RUBRIC:
        return f"grade.json rubric {grade['rubric']!r} is not {RUBRIC}"
    scores, problem = scores_of(grade)
    if problem is not None:
        return problem
    problem = _inputs_problem(grade, inputs)
    if problem is not None:
        return problem
    gates = grade["gates"]
    want = expected(scores, gates if isinstance(gates, dict) else {})
    got = {key: grade[key] for key in want}
    if got != want:
        return f"grade.json says {got}, but {RUBRIC} computes {want}"
    return None


def report_problem(grade: dict[str, object], report: str) -> str | None:
    """Return why grade.md's Grade or Graded commit line disagrees with grade.json, or None when both agree."""
    expected_lines = {
        "**Grade:**": f"**Grade:** {grade['grade']} ({grade['score']}) · **Rubric:** {RUBRIC}",
        "**Graded commit:**": f"**Graded commit:** `{grade['head']}`",
    }
    lines = report.splitlines()
    for label, want in expected_lines.items():
        line = next((line for line in lines if line.startswith(label)), None)
        if line is None:
            return f"grade.md has no {label.strip('*:')} line; run /dovo-grade again"
        if line.strip() != want:
            return f"grade.md {label.strip('*:')} line is {line!r}; grade.json says {want!r}"
    return None


def comment_body(grade: dict[str, object], report: str, *, session: str | None) -> str:
    """Return the comment: the marker, a header stamped from grade.json, a blank line, then grade.md.

    The header is exactly:
        <!-- dovo-grade -->
        Graded commit: `<head>`
        Grade: <grade> · Score: <score> · Rubric: dovo-grade@1
        Dovo session: `<session>`     (only when session is given)
    """
    header = (
        f"{MARKER}\n"
        f"Graded commit: `{grade['head']}`\n"
        f"Grade: {grade['grade']} · Score: {grade['score']} · Rubric: {RUBRIC}\n"
    )
    if session is not None:
        header += f"{SESSION_LABEL}`{session}`\n"
    return f"{header}\n{report}"


def _read_json(path: Path) -> dict[str, object]:
    """Return the JSON object in path.

    Raises:
        PublishGradeError: When the file is missing, empty, malformed or not an object.
    """
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        raise PublishGradeError(f"Missing or empty {path}. Run /dovo-grade first.")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise PublishGradeError(f"{path} is not valid JSON: {error}") from error
    if not isinstance(document, dict):
        raise PublishGradeError(f"{path} must hold a JSON object.")
    return document


def read_grade() -> tuple[dict[str, object], str]:
    """Return grade.json and grade.md once both agree with each other, the rubric and grade-inputs.json.

    Raises:
        PublishGradeError: When a file is missing or the grade is inconsistent.
    """
    grade = _read_json(GRADE_JSON)
    inputs = _read_json(INPUTS_JSON)
    if not GRADE_MD.exists() or not GRADE_MD.read_text(encoding="utf-8").strip():
        raise PublishGradeError(f"Missing or empty {GRADE_MD}. Run /dovo-grade first.")
    report = GRADE_MD.read_text(encoding="utf-8")
    problem = grade_problem(grade, inputs) or report_problem(grade, report)
    if problem is not None:
        raise PublishGradeError(problem)
    return grade, report


def publish(pr: str, *, session: str | None) -> str:
    """Post or update the grade comment on pr, stamped with session when given, and return the line to report.

    Raises:
        PublishGradeError: If the grade is inconsistent, stale or too long to post.
    """
    grade, report = read_grade()
    view = json.loads(run("gh", "pr", "view", pr, "--json", "number,headRefOid,url"))
    number: int = view["number"]
    if grade["pr"] != number or grade["head"] != view["headRefOid"]:
        raise PublishGradeError(
            f"the grade covers #{grade['pr']} at {grade['head']}, but #{number} is at {view['headRefOid']}"
        )
    body = comment_body(grade, report, session=session)
    if len(body) > MAX_COMMENT_CHARS:
        raise PublishGradeError(f"the comment is {len(body)} characters; GitHub accepts at most {MAX_COMMENT_CHARS}")
    host, repository = pr_repository(view["url"])
    viewer = run("gh", "api", "--hostname", host, "user", "--jq", ".login")
    comment_id = find_own_comment(list_comments(host, repository, number), viewer, marker=MARKER)
    if comment_id is None:
        url = write_comment(host, [f"repos/{repository}/issues/{number}/comments"], body)
        return f"Posted grade comment: {url}\n"
    url = write_comment(host, ["-X", "PATCH", f"repos/{repository}/issues/comments/{comment_id}"], body)
    return f"Updated grade comment: {url}\n"


def check() -> str:
    """Validate the grade files and return the line to report.

    Raises:
        PublishGradeError: If the grade is inconsistent.
    """
    grade, _ = read_grade()
    return f"Grade is consistent: {grade['grade']} ({grade['score']}), caps {grade['caps'] or 'none'}\n"


def main() -> int:
    """Check .agentic/grade.json, then post it as the pull request's one dovo-grade comment unless --check."""
    parser = argparse.ArgumentParser(description="Check a /dovo-grade grade and post or update its PR comment.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--pr", help="Pull request number, URL or branch to post the grade to.")
    target.add_argument("--check", action="store_true", help="Only validate the grade files; post nothing.")
    parser.add_argument("--session", metavar="SESSION", help="Dovo session ID to stamp on the comment header.")
    args = parser.parse_args()
    session: str | None = args.session
    if session is not None and not session.strip():
        sys.stderr.write("publish_grade.py: --session must not be empty\n")
        return 1

    try:
        sys.stdout.write(check() if args.check else publish(args.pr, session=session))
    except PublishGradeError as error:
        sys.stderr.write(f"publish_grade.py: {error}\n")
        return 1
    except subprocess.CalledProcessError as error:
        command: list[str] = error.cmd
        stderr: str = error.stderr
        sys.stderr.write(f"publish_grade.py: command failed: {' '.join(command)}\n{stderr}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
