#!/usr/bin/env python3
"""Generates, answers, validates and compares the per-change audit files the coding and review agents fill in.

Each agent audits the same change against REVIEW_CHECKLIST.json in its own file, .agentic/<role>-audit.json, without
reading the other's. `compare` is the only command that opens both, and only once the review audit validates.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKLIST_RELATIVE = Path("docs") / "agents" / "REVIEW_CHECKLIST.json"
AUDIT_DIR = Path(".agentic")
ROLES: tuple[str, ...] = ("coding", "review")
VERDICTS: tuple[str, ...] = ("PASS", "FAIL", "N/A")
EXCLUDED_PREFIXES: tuple[str, ...] = (".agentic/", "scratch/")
EVERYTHING = "whole repository"
CODE_SCOPES: dict[str, tuple[str, ...]] = {"all source and test files": ("src/", "tests/", "e2e/")}
PATH_PIECE = re.compile(r"[\w.*/\-]+")
CITATION = re.compile(r"[\w./\-]+\.[A-Za-z0-9]+:\d+")
EVIDENCE_PREVIEW = 160


def out(text: str) -> None:
    """Write a line to stdout."""
    sys.stdout.write(text + "\n")


def err(text: str) -> None:
    """Write a line to stderr."""
    sys.stderr.write(text + "\n")


def audit_path(root: Path, role: str, override: Path | None) -> Path:
    """Return where a role's audit file lives, unless the caller names another file."""
    return override if override is not None else root / AUDIT_DIR / f"{role}-audit.json"


def load_json(path: Path) -> dict[str, Any]:
    """Return a JSON object read from disk."""
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, document: dict[str, Any]) -> None:
    """Write a JSON file atomically, so a crash never leaves a half-written audit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
    os.replace(temp_name, path)


# ---------------------------------------------------------------------------------------------------------------
# scope: which changed files a rule applies to
# ---------------------------------------------------------------------------------------------------------------


def scope_patterns(scope: str) -> list[str] | None:
    """Return the path patterns named in a rule scope, or None when the scope names no path (a process rule)."""
    lowered = scope.strip().lower()
    if lowered == EVERYTHING:
        return [""]
    if lowered in CODE_SCOPES:
        return list(CODE_SCOPES[lowered])
    pieces = [piece for piece in PATH_PIECE.findall(scope) if "/" in piece or re.search(r"\.\w+$", piece)]
    return pieces or None


def path_matches(path: str, pattern: str) -> bool:
    """Return True if a repo-relative path falls under a scope pattern (a directory prefix, a file or a glob)."""
    if pattern.endswith("/") or pattern == "":
        return fnmatch.fnmatch(path, pattern + "*")
    options = {pattern, pattern.replace("**/", "")}
    return any(fnmatch.fnmatch(path, option) for option in options) or path.startswith(pattern + "/")


def rule_applies(scope: str, files: list[str]) -> bool | None:
    """Return whether any changed file is in the rule's scope, or None for a scope that names no path."""
    patterns = scope_patterns(scope)
    if patterns is None:
        return None
    return any(path_matches(path, pattern) for path in files for pattern in patterns)


def git_lines(root: Path, *args: str) -> list[str]:
    """Return the non-empty output lines of a git command run in the repo."""
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)
    return [line for line in result.stdout.splitlines() if line.strip()]


def default_base(root: Path) -> str:
    """Return the remote default branch ref, falling back to origin/main."""
    try:
        return git_lines(root, "rev-parse", "--abbrev-ref", "origin/HEAD")[0]
    except (subprocess.CalledProcessError, IndexError):
        return "origin/main"


def porcelain_path(line: str) -> str:
    """Return the destination path of one `git status --porcelain` line."""
    return line[3:].split(" -> ")[-1].strip().strip('"')


def keep_reviewable(paths: list[str]) -> list[str]:
    """Return the sorted unique paths with the agents' own working directories removed."""
    return sorted({path for path in paths if not path.startswith(EXCLUDED_PREFIXES)})


def resolve_scope(root: Path, range_: str | None, paths: list[str] | None) -> dict[str, Any]:
    """Return the change under audit: explicit paths, an explicit git range, the dirty tree or the branch diff."""
    if paths:
        return {"kind": "paths", "ref": None, "files": keep_reviewable(paths)}
    if range_:
        return {
            "kind": "range",
            "ref": range_,
            "files": keep_reviewable(git_lines(root, "diff", "--name-only", range_)),
        }
    dirty = git_lines(root, "status", "--porcelain", "-uall")
    if dirty:
        return {"kind": "worktree", "ref": None, "files": keep_reviewable([porcelain_path(line) for line in dirty])}
    base = default_base(root)
    files = git_lines(root, "diff", "--name-only", f"{base}...HEAD")
    return {"kind": "branch", "ref": f"{base}...HEAD", "files": keep_reviewable(files)}


# ---------------------------------------------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------------------------------------------


def load_checklist(root: Path) -> tuple[list[dict[str, Any]], str]:
    """Return the checklist rules and the hash of the file they were read from."""
    text = (root / CHECKLIST_RELATIVE).read_text(encoding="utf-8")
    return json.loads(text)["rules"], hashlib.sha256(text.encode("utf-8")).hexdigest()


def criterion_entry(rule: dict[str, Any], criterion: dict[str, Any]) -> dict[str, Any]:
    """Return one blank, unanswered audit entry for a checklist criterion."""
    return {
        "id": criterion["id"],
        "rule": rule["id"],
        "severity": rule["severity"],
        "kind": criterion["kind"],
        "statement": criterion["statement"],
        "na_when": criterion.get("na_when"),
        "verdict": None,
        "evidence": None,
    }


def expected_entries(
    rules: list[dict[str, Any]], files: list[str]
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Return the criteria that apply to the changed files, and the rules skipped because their scope names no path."""
    entries: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for rule in rules:
        applies = rule_applies(rule["scope"], files)
        if applies is None:
            skipped.append({"rule": rule["id"], "scope": rule["scope"]})
        elif applies:
            entries.extend(criterion_entry(rule, criterion) for criterion in rule.get("criteria", []))
    return entries, skipped


def keep_answers(entries: list[dict[str, Any]], previous: dict[str, Any] | None) -> list[str]:
    """Copy earlier answers onto entries whose id and statement are unchanged, returning the ids that lost theirs."""
    if previous is None:
        return []
    old = {entry["id"]: entry for entry in previous.get("criteria", []) if entry.get("verdict")}
    for entry in entries:
        earlier = old.pop(entry["id"], None)
        if earlier is not None and earlier["statement"] == entry["statement"]:
            entry["verdict"], entry["evidence"] = earlier["verdict"], earlier["evidence"]
        elif earlier is not None:
            old[entry["id"]] = earlier
    return sorted(old)


def build_audit(
    root: Path, role: str, scope: dict[str, Any], previous: dict[str, Any] | None
) -> tuple[dict[str, Any], list[str]]:
    """Return the audit document for a change, with earlier answers kept, and the ids whose answers were dropped."""
    rules, digest = load_checklist(root)
    entries, skipped = expected_entries(rules, scope["files"])
    dropped = keep_answers(entries, previous)
    document = {
        "role": role,
        "scope": scope,
        "checklist_sha256": digest,
        "skipped_rules": skipped,
        "criteria": entries,
    }
    return document, dropped


def generate_command(root: Path, role: str, file: Path | None, range_: str | None, paths: list[str] | None) -> int:
    """Write the role's audit file for the current change, keeping answers that still apply."""
    path = audit_path(root, role, file)
    previous = load_json(path) if path.is_file() else None
    scope = resolve_scope(root, range_, paths)
    if not scope["files"]:
        err("no changed files in scope; nothing to audit")
        return 1
    document, dropped = build_audit(root, role, scope, previous)
    write_json(path, document)
    answered = sum(1 for entry in document["criteria"] if entry["verdict"])
    rules = len({entry["rule"] for entry in document["criteria"]})
    out(f"wrote {path}: {len(document['criteria'])} criteria from {rules} rules, {answered} answers kept")
    out(f"scope {scope['kind']}: {len(scope['files'])} files; {len(document['skipped_rules'])} process rules skipped")
    if dropped:
        out(f"answers dropped (criterion left scope or its statement changed): {', '.join(dropped)}")
    return 0


# ---------------------------------------------------------------------------------------------------------------
# answer and validate
# ---------------------------------------------------------------------------------------------------------------


def answer_problem(answer: dict[str, Any]) -> str | None:
    """Return what is wrong with one answer, or None when it is complete."""
    verdict, evidence = answer.get("verdict"), answer.get("evidence")
    if verdict not in VERDICTS:
        return f"verdict must be one of {', '.join(VERDICTS)}"
    if not (isinstance(evidence, str) and evidence.strip()):
        return "evidence is empty"
    if verdict == "FAIL" and not CITATION.search(evidence):
        return "a FAIL needs a path:line citation in its evidence"
    return None


def parse_answers(raw: Any) -> dict[str, dict[str, Any]]:
    """Return answers keyed by criterion id, from either an id-keyed object or a list of objects with an id."""
    if isinstance(raw, list):
        return {item["id"]: item for item in raw if isinstance(item, dict) and "id" in item}
    return raw if isinstance(raw, dict) else {}


def apply_answers(document: dict[str, Any], answers: dict[str, dict[str, Any]]) -> list[str]:
    """Merge answers into the document, leaving every other entry untouched, and return the problems found."""
    by_id = {entry["id"]: entry for entry in document["criteria"]}
    problems = [f"{key}: not a criterion of this audit" for key in answers if key not in by_id]
    problems += [f"{key}: {why}" for key, answer in answers.items() if key in by_id and (why := answer_problem(answer))]
    if problems:
        return problems
    for key, answer in answers.items():
        by_id[key]["verdict"], by_id[key]["evidence"] = answer["verdict"], answer["evidence"].strip()
    return []


def answers_from_args(source: str | None, criterion: str | None, verdict: str | None, evidence: str | None) -> Any:
    """Return the raw answers given as a file, stdin or the single-answer flags."""
    if source is not None:
        return json.loads(sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8"))
    return {criterion: {"verdict": verdict, "evidence": evidence}}


def answer_command(root: Path, role: str, file: Path | None, raw: Any) -> int:
    """Merge answers into the role's audit file, keeping every answer not named."""
    path = audit_path(root, role, file)
    if not path.is_file():
        err(f"{path} does not exist; run generate first")
        return 1
    document = load_json(path)
    problems = apply_answers(document, parse_answers(raw))
    if problems:
        err("\n".join(f"error: {problem}" for problem in problems))
        return 1
    write_json(path, document)
    open_ids = [entry["id"] for entry in document["criteria"] if not entry["verdict"]]
    out(f"saved {path}: {len(document['criteria']) - len(open_ids)} answered, {len(open_ids)} unanswered")
    return 0


def drift_problems(document: dict[str, Any], root: Path) -> list[str]:
    """Return the ways the file's criteria differ from what the checklist requires for the recorded change."""
    rules, _ = load_checklist(root)
    wanted, _ = expected_entries(rules, document["scope"]["files"])
    have = {entry["id"]: entry for entry in document["criteria"]}
    problems = [f"{entry['id']}: missing from the audit file" for entry in wanted if entry["id"] not in have]
    wanted_ids = {entry["id"] for entry in wanted}
    problems += [f"{key}: not required by the checklist for this change" for key in have if key not in wanted_ids]
    problems += [
        f"{entry['id']}: statement differs from the checklist"
        for entry in wanted
        if entry["id"] in have and have[entry["id"]]["statement"] != entry["statement"]
    ]
    return problems


def answer_problems(document: dict[str, Any]) -> list[str]:
    """Return one problem per unanswered or malformed criterion."""
    problems: list[str] = []
    for entry in document["criteria"]:
        if not entry.get("verdict"):
            problems.append(f"{entry['id']}: unanswered")
        elif why := answer_problem(entry):
            problems.append(f"{entry['id']}: {why}")
    return problems


def validation_problems(document: dict[str, Any], role: str, root: Path) -> list[str]:
    """Return every reason the audit is not complete and consistent with the checklist."""
    if document.get("role") != role:
        return [f"file role is {document.get('role')!r}, expected {role!r}"]
    return drift_problems(document, root) + answer_problems(document)


def validate_command(root: Path, role: str, file: Path | None) -> int:
    """Exit non-zero unless every criterion is answered and the file matches the checklist."""
    path = audit_path(root, role, file)
    if not path.is_file():
        err(f"{path} does not exist; run generate first")
        return 1
    document = load_json(path)
    problems = validation_problems(document, role, root)
    if problems:
        err("\n".join(f"error: {problem}" for problem in problems))
        err(f"INVALID {path}: {len(problems)} problems")
        return 1
    failed = [entry["id"] for entry in document["criteria"] if entry["verdict"] == "FAIL"]
    out(f"OK {path}: {len(document['criteria'])} criteria answered, {len(failed)} FAIL")
    if failed:
        out(f"FAIL: {', '.join(failed)}")
    return 0


# ---------------------------------------------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------------------------------------------


def preview(entry: dict[str, Any] | None) -> str:
    """Return one side of a disagreement as `VERDICT: start of evidence`."""
    if entry is None:
        return "(not in this audit)"
    return f"{entry['verdict']}: {str(entry['evidence'])[:EVIDENCE_PREVIEW]}"


def disagreements(
    coding: dict[str, Any], review: dict[str, Any]
) -> list[tuple[str, dict[str, Any] | None, dict[str, Any] | None]]:
    """Return the criteria where the two audits differ in verdict or appear in only one of them."""
    mine = {entry["id"]: entry for entry in coding["criteria"]}
    theirs = {entry["id"]: entry for entry in review["criteria"]}
    rows = []
    for key in sorted(mine.keys() | theirs.keys()):
        left, right = mine.get(key), theirs.get(key)
        if left is None or right is None or left["verdict"] != right["verdict"]:
            rows.append((key, left, right))
    return rows


def compare_command(root: Path, coding_file: Path | None, review_file: Path | None) -> int:
    """Print where the coding and review audits disagree, once the review audit is complete."""
    review_path, coding_path = audit_path(root, "review", review_file), audit_path(root, "coding", coding_file)
    if not review_path.is_file() or validation_problems(load_json(review_path), "review", root):
        err("the review audit must exist and validate before the coding audit is read")
        return 2
    if not coding_path.is_file():
        err(f"{coding_path} does not exist; the coding agent left no audit")
        return 2
    coding, review = load_json(coding_path), load_json(review_path)
    rows = disagreements(coding, review)
    out(f"{len(rows)} disagreements across {len(review['criteria'])} review criteria")
    for key, left, right in rows:
        out(f"- {key}\n    coding: {preview(left)}\n    review: {preview(right)}")
    return 0


# ---------------------------------------------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Return the command line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="Repo root (default: this repository).")
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate", help="Write .agentic/<role>-audit.json for the current change.")
    generate.add_argument("--range", help="Audit this git range (A...B) instead of the working tree or branch.")
    generate.add_argument("--paths", nargs="+", help="Audit exactly these repo-relative paths.")
    answer = commands.add_parser("answer", help="Merge answers into the audit file, keeping the rest.")
    answer.add_argument("--from", dest="source", help="JSON file of answers, or - for stdin.")
    answer.add_argument("--id", dest="criterion", help="Criterion id for a single answer.")
    answer.add_argument("--verdict", choices=VERDICTS, help="Verdict for a single answer.")
    answer.add_argument("--evidence", help="Evidence for a single answer.")
    validate = commands.add_parser("validate", help="Fail unless every criterion is answered.")
    for command in (generate, answer, validate):
        command.add_argument("--role", choices=ROLES, required=True)
        command.add_argument("--file", type=Path, help="Audit file (default: .agentic/<role>-audit.json).")
    compare = commands.add_parser("compare", help="List disagreements; needs a valid review audit.")
    compare.add_argument("--coding", type=Path, help="Coding audit file.")
    compare.add_argument("--review", type=Path, help="Review audit file.")
    return parser


def run_answer(args: argparse.Namespace) -> int:
    """Run `answer`, requiring either a source or a complete single answer."""
    single = (args.criterion, args.verdict, args.evidence)
    if (args.source is not None) == all(value is not None for value in single):
        err("give either --from, or all of --id, --verdict and --evidence")
        return 2
    raw = answers_from_args(args.source, args.criterion, args.verdict, args.evidence)
    return answer_command(args.root, args.role, args.file, raw)


def main() -> int:
    """Run the requested command."""
    args = build_parser().parse_args()
    if args.command == "generate":
        return generate_command(args.root, args.role, args.file, args.range, args.paths)
    if args.command == "answer":
        return run_answer(args)
    if args.command == "validate":
        return validate_command(args.root, args.role, args.file)
    return compare_command(args.root, args.coding, args.review)


if __name__ == "__main__":
    sys.exit(main())
