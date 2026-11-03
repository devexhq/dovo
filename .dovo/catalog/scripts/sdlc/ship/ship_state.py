#!/usr/bin/env python3
"""Deterministic state, stage dispatcher, transition recorder, publication gate, and reporter for ship."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Final

AGENTIC_DIR: Final[Path] = Path(".agentic")
PR_URL_FILE: Final[Path] = AGENTIC_DIR / "pr-url"
PR_TITLE_FILE: Final[Path] = AGENTIC_DIR / "pr-title"
PR_DESCRIPTION_FILE: Final[Path] = AGENTIC_DIR / "pr-description"
SHIP_DIR: Final[Path] = AGENTIC_DIR / "ship"
CONTRACT_JSON: Final[Path] = SHIP_DIR / "contract.json"
PROGRESS_JSON: Final[Path] = SHIP_DIR / "progress.json"
STAGE_JSON: Final[Path] = SHIP_DIR / "stage.json"
QUESTION_MD: Final[Path] = SHIP_DIR / "question.md"
VALIDATE_MD: Final[Path] = SHIP_DIR / "validate.md"
PUBLICATION_JSON: Final[Path] = SHIP_DIR / "publication.json"
PR_JSON: Final[Path] = SHIP_DIR / "pr.json"
RESULT_JSON: Final[Path] = SHIP_DIR / "result.json"
REPORT_MD: Final[Path] = SHIP_DIR / "report.md"
REPORTS_DIR: Final[Path] = SHIP_DIR / "reports"

STEP_ENV_PREFIXES: Final[tuple[str, ...]] = (
    "DOVO_STEP",
    "DOVO_STEPS",
    "DOVO_ITERATION",
    "DOVO_BLUEPRINT",
    "DOVO_PREVIOUS",
    "DOVO_TEMP",
    "DOVO_RUNNER",
    "DOVO_OUTPUT",
)

MAX_REVIEW_ROUNDS: Final[int] = 3
REVIEW_CHECK_COMMAND: Final[tuple[str, ...]] = ("uv", "run", "python", "scripts/publish_review.py", "--check")
ACTIONS: Final[tuple[str, ...]] = ("assess", "investigate", "plan", "implement", "validate", "review", "ask")
AGENT_AND_CHECK_ACTIONS: Final[frozenset[str]] = frozenset(
    {"assess", "investigate", "plan", "implement", "validate", "review"}
)
OUTCOMES: Final[tuple[str, ...]] = ("working", "ready", "no_action", "blocked", "incomplete")
STOP_ANSWERS: Final[frozenset[str]] = frozenset({"stop", "abort", "cancel"})

STAGE_NEXT_ACTIONS: Final[dict[str, frozenset[str]]] = {
    "assess": frozenset({"investigate", "plan", "implement", "validate", "review"}),
    "investigate": frozenset({"assess", "plan", "implement"}),
    "plan": frozenset({"implement"}),
    "implement": frozenset({"validate", "plan", "investigate"}),
}


class ShipStateError(RuntimeError):
    """Raised when a ship state precondition, artifact contract, or git/GitHub operation fails."""


def _run_cmd(*args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a subprocess command and return its captured text result."""
    try:
        return subprocess.run(args, cwd=cwd, check=check, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise ShipStateError(f"Command failed ({' '.join(args)}): {detail}") from exc


def _sha256_text(text: str) -> str:
    """Return the hex SHA-256 digest of UTF-8 encoded text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _atomic_write_text(path: Path, content: str) -> None:
    """Atomically write UTF-8 text to path via a sibling temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.name}.tmp")
    staging.write_text(content, encoding="utf-8")
    os.replace(staging, path)


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    """Atomically write a JSON object to path with trailing newline."""
    _atomic_write_text(path, json.dumps(payload, indent=2) + "\n")


def _load_json_dict(path: Path) -> dict[str, object]:
    """Read path and return its parsed JSON object, or raise ShipStateError."""
    if not path.is_file():
        raise ShipStateError(f"Missing required artifact '{path}'.")
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ShipStateError(f"Artifact '{path}' is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ShipStateError(f"Artifact '{path}' must be a JSON object.")
    return parsed


def _emit_outputs(outputs: dict[str, str]) -> None:
    """Write outputs as JSON to stdout and append key=value lines to $DOVO_OUTPUT when set."""
    sys.stdout.write(json.dumps(outputs) + "\n")
    target = os.environ.get("DOVO_OUTPUT")
    if not target:
        return
    with Path(target).open("a", encoding="utf-8") as handle:
        for key, value in outputs.items():
            clean = value.replace("\r", " ").replace("\n", " ")
            handle.write(f"{key}={clean}\n")


def _parse_issue_number(raw: str) -> int:
    """Validate and return a positive GitHub issue number."""
    cleaned = raw.strip().removeprefix("#")
    if not cleaned.isascii() or not cleaned.isdigit() or int(cleaned) <= 0:
        raise ShipStateError(f"Invalid issue number '{raw}'.")
    return int(cleaned)


def _gh_api_json(endpoint: str, *, check: bool = True) -> object | None:
    """GET a GitHub REST endpoint via `gh api` and return its parsed JSON, or None on a failed unchecked call."""
    res = _run_cmd("gh", "api", endpoint, check=check)
    if res.returncode != 0 or not res.stdout.strip():
        return None
    try:
        return json.loads(res.stdout)
    except ValueError as exc:
        raise ShipStateError(f"Invalid JSON from gh api {endpoint}: {exc}") from exc


def _fetch_issue(issue_number: int) -> dict[str, object]:
    """Fetch live issue metadata from the GitHub REST API via gh api."""
    data = _gh_api_json(f"repos/{{owner}}/{{repo}}/issues/{issue_number}")
    if not isinstance(data, dict):
        raise ShipStateError(f"Unexpected response for issue #{issue_number}.")
    body = str(data.get("body") or "")
    return {
        "number": issue_number,
        "title": str(data.get("title") or ""),
        "body": body,
        "body_sha": _sha256_text(body),
        "url": str(data.get("html_url") or ""),
    }


def _ref_field(data: dict[str, object], side: str, key: str) -> str:
    """Return data[side][key] from a REST pull-request payload's head/base object, or an empty string."""
    ref = data.get(side)
    return str(ref.get(key) or "") if isinstance(ref, dict) else ""


def _pr_dict(data: dict[str, object]) -> dict[str, object]:
    """Normalize a REST pull-request payload into contract.json's pull_request shape."""
    number = data.get("number")
    if not isinstance(number, int):
        raise ShipStateError("Pull request payload is missing integer 'number'.")
    return {
        "pr_number": number,
        "branch": _ref_field(data, "head", "ref"),
        "head_sha": _ref_field(data, "head", "sha"),
        "base_branch": _ref_field(data, "base", "ref") or "main",
        "url": str(data.get("html_url") or ""),
    }


def _mentions_issue(entry: dict[str, object], issue_number: int) -> bool:
    """Return whether a REST pull-request entry's body or head branch names issue_number."""
    body = str(entry.get("body") or "")
    branch = _ref_field(entry, "head", "ref")
    tokens = (f"#{issue_number}", f"issues/{issue_number}", f"GH-{issue_number}")
    if any(token in body for token in tokens):
        return True
    return str(issue_number) in branch.split("-") or branch.startswith(f"{issue_number}-")


def _list_open_pull_requests() -> list[dict[str, object]]:
    """Return every open pull request in the repository via the paginated REST pulls endpoint."""
    res = _run_cmd("gh", "api", "--paginate", "repos/{owner}/{repo}/pulls?state=open&per_page=100", "--jq", ".[]")
    rows: list[dict[str, object]] = []
    for line in res.stdout.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise ShipStateError(f"Invalid JSON from gh api pulls listing: {exc}") from exc
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _resolve_pull_request(issue_number: int, pr_arg: str) -> dict[str, object] | None:
    """Resolve an explicit PR or discover an open PR referencing issue_number."""
    cleaned = pr_arg.strip().removeprefix("#")
    if cleaned:
        data = _gh_api_json(f"repos/{{owner}}/{{repo}}/pulls/{_pr_number_from_ref(cleaned)}")
        if not isinstance(data, dict) or str(data.get("state", "")).lower() != "open":
            raise ShipStateError(f"Pull request #{cleaned} is not open.")
        return _pr_dict(data)
    for row in _list_open_pull_requests():
        if _mentions_issue(row, issue_number):
            return _pr_dict(row)
    return None


def _parse_worktree_porcelain(output: str) -> list[tuple[Path, str, str]]:
    """Parse `git worktree list --porcelain` into (path, head_sha, branch_ref) tuples."""
    entries: list[tuple[Path, str, str]] = []
    wt_path: Path | None = None
    head_sha = ""
    branch_ref = ""
    for line in [*output.splitlines(), ""]:
        if not line.strip():
            if wt_path is not None:
                entries.append((wt_path, head_sha, branch_ref))
            wt_path, head_sha, branch_ref = None, "", ""
        elif line.startswith("worktree "):
            wt_path = Path(line.removeprefix("worktree ").strip()).resolve()
        elif line.startswith("HEAD "):
            head_sha = line.removeprefix("HEAD ").strip()
        elif line.startswith("branch "):
            branch_ref = line.removeprefix("branch ").strip()
    return entries


def _check_kept_worktree_clean_and_synced(wt_path: Path, branch: str, pr_head_sha: str) -> None:
    """Refuse when another worktree holding branch has uncommitted changes or unpushed commits."""
    status = _run_cmd("git", "status", "--porcelain", "--", ".", ":!.dovo/run", cwd=wt_path, check=False)
    if status.returncode != 0 or status.stdout.strip():
        raise ShipStateError(
            f"Kept worktree at '{wt_path}' has branch '{branch}' checked out with uncommitted changes. "
            f"Fix: inspect or clean '{wt_path}' (or remove it with 'dovo worktree delete') before continuing PR."
        )
    wt_head = _run_cmd("git", "rev-parse", "HEAD", cwd=wt_path).stdout.strip()
    if wt_head != pr_head_sha:
        raise ShipStateError(
            f"Kept worktree at '{wt_path}' has branch '{branch}' checked out at local commit {wt_head[:12]}, "
            f"which differs from remote PR head {pr_head_sha[:12]}. "
            f"Fix: push or reconcile '{wt_path}' (or remove it with 'dovo worktree delete') before continuing PR."
        )


def _ensure_current_worktree_clean() -> None:
    """Refuse to reset the current checkout when it has uncommitted changes outside .agentic/ and .dovo/run."""
    status = _run_cmd("git", "status", "--porcelain", "--", ".", ":!.dovo/run", ":!.agentic", check=False)
    if status.returncode != 0 or status.stdout.strip():
        raise ShipStateError("Current checkout has uncommitted changes; refusing to overwrite local work.")


def _align_checkout_with_pr(pr: dict[str, object]) -> str:
    """Verify no kept worktree conflicts on the PR branch, then fetch and reset HEAD to FETCH_HEAD."""
    branch = str(pr["branch"])
    pr_head_sha = str(pr["head_sha"])
    _ensure_current_worktree_clean()
    cwd = Path.cwd().resolve()
    porcelain = _run_cmd("git", "worktree", "list", "--porcelain").stdout
    target_ref = f"refs/heads/{branch}"
    for wt_path, _, branch_ref in _parse_worktree_porcelain(porcelain):
        if wt_path != cwd and branch_ref == target_ref:
            _check_kept_worktree_clean_and_synced(wt_path, branch, pr_head_sha)
    _run_cmd("git", "fetch", "-q", "origin", target_ref)
    _run_cmd("git", "reset", "-q", "--hard", "FETCH_HEAD")
    head = _current_head_sha()
    if head != pr_head_sha:
        raise ShipStateError(
            f"Fetched PR #{pr['pr_number']} branch '{branch}' at {head[:12]}, expected {pr_head_sha[:12]}."
        )
    return head


def _current_head_sha() -> str:
    """Return the current git HEAD commit SHA."""
    return _run_cmd("git", "rev-parse", "HEAD").stdout.strip()


def initial_progress(issue_number: int, body_sha: str, head_sha: str) -> dict[str, object]:
    """Return a fresh progress.json structure for issue_number."""
    return {
        "schema_version": 1,
        "issue": issue_number,
        "body_sha": body_sha,
        "head_sha": head_sha,
        "outcome": "working",
        "next_action": "assess",
        "review_rounds": 0,
        "pending_questions": [],
        "answered_questions": [],
        "history": [],
        "failure_fingerprints": [],
        "last_validation": None,
        "last_review": None,
        "blocker": "",
        "summary": "",
    }


def _compatible_existing_progress(issue_number: int, body_sha: str) -> dict[str, object] | None:
    """Return existing progress.json when it matches issue_number and body_sha, else None."""
    if not PROGRESS_JSON.is_file():
        return None
    progress = _load_json_dict(PROGRESS_JSON)
    if progress.get("issue") == issue_number and progress.get("body_sha") == body_sha:
        return progress
    return None


def cmd_prepare(issue_raw: str, pr_raw: str) -> int:
    """Execute `ship/prepare`: capture contract, align checkout, and initialize or preserve progress."""
    issue_number = _parse_issue_number(issue_raw)
    issue = _fetch_issue(issue_number)
    pr = _resolve_pull_request(issue_number, pr_raw)
    body_sha = str(issue["body_sha"])
    existing = _compatible_existing_progress(issue_number, body_sha)
    if pr is not None:
        head_sha = _align_checkout_with_pr(pr)
    else:
        if existing is None:
            _ensure_current_worktree_clean()
            _run_cmd("git", "fetch", "-q", "origin", "main", check=False)
        head_sha = _current_head_sha()
    contract: dict[str, object] = {
        "schema_version": 1,
        "issue": issue,
        "pull_request": pr,
        "base_sha": head_sha,
        "head_sha": head_sha,
    }
    _atomic_write_json(CONTRACT_JSON, contract)
    progress = existing or initial_progress(issue_number, body_sha, head_sha)
    progress["head_sha"] = head_sha
    _atomic_write_json(PROGRESS_JSON, progress)
    _emit_outputs(
        {
            "issue": str(issue_number),
            "body_sha": body_sha,
            "head_sha": head_sha,
            "pr_number": str(pr["pr_number"]) if pr is not None else "",
        }
    )
    return 0


def _first_unanswered_question(progress: dict[str, object]) -> dict[str, object] | None:
    """Return the first unanswered question dict in progress['pending_questions'], or None."""
    pending = progress.get("pending_questions")
    if not isinstance(pending, list):
        return None
    for item in pending:
        if isinstance(item, dict) and not item.get("answer"):
            return item
    return None


def _render_question_markdown(question: dict[str, object]) -> str:
    """Render the markdown artifact for a pending human question."""
    explicit = str(question.get("report_markdown", "")).strip()
    if explicit:
        return explicit + "\n"
    lines = [
        f"# Decision Required (`{question.get('id', '')}`)",
        "",
        f"**Question:** {question.get('question', '')}",
    ]
    for label, key in (
        ("Evidence", "evidence"),
        ("Recommendation", "recommendation"),
        ("Alternatives", "alternatives"),
        ("Affected scope", "affected_scope"),
    ):
        val = str(question.get(key, "")).strip()
        if val:
            lines.extend(["", f"**{label}:** {val}"])
    return "\n".join(lines) + "\n"


def cmd_next() -> int:
    """Execute `ship/next`: remove stale stage.json and output the next stage or pending question."""
    STAGE_JSON.unlink(missing_ok=True)
    progress = _load_json_dict(PROGRESS_JSON)
    pending = _first_unanswered_question(progress)
    if pending is not None:
        qid = str(pending.get("id", ""))
        qtext = str(pending.get("question", ""))
        if not qid or not qtext:
            raise ShipStateError("Pending question in progress.json is missing 'id' or 'question'.")
        _atomic_write_text(QUESTION_MD, _render_question_markdown(pending))
        _emit_outputs({"action": "ask", "question_id": qid, "question": qtext})
        return 0
    QUESTION_MD.unlink(missing_ok=True)
    action = str(progress.get("next_action", ""))
    if action not in AGENT_AND_CHECK_ACTIONS:
        raise ShipStateError(f"Invalid next_action '{action}' in '{PROGRESS_JSON}'.")
    _emit_outputs({"action": action, "question_id": "", "question": ""})
    return 0


def _is_stop_answer(answer: str) -> bool:
    """Return whether a human answer explicitly requests stopping the run."""
    lowered = answer.strip().lower()
    return lowered in STOP_ANSWERS or lowered.startswith("stop:")


def _record_ask_answer(progress: dict[str, object], question_id: str, answer: str) -> tuple[str, str]:
    """Bind answer to question_id in progress and return (outcome, next_action)."""
    cleaned_id = question_id.strip()
    cleaned_answer = answer.strip()
    if not cleaned_id or not cleaned_answer:
        raise ShipStateError("Recording an 'ask' action requires non-empty SHIP_QUESTION_ID and SHIP_ANSWER.")
    pending = _first_unanswered_question(progress)
    if pending is None or str(pending.get("id", "")) != cleaned_id:
        raise ShipStateError(f"Question ID '{cleaned_id}' does not match the current pending question.")
    pending["answer"] = cleaned_answer
    answered = progress.setdefault("answered_questions", [])
    if isinstance(answered, list):
        answered.append(dict(pending))
    if _is_stop_answer(cleaned_answer):
        progress["blocker"] = f"Stopped by user on question '{cleaned_id}': {cleaned_answer}"
        progress["summary"] = str(progress["blocker"])
        return "blocked", ""
    next_pending = _first_unanswered_question(progress)
    if next_pending is not None:
        return "working", "ask"
    origin = str(pending.get("origin_stage", "assess"))
    progress["pending_questions"] = []
    return "working", origin


def _archive_stage_report(stage_data: dict[str, object], seq: int, action: str) -> str:
    """Copy a stage's report file into .agentic/ship/reports/ when present and return its path."""
    raw_report = str(stage_data.get("report_path", "")).strip()
    if not raw_report:
        return ""
    source = Path(raw_report)
    if not source.is_file():
        raise ShipStateError(f"Stage '{action}' declared missing report_path '{raw_report}'.")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix or ".md"
    dest = REPORTS_DIR / f"{seq:02d}-{action}{suffix}"
    if source.resolve() != dest.resolve():
        shutil.copyfile(source, dest)
    return dest.as_posix()


def _validate_stage_envelope(
    stage_data: dict[str, object], action: str, contract: dict[str, object], head_sha: str
) -> None:
    """Verify stage.json matches the active stage, live contract body_sha, and current HEAD SHA."""
    if stage_data.get("stage") != action:
        raise ShipStateError(f"stage.json stage '{stage_data.get('stage')}' does not match active action '{action}'.")
    issue = contract.get("issue")
    expected_body_sha = str(issue.get("body_sha", "")) if isinstance(issue, dict) else ""
    if str(stage_data.get("body_sha", "")) != expected_body_sha:
        raise ShipStateError(
            f"stage.json body_sha '{stage_data.get('body_sha')}' does not match contract body_sha '{expected_body_sha}'."
        )
    if str(stage_data.get("head_sha", "")) != head_sha:
        raise ShipStateError(
            f"stage.json head_sha '{stage_data.get('head_sha')}' does not match current HEAD '{head_sha}'."
        )


def _normalize_question_entry(item: object, origin: str) -> dict[str, object]:
    """Validate and normalize one stage question item."""
    if not isinstance(item, dict):
        raise ShipStateError(f"Question entry in stage '{origin}' is not a JSON object.")
    qid = str(item.get("id", "")).strip()
    qtext = str(item.get("question", "")).strip()
    if not qid or not qtext:
        raise ShipStateError(f"Question entry in stage '{origin}' requires non-empty 'id' and 'question'.")
    return {
        "id": qid,
        "origin_stage": origin,
        "question": qtext,
        "evidence": str(item.get("evidence", "")),
        "recommendation": str(item.get("recommendation", "")),
        "alternatives": str(item.get("alternatives", "")),
        "affected_scope": str(item.get("affected_scope", "")),
        "report_markdown": str(item.get("report_markdown", "")),
        "answer": None,
    }


def _queue_stage_questions(progress: dict[str, object], stage_data: dict[str, object], origin: str) -> None:
    """Validate and queue pending human questions raised by a stage."""
    raw_questions = stage_data.get("questions")
    if not isinstance(raw_questions, list) or not raw_questions:
        raise ShipStateError(f"Stage '{origin}' returned disposition 'ask' without a non-empty 'questions' list.")
    progress["pending_questions"] = [_normalize_question_entry(item, origin) for item in raw_questions]


def _record_fingerprint(progress: dict[str, object], fingerprint: str) -> bool:
    """Append fingerprint to progress and return True when the same fingerprint already occurred."""
    seen = progress.setdefault("failure_fingerprints", [])
    if not isinstance(seen, list):
        seen = []
        progress["failure_fingerprints"] = seen
    repeated = fingerprint in seen
    seen.append(fingerprint)
    return repeated


def _has_matching_stage_status(record: object, status: str, head_sha: str) -> bool:
    """Return whether record is a dict with matching status and head_sha."""
    return isinstance(record, dict) and record.get("status") == status and record.get("head_sha") == head_sha


def _require_non_empty_summary(summary: str, stage: str, disposition: str) -> None:
    """Raise ShipStateError when summary is blank."""
    if not summary:
        raise ShipStateError(f"Stage '{stage}' with disposition '{disposition}' requires a non-empty 'summary'.")


def _transition_validate(
    progress: dict[str, object], stage_data: dict[str, object], disposition: str, head_sha: str
) -> tuple[str, str]:
    """Compute (outcome, next_action) for a `validate` stage result."""
    summary = str(stage_data.get("summary", "")).strip()
    if disposition == "passed":
        progress["last_validation"] = {"head_sha": head_sha, "status": "passed", "summary": summary}
        if _has_matching_stage_status(progress.get("last_review"), "ready", head_sha):
            return "ready", ""
        return "working", "review"
    if disposition == "checks_failed":
        _require_non_empty_summary(summary, "validate", "checks_failed")
        progress["last_validation"] = {"head_sha": head_sha, "status": "checks_failed", "summary": summary}
        fingerprint = f"validate:{head_sha}:{_sha256_text(summary)[:16]}"
        if _record_fingerprint(progress, fingerprint):
            progress["summary"] = f"Repeated validation failure at {head_sha[:12]} without new evidence: {summary}"
            return "incomplete", ""
        return "working", "implement"
    raise ShipStateError(f"Unsupported disposition '{disposition}' for stage 'validate'.")


def _int_field(mapping: dict[str, object], key: str) -> int:
    """Return mapping[key] when it is an int (and not bool), else 0."""
    value = mapping.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _record_review_changes_requested(progress: dict[str, object], summary: str, head_sha: str) -> tuple[str, str]:
    """Record a changes_requested review verdict and enforce the 3-round and non-progress limits."""
    _require_non_empty_summary(summary, "review", "changes_requested")
    rounds = _int_field(progress, "review_rounds") + 1
    progress["review_rounds"] = rounds
    progress["last_review"] = {"head_sha": head_sha, "status": "changes_requested", "summary": summary}
    fingerprint = f"review:{head_sha}:{_sha256_text(summary)[:16]}"
    repeated = _record_fingerprint(progress, fingerprint)
    if rounds <= MAX_REVIEW_ROUNDS and not repeated:
        return "working", "implement"
    reason = "repeated identical review findings" if repeated else f"exceeded {MAX_REVIEW_ROUNDS} review rounds"
    progress["summary"] = f"Review/fix loop incomplete ({reason}): {summary}"
    return "incomplete", ""


def _review_record_problem() -> str | None:
    """Return why scripts/publish_review.py would refuse .agentic/review.json and review.md, or None when it would publish them."""
    res = _run_cmd(*REVIEW_CHECK_COMMAND, check=False)
    if res.returncode == 0:
        return None
    return (res.stderr or res.stdout).strip() or f"scripts/publish_review.py --check exited {res.returncode}"


def _record_rejected_review(progress: dict[str, object], problem: str, head_sha: str) -> tuple[str, str]:
    """Record a ready review the publisher refuses and send it back to `review`, or end incomplete on a repeat."""
    progress["last_review"] = {"head_sha": head_sha, "status": "rejected", "summary": problem}
    fingerprint = f"review_record:{head_sha}:{_sha256_text(problem)[:16]}"
    if _record_fingerprint(progress, fingerprint):
        progress["summary"] = f"Review record rejected twice at {head_sha[:12]}: {problem}"
        return "incomplete", ""
    return "working", "review"


def _transition_review(
    progress: dict[str, object], stage_data: dict[str, object], disposition: str, head_sha: str
) -> tuple[str, str]:
    """Compute (outcome, next_action) for a `review` stage result.

    A ready review is first checked with `scripts/publish_review.py --check`, so one publish would refuse
    goes back to `review`, where it can still be corrected, instead of failing at publish.
    """
    summary = str(stage_data.get("summary", "")).strip()
    if disposition in ("ready", "approved"):
        problem = _review_record_problem()
        if problem is not None:
            return _record_rejected_review(progress, problem, head_sha)
        progress["last_review"] = {"head_sha": head_sha, "status": "ready", "summary": summary}
        if _has_matching_stage_status(progress.get("last_validation"), "passed", head_sha):
            return "ready", ""
        return "working", "validate"
    if disposition == "changes_requested":
        return _record_review_changes_requested(progress, summary, head_sha)
    if disposition in ("plan", "investigate"):
        return "working", disposition
    raise ShipStateError(f"Unsupported disposition '{disposition}' for stage 'review'.")


def _transition_simple_stage(action: str, disposition: str) -> tuple[str, str]:
    """Validate and map a non-terminal routing disposition for assess/investigate/plan/implement."""
    normalized = "validate" if action == "implement" and disposition == "completed" else disposition
    if normalized not in STAGE_NEXT_ACTIONS.get(action, frozenset()):
        raise ShipStateError(f"Unsupported disposition '{disposition}' for stage '{action}'.")
    return "working", normalized


def _transition_stage(
    progress: dict[str, object], action: str, stage_data: dict[str, object], head_sha: str
) -> tuple[str, str]:
    """Validate a non-ask stage disposition and return (outcome, next_action)."""
    disposition = str(stage_data.get("disposition", "")).strip()
    summary = str(stage_data.get("summary", "")).strip()
    if disposition in ("ask", "needs_human"):
        _queue_stage_questions(progress, stage_data, action)
        return "working", "ask"
    if disposition in ("blocked", "split_needed"):
        _require_non_empty_summary(summary, action, disposition)
        progress["blocker"] = summary
        progress["summary"] = summary
        return "blocked", ""
    if action == "assess" and disposition == "no_action":
        _require_non_empty_summary(summary, "assess", "no_action")
        progress["summary"] = summary
        return "no_action", ""
    if action == "validate":
        return _transition_validate(progress, stage_data, disposition, head_sha)
    if action == "review":
        return _transition_review(progress, stage_data, disposition, head_sha)
    return _transition_simple_stage(action, disposition)


def cmd_record(action_raw: str, question_id_raw: str, answer_raw: str) -> int:
    """Execute `ship/record`: validate stage.json or ask answer, update progress.json, and output outcome."""
    action = action_raw.strip()
    if action not in ACTIONS:
        raise ShipStateError(f"Invalid SHIP_ACTION '{action}'.")
    contract = _load_json_dict(CONTRACT_JSON)
    progress = _load_json_dict(PROGRESS_JSON)
    head_sha = _current_head_sha()
    history = progress.setdefault("history", [])
    seq = (len(history) if isinstance(history, list) else 0) + 1
    if action == "ask":
        outcome, next_action = _record_ask_answer(progress, question_id_raw, answer_raw)
        entry: dict[str, object] = {
            "seq": seq,
            "stage": "ask",
            "question_id": question_id_raw.strip(),
            "head_sha": head_sha,
            "outcome": outcome,
            "next_action": next_action,
        }
    else:
        stage_data = _load_json_dict(STAGE_JSON)
        _validate_stage_envelope(stage_data, action, contract, head_sha)
        archived = _archive_stage_report(stage_data, seq, action)
        outcome, next_action = _transition_stage(progress, action, stage_data, head_sha)
        entry = {
            "seq": seq,
            "stage": action,
            "disposition": str(stage_data.get("disposition", "")),
            "summary": str(stage_data.get("summary", "")),
            "head_sha": head_sha,
            "report_path": archived,
            "outcome": outcome,
            "next_action": next_action,
        }
        STAGE_JSON.unlink(missing_ok=True)
    if isinstance(history, list):
        history.append(entry)
    progress["head_sha"] = head_sha
    progress["outcome"] = outcome
    progress["next_action"] = next_action
    _atomic_write_json(PROGRESS_JSON, progress)
    _emit_outputs({"outcome": outcome, "next_action": next_action})
    return 0


def _expected_body_sha(contract: dict[str, object]) -> str:
    """Return issue.body_sha from contract.json."""
    issue = contract.get("issue")
    return str(issue.get("body_sha", "")) if isinstance(issue, dict) else ""


def _evaluate_publication_gate(
    contract: dict[str, object], progress: dict[str, object], live_body_sha: str, head_sha: str
) -> tuple[bool, str, str]:
    """Return (permitted, outcome, reason) for `ship/publication-gate`."""
    recorded_outcome = str(progress.get("outcome", "working"))
    if recorded_outcome == "working":
        return False, "incomplete", "Delivery loop reached max_iterations without a terminal outcome."
    if recorded_outcome != "ready":
        reason = str(progress.get("blocker") or progress.get("summary") or f"Run ended with {recorded_outcome}.")
        return False, recorded_outcome, reason
    if live_body_sha != _expected_body_sha(contract):
        return False, "blocked", "Live GitHub issue body changed since prepare; reassessment required."
    val_ok = _has_matching_stage_status(progress.get("last_validation"), "passed", head_sha)
    rev_ok = _has_matching_stage_status(progress.get("last_review"), "ready", head_sha)
    if not val_ok or not rev_ok:
        return False, "incomplete", f"Current HEAD {head_sha[:12]} lacks matching passed validation and ready review."
    return True, "ready", "Contract, HEAD commit, validation, and review agree."


def cmd_publication_gate() -> int:
    """Execute `ship/publication-gate`: verify contract, HEAD, validation, and review before publishing."""
    contract = _load_json_dict(CONTRACT_JSON)
    progress = _load_json_dict(PROGRESS_JSON)
    issue = contract.get("issue")
    if not isinstance(issue, dict) or not isinstance(issue.get("number"), int):
        raise ShipStateError("contract.json is missing issue.number.")
    live_issue = _fetch_issue(int(issue["number"]))
    live_body_sha = str(live_issue["body_sha"])
    head_sha = _current_head_sha()
    permitted, outcome, reason = _evaluate_publication_gate(contract, progress, live_body_sha, head_sha)
    if progress.get("outcome") != outcome:
        progress["outcome"] = outcome
        if not progress.get("summary"):
            progress["summary"] = reason
        _atomic_write_json(PROGRESS_JSON, progress)
    publication: dict[str, object] = {
        "schema_version": 1,
        "permitted": permitted,
        "outcome": outcome,
        "head_sha": head_sha,
        "body_sha": str(issue.get("body_sha", "")),
        "live_body_sha": live_body_sha,
        "reason": reason,
    }
    _atomic_write_json(PUBLICATION_JSON, publication)
    _emit_outputs({"outcome": outcome})
    return 0


def _sanitized_gate_env() -> dict[str, str]:
    """Return an environment mapping with per-step DOVO_* variables removed and terminal width set."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(STEP_ENV_PREFIXES)}
    env["COLUMNS"] = "160"
    env["PYTHONIOENCODING"] = "utf-8"
    if env.get("TERM") in (None, "", "dumb"):
        env["TERM"] = "xterm-256color"
    return env


def _changed_python_files(base_ref: str) -> list[str]:
    """Return sorted changed or untracked Python file paths relative to merge-base with base_ref."""
    mb = _run_cmd("git", "merge-base", base_ref, "HEAD", check=False)
    base = mb.stdout.strip() if mb.returncode == 0 and mb.stdout.strip() else base_ref
    diff = _run_cmd("git", "diff", "--name-only", "--diff-filter=ACMR", base, check=False).stdout.splitlines()
    untracked = _run_cmd("git", "ls-files", "--others", "--exclude-standard", check=False).stdout.splitlines()
    paths = {p.strip() for p in [*diff, *untracked] if p.strip().endswith(".py") and Path(p.strip()).is_file()}
    return sorted(paths)


def _quality_gate_commands(base_ref: str, site_dir: Path, pytest_basetemp: Path) -> list[tuple[str, tuple[str, ...]]]:
    """Build the ordered quality gate (label, command_tuple) pairs."""
    gates: list[tuple[str, tuple[str, ...]]] = [
        ("ruff format", ("uv", "run", "ruff", "format", "--check", ".")),
        ("ruff check", ("uv", "run", "ruff", "check", ".")),
        ("ty check", ("uv", "run", "ty", "check", "src", "tests", "e2e", ".dovo/catalog/scripts")),
        ("mkdocs build", ("uv", "run", "python", "-m", "mkdocs", "build", "--strict", "--site-dir", str(site_dir))),
    ]
    changed_py = _changed_python_files(base_ref)
    if changed_py:
        gates.append(
            (
                "complexipy",
                ("uv", "run", "complexipy", *changed_py, "--max-complexity-allowed", "10", "--failed", "--plain"),
            )
        )
    gates.append(
        (
            "pytest",
            (
                "uv",
                "run",
                "python",
                "-m",
                "pytest",
                "tests/",
                "--cov=dovo",
                "--cov-report=term-missing",
                f"--basetemp={pytest_basetemp}",
            ),
        )
    )
    return gates


def _run_one_gate(label: str, cmd: tuple[str, ...], env: dict[str, str]) -> tuple[str | None, list[str]]:
    """Run one gate command and return (failure_label_or_None, markdown_lines)."""
    res = subprocess.run(cmd, check=False, capture_output=True, text=True, env=env)
    Path("complexipy-results.json").unlink(missing_ok=True)
    status = "PASSED" if res.returncode == 0 else f"FAILED (exit {res.returncode})"
    failure = None if res.returncode == 0 else f"{label} (exit {res.returncode})"
    combined = "\n".join(part.strip() for part in (res.stdout, res.stderr) if part and part.strip())
    lines = [f"## {label} — {status}", "", f"Command: `{' '.join(cmd)}`", ""]
    if combined:
        lines.extend(["```text", combined[-4000:], "```", ""])
    return failure, lines


def _run_gate_commands(gates: list[tuple[str, tuple[str, ...]]], env: dict[str, str]) -> tuple[list[str], list[str]]:
    """Execute each gate command and return (failed_labels, markdown_sections)."""
    failed: list[str] = []
    sections: list[str] = ["# Ship Validation Report", ""]
    for label, cmd in gates:
        failure, lines = _run_one_gate(label, cmd, env)
        if failure is not None:
            failed.append(failure)
        sections.extend(lines)
    return failed, sections


def cmd_validate(base_ref: str) -> int:
    """Execute `ship/validate`: run repository quality gates and write validate.md and stage.json."""
    contract = _load_json_dict(CONTRACT_JSON)
    body_sha = _expected_body_sha(contract)
    if not body_sha:
        raise ShipStateError("contract.json is missing issue.body_sha.")
    head_sha = _current_head_sha()
    env = _sanitized_gate_env()
    with tempfile.TemporaryDirectory(prefix="dovo-ship-validate-") as tmp:
        tmp_root = Path(tmp)
        gates = _quality_gate_commands(base_ref, tmp_root / "site", tmp_root / "pytest")
        failed, sections = _run_gate_commands(gates, env)
    disposition = "checks_failed" if failed else "passed"
    summary = f"Failed quality gates: {', '.join(failed)}" if failed else "All quality gates passed."
    _atomic_write_text(VALIDATE_MD, "\n".join(sections).rstrip() + "\n")
    _atomic_write_json(
        STAGE_JSON,
        {
            "stage": "validate",
            "disposition": disposition,
            "summary": summary,
            "body_sha": body_sha,
            "head_sha": head_sha,
            "report_path": VALIDATE_MD.as_posix(),
        },
    )
    _emit_outputs({"disposition": disposition, "head_sha": head_sha})
    return 0


def _verify_publication_permitted(publication: dict[str, object], head_sha: str) -> None:
    """Verify publication.json permits publishing at current HEAD and the working tree is clean."""
    if publication.get("permitted") is not True or publication.get("outcome") != "ready":
        raise ShipStateError("Publication is not permitted by '.agentic/ship/publication.json'.")
    gated_sha = str(publication.get("head_sha", "")).strip()
    if gated_sha != head_sha:
        raise ShipStateError(f"Current HEAD '{head_sha}' does not match gated head_sha '{gated_sha}'.")
    _ensure_current_worktree_clean()


def _pr_number_from_ref(pr_ref: str) -> str:
    """Return the pull-request number from a number, '#<n>' or pull-request URL reference."""
    cleaned = pr_ref.strip().rstrip("/").removeprefix("#")
    number = cleaned.rsplit("/pull/", 1)[-1] if "/pull/" in cleaned else cleaned
    if not number.isascii() or not number.isdigit():
        raise ShipStateError(f"Invalid pull request reference '{pr_ref}'.")
    return number


def _fetch_pr_metadata(pr_ref: str) -> dict[str, object] | None:
    """Return normalized PR metadata via the REST pulls endpoint, or None when lookup fails."""
    try:
        parsed = _gh_api_json(f"repos/{{owner}}/{{repo}}/pulls/{_pr_number_from_ref(pr_ref)}", check=False)
    except ShipStateError:
        return None
    return _pr_dict(parsed) if isinstance(parsed, dict) else None


def _sync_pr_metadata_if_present(pr_number: int) -> None:
    """Update PR title and description via the REST pulls endpoint when .agentic/pr-title and pr-description exist."""
    if not PR_TITLE_FILE.is_file() or not PR_DESCRIPTION_FILE.is_file():
        return
    title = PR_TITLE_FILE.read_text(encoding="utf-8").strip()
    description = PR_DESCRIPTION_FILE.read_text(encoding="utf-8").strip()
    if not title or not description:
        return
    edit = _run_cmd(
        "gh",
        "api",
        "--method",
        "PATCH",
        f"repos/{{owner}}/{{repo}}/pulls/{pr_number}",
        "-f",
        f"title={title}",
        "-f",
        f"body={description}",
        "--silent",
        check=False,
    )
    if edit.returncode != 0 and _fetch_pr_metadata(str(pr_number)) is None:
        detail = (edit.stderr or edit.stdout).strip()
        raise ShipStateError(f"Failed to update PR #{pr_number}: {detail}")


def _publish_existing_pr(existing_pr: dict[str, object], head_sha: str) -> dict[str, object]:
    """Push HEAD to an existing PR branch via explicit refspec and reconcile remote state on failure."""
    pr_number = _int_field(existing_pr, "pr_number")
    branch = str(existing_pr.get("branch", "")).strip()
    if pr_number <= 0 or not branch:
        raise ShipStateError("contract.json pull_request is missing 'pr_number' or 'branch'.")
    push = _run_cmd("git", "push", "origin", f"HEAD:refs/heads/{branch}", check=False)
    remote = _fetch_pr_metadata(str(pr_number))
    if push.returncode != 0 and (remote is None or str(remote.get("head_sha", "")) != head_sha):
        detail = (push.stderr or push.stdout).strip()
        raise ShipStateError(f"git push to '{branch}' failed: {detail}")
    _sync_pr_metadata_if_present(pr_number)
    resolved = remote or existing_pr
    url = str(resolved.get("url", existing_pr.get("url", ""))).strip()
    _atomic_write_text(PR_URL_FILE, f"{url}\n")
    return {"pr_number": pr_number, "url": url, "branch": branch, "head_sha": head_sha}


def _reconcile_or_raise_new_pr(issue_number: int, head_sha: str, error_detail: str) -> dict[str, object]:
    """Check whether a PR for issue_number or current HEAD already landed on GitHub after an ambiguous pr.py failure."""
    discovered = _resolve_pull_request(issue_number, "")
    if discovered is not None and str(discovered.get("head_sha", "")) == head_sha:
        url = str(discovered.get("url", "")).strip()
        _atomic_write_text(PR_URL_FILE, f"{url}\n")
        return {
            "pr_number": _int_field(discovered, "pr_number"),
            "url": url,
            "branch": str(discovered.get("branch", "")),
            "head_sha": head_sha,
        }
    raise ShipStateError(f"scripts/pr.py failed and no matching remote PR was found: {error_detail}")


def _publish_new_pr(issue_number: int, head_sha: str) -> dict[str, object]:
    """Create or reuse a PR via scripts/pr.py and reconcile remote state on ambiguous failure."""
    res = _run_cmd("uv", "run", "python", "scripts/pr.py", check=False)
    if res.returncode != 0:
        detail = (res.stderr or res.stdout).strip()
        return _reconcile_or_raise_new_pr(issue_number, head_sha, detail)
    pr_url = PR_URL_FILE.read_text(encoding="utf-8").splitlines()[0].strip() if PR_URL_FILE.is_file() else ""
    if not pr_url:
        raise ShipStateError("scripts/pr.py succeeded but '.agentic/pr-url' is missing or empty.")
    meta = _fetch_pr_metadata(pr_url)
    return {
        "pr_number": _int_field(meta, "pr_number") if isinstance(meta, dict) else 0,
        "url": pr_url,
        "branch": str(meta.get("branch", "")) if isinstance(meta, dict) else "",
        "head_sha": head_sha,
    }


def _publish_review_comment(pr_url: str) -> str:
    """Post the structured review via scripts/publish_review.py and return the comment URL."""
    cmd = ["uv", "run", "python", "scripts/publish_review.py", "--pr", pr_url]
    session_id = os.environ.get("DOVO_SESSION_ID", "").strip()
    if session_id:
        cmd.extend(["--session", session_id])
    res = _run_cmd(*cmd, check=False)
    if res.returncode != 0:
        detail = (res.stderr or res.stdout).strip()
        raise ShipStateError(f"scripts/publish_review.py failed: {detail}")
    for line in res.stdout.splitlines():
        if "http://" in line or "https://" in line:
            return line.split()[-1].strip()
    return pr_url


def cmd_publish() -> int:
    """Execute `ship/publish`: verify publication gate, push and create/update PR, post review, and write pr.json."""
    contract = _load_json_dict(CONTRACT_JSON)
    publication = _load_json_dict(PUBLICATION_JSON)
    head_sha = _current_head_sha()
    _verify_publication_permitted(publication, head_sha)
    issue_number = _contract_issue_number(contract)
    existing_pr = contract.get("pull_request")
    if isinstance(existing_pr, dict):
        pr_info = _publish_existing_pr(existing_pr, head_sha)
    else:
        pr_info = _publish_new_pr(issue_number, head_sha)
    pr_url = str(pr_info.get("url", "")).strip()
    comment_url = _publish_review_comment(pr_url)
    pr_record: dict[str, object] = {
        "pr_number": _int_field(pr_info, "pr_number"),
        "url": pr_url,
        "branch": str(pr_info.get("branch", "")),
        "head_sha": head_sha,
        "review_comment_url": comment_url,
    }
    _atomic_write_json(PR_JSON, pr_record)
    _emit_outputs({"pr_number": str(pr_record["pr_number"]), "url": pr_url, "head_sha": head_sha})
    return 0


def _require_published_pr(head_sha: str) -> dict[str, object]:
    """Load and verify .agentic/ship/pr.json when publication was permitted."""
    pr_data = _load_json_dict(PR_JSON)
    url = str(pr_data.get("url", "")).strip()
    pr_head = str(pr_data.get("head_sha", "")).strip()
    if not url:
        raise ShipStateError(f"'{PR_JSON}' is missing a non-empty pull request 'url'.")
    if pr_head and pr_head != head_sha:
        raise ShipStateError(f"'{PR_JSON}' head_sha '{pr_head}' does not match gated HEAD '{head_sha}'.")
    return pr_data


def _format_report_markdown(result: dict[str, object], progress: dict[str, object]) -> str:
    """Format the human-readable .agentic/ship/report.md summary."""
    lines = [
        f"# Ship Delivery Report — Issue #{result['issue']}",
        "",
        f"- **Status:** `{result['status']}`",
        f"- **Outcome:** `{result['outcome']}`",
        f"- **HEAD SHA:** `{result['head_sha']}`",
        f"- **Validation:** `{result['validation_status']}`",
        f"- **Review:** `{result['review_status']}` (rounds: {result['review_rounds']})",
    ]
    if result.get("pr_url"):
        lines.append(f"- **Pull Request:** {result['pr_url']}")
    if result.get("summary"):
        lines.extend(["", "## Summary", "", str(result["summary"])])
    answered = progress.get("answered_questions")
    if isinstance(answered, list) and answered:
        lines.extend(["", "## Settled Human Decisions", ""])
        for item in answered:
            if isinstance(item, dict):
                lines.append(f"- **`{item.get('id', '')}`:** {item.get('question', '')} → `{item.get('answer', '')}`")
    if result.get("next_action"):
        lines.extend(["", "## Next Action", "", str(result["next_action"])])
    return "\n".join(lines) + "\n"


def _stage_status_label(record: object) -> str:
    """Return record['status'] as a string, or 'not_run' when absent."""
    return str(record.get("status", "not_run")) if isinstance(record, dict) else "not_run"


def _contract_issue_number(contract: dict[str, object]) -> int:
    """Return issue.number from contract.json, or 0 when absent."""
    issue = contract.get("issue")
    return _int_field(issue, "number") if isinstance(issue, dict) else 0


def _resolve_report_outcome(
    progress: dict[str, object], publication: dict[str, object], head_sha: str
) -> tuple[str, str, str]:
    """Return (status, pr_url, next_step) for cmd_report."""
    if bool(publication.get("permitted")):
        pr_data = _require_published_pr(head_sha)
        pr_url = str(pr_data["url"]).strip()
        return "published", pr_url, f"Review and merge {pr_url}."
    outcome = str(publication.get("outcome", progress.get("outcome", "incomplete")))
    next_step = str(progress.get("blocker", "") or publication.get("reason", "") or progress.get("summary", ""))
    return outcome, "", next_step


def cmd_report() -> int:
    """Execute `ship/report`: write result.json and report.md from verified run artifacts."""
    contract = _load_json_dict(CONTRACT_JSON)
    progress = _load_json_dict(PROGRESS_JSON)
    publication = _load_json_dict(PUBLICATION_JSON)
    head_sha = str(publication.get("head_sha", _current_head_sha()))
    outcome = str(publication.get("outcome", progress.get("outcome", "incomplete")))
    status, pr_url, next_step = _resolve_report_outcome(progress, publication, head_sha)
    result: dict[str, object] = {
        "schema_version": 1,
        "issue": _contract_issue_number(contract),
        "status": status,
        "outcome": outcome,
        "head_sha": head_sha,
        "pr_url": pr_url,
        "validation_status": _stage_status_label(progress.get("last_validation")),
        "review_status": _stage_status_label(progress.get("last_review")),
        "review_rounds": _int_field(progress, "review_rounds"),
        "summary": str(progress.get("summary", "") or publication.get("reason", "")),
        "next_action": next_step,
    }
    _atomic_write_json(RESULT_JSON, result)
    _atomic_write_text(REPORT_MD, _format_report_markdown(result, progress))
    _emit_outputs({"status": status, "outcome": outcome, "pr_url": pr_url})
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser for ship_state.py."""
    parser = argparse.ArgumentParser(description="Deterministic state and stage controller for ship.")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    prep = sub.add_parser("prepare", help="Capture the ticket contract and align the checkout.")
    prep.add_argument("--issue", default=os.environ.get("SHIP_ISSUE", ""), help="GitHub issue number.")
    prep.add_argument(
        "--pull-request", default=os.environ.get("SHIP_PULL_REQUEST", ""), help="Existing PR number to continue."
    )

    sub.add_parser("next", help="Output the next stage action or pending question.")

    rec = sub.add_parser("record", help="Validate stage.json or human answer and update progress.json.")
    rec.add_argument("--action", default=os.environ.get("SHIP_ACTION", ""), help="Active stage action.")
    rec.add_argument("--question-id", default=os.environ.get("SHIP_QUESTION_ID", ""), help="Active question ID.")
    rec.add_argument("--answer", default=os.environ.get("SHIP_ANSWER", ""), help="Human answer text.")

    val = sub.add_parser("validate", help="Run repository quality gates and write stage.json.")
    val.add_argument("--base", default=os.environ.get("SHIP_BASE_REF", "origin/main"), help="Base git ref.")

    sub.add_parser("publication-gate", help="Verify contract, HEAD, validation, and review before publishing.")
    sub.add_parser("publish", help="Push the gated revision, open or update the PR, and publish the review.")
    sub.add_parser("report", help="Write final result.json and report.md.")
    return parser


def _dispatch_subcommand(args: argparse.Namespace) -> int:
    """Dispatch parsed CLI arguments to the selected subcommand handler."""
    if args.subcommand == "prepare":
        return cmd_prepare(str(args.issue), str(args.pull_request))
    if args.subcommand == "next":
        return cmd_next()
    if args.subcommand == "record":
        return cmd_record(str(args.action), str(args.question_id), str(args.answer))
    if args.subcommand == "validate":
        return cmd_validate(str(args.base))
    if args.subcommand == "publication-gate":
        return cmd_publication_gate()
    if args.subcommand == "publish":
        return cmd_publish()
    if args.subcommand == "report":
        return cmd_report()
    return 1


def main(argv: list[str] | None = None) -> int:
    """Entrypoint for ship_state.py."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch_subcommand(args)
    except ShipStateError as exc:
        sys.stderr.write(f"ship_state.py: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
