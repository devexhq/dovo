#!/usr/bin/env python3
"""Generates blank per-rule audit files for editing rules_spec.yaml one rule at a time.

The audit files are templates. The editing agent fills every verdict and evidence field of a rule's file while it
edits that rule, then writes the whole file back.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_RELATIVE = Path("docs") / "agents" / "rules_spec.yaml"
DEFAULT_OUT_DIR = Path(".agentic") / "rules-audit"
FRAGMENTS_DIR = "fragments"
INTENT_WORD_LIMIT = 40
REPORT_CELL_LIMIT = 300
IDENTITY_POLICY_FIELDS: tuple[str, ...] = ("severity", "scope", "domain")
VOLATILE_PATHS: frozenset[str] = frozenset(
    {"docs/agents/rules_spec.yaml", "docs/agents/REVIEW_CHECKLIST.json", "docs/agents/PLANNER_RULES.md"}
)
PATH_LINE = re.compile(r"(?P<path>[\w./\-]+\.[A-Za-z0-9]+):(?P<line>\d+)(?: `(?P<quote>[^`]+)`)?")
AUDIT_REF = re.compile(r"@(?P<ref>[A-Za-z][\w\-]*(?:\.[\w\-]+)+)")

SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])(?<!e\.g\.)(?<!i\.e\.)\s+(?=[A-Z`\"(])")

# Rule-level questions. YES means fine for every question.
RULE_QUESTIONS: dict[str, str] = {
    "A01": "Is every sentence of the old guideline (`old.guideline_sentences`) assigned a destination in the evidence: intent, a criterion id, an example, or dropped with a reason?",
    "A02": "Is every clause of the old evaluation_criteria string (`old.criteria_clauses`) covered by at least one proposed criterion?",
    "A03": "Does every proposed criterion trace to an old guideline sentence or criteria clause, with anything new flagged for review?",
    "A04": "Are the old exceptions, permissions and in-sentence definitions kept as conditional criteria with a stated na_when, or as a criterion or example?",
    "B01": "Does `intent` say what the rule protects, or what goes wrong without it, in one or two sentences?",
    "B02": "Is `intent` free of checkable details that belong in the criteria, so it stays true if a criterion is added or tightened?",
    "D01": "Do the criteria together cover the rule, each checking a different fact, and is their number proportional to the size of the rule?",
    "E01": "Is every criterion free of duplication in another rule, or is the owning rule named in cross-references.json?",
    "E02": "Does the rule avoid contradicting another rule where their scopes overlap, tested with an example from each?",
    "E03": "Does `scope` match exactly where the rule should apply, and is `domain` consistent with it?",
    "E04": "Is `severity` justified by the criteria now written and consistent with similar rules?",
    "F01": "For every mechanical criterion, does the named executing check exist at its path and check the same thing, and does every remaining 'enforced by' sentence still hold?",
    "F02": "For every judgment criterion, is it one a lint or hook could not check, or is it recorded as a lint candidate in the evidence?",
    "G01": "Does the citation anchor resolve to a section that still matches the rule?",
    "G02": "Do the other files that refer to this rule by id or title (docs/, skills, AGENTS.md) still say the right thing?",
    "H01": "Is every change to severity, scope, domain, an exemption, an enforcement classification, or a requirement (added or removed) listed in `proposed.policy_changes` with a reason?",
}

# Questions asked of every proposed criterion. YES means fine.
CRITERION_QUESTIONS: dict[str, str] = {
    "C01": "Is it one yes/no question about one fact, with YES meaning compliant, no double negative, and no vague word that lacks an operational test?",
    "C02": "Can it be answered from evidence a reviewer can produce by the stated `method`, over an explicit scope, with `na_when` stated if it is conditional?",
    "C03": "Do `positive_example` and `negative_example` show a concrete pass and a concrete fail, or does `example_not_applicable` give a reason specific to this criterion?",
}

VERDICTS: tuple[str, ...] = ("PASS", "FAIL", "N/A", "BLOCKED")

POLICY_CHANGE_KINDS: tuple[str, ...] = (
    "severity",
    "scope",
    "domain",
    "exemption",
    "enforcement",
    "requirement_added",
    "requirement_removed",
)

INSTRUCTIONS: list[str] = [
    "You are the reviewer. You did not write this rule's new text. Do not edit rules_spec.yaml or the author's fragment; edit only this rule's audit file and, for E01 and E02, cross-references.json.",
    "Judge the rule text, not the code's compliance with it. A violation that already exists in the repo is not a FAIL; it is useful as an example.",
    "`old` is a frozen snapshot of the rule before the rewrite, and `old_sha256` is its hash. Read it, do not change it.",
    "`proposed.intent` and `proposed.criteria` are prefilled from the merged spec. Do not change them or any `question`; a checker compares them with the spec. Fill in each criterion's `source`, `method` and `audit`.",
    "`source` lists the ids it comes from, for example `G2, K1`, using the keys of `old.guideline_sentences` and `old.criteria_clauses`.",
    "Set each `verdict` to PASS (the answer is yes), FAIL (no), N/A (does not apply) or BLOCKED. No verdict may stay null.",
    "FAIL means the author must fix the rule text. BLOCKED means the answer is no but the fix lies outside the rule text, such as a doc section, a skill or a citation target; leave the citation unchanged, and name the file, line and owner in the evidence.",
    "Every verdict needs `evidence`. For a line in the repo write `path:line` with the full repo path, and put any quoted text in backticks immediately after it, for example ``docs/agents/testing.md:115 `## Core Testing Rules` ``; a checker re-reads that line. For docs/agents/rules_spec.yaml and the generated rule files, whose lines shift during a run, the checker only confirms the quoted text appears somewhere in the file, so cite them with a quote or by rule id.",
    "For a search write `ran: <command> -> <result>`; for 'none found' write `ran: <command> -> no matches`. To cite a field of an audit file write `@<audit id>.<field>`, for example `@ENCAP-001.Q01.method`, never a line number.",
    f"A word is a whitespace-separated token, so `intent` is {INTENT_WORD_LIMIT} words or fewer when `wc -w` says so.",
    "A question starts with Does, Do, Is, Are, Was, Were, Has or Have, is one sentence ending in '?', and is phrased so that YES means compliant.",
    "For A01 the evidence lists every key in `old.guideline_sentences`, each with where it went (intent, a criterion id, an example) or the reason it was dropped.",
    "List every difference between `old` and the new rule in `proposed.policy_changes`, copying `policy_change_template`: a changed severity, scope or domain, an added exemption (`na_when`), a changed enforcement classification, and any requirement added, removed, tightened or loosened. A permission turned into an obligation counts. Leave `approved_by` null; only a human fills it.",
    "Check every symbol, path, field and enum value that an example names against the repo, and every statement it makes about existing code. An example that names something that does not exist, or says something false, is a FAIL on C03.",
    "Record duplicates and contradictions with other rules in `cross-references.json`, and answer E01 and E02 from it.",
    "Every `@` reference must name a real field of this audit file or of shared.json; never write a placeholder such as `Qnn` or a range. A field whose value is null still exists.",
    "F01 is N/A when no criterion is mechanical. A criterion that joins two steps of one mechanism is one fact for C01; split it only if one step can pass while the other fails.",
    "Isolation (that no other rule changed) is guaranteed by `merge`, so there is no slot for it; hunks of other rules in the working-tree diff are other steps' work.",
    "Write the audit file whole the first time, keeping every key, every slot and the key order. To fix one field afterwards, a single Edit is fine if its old_string includes the slot's audit id so it matches once.",
    "Do not run `scripts/compile_rules.py`; the driver runs it once after the last rule of the prefix, so no slot asks about it. Do not cite a rule for a change it does not require.",
]


def split_sentences(text: str) -> list[str]:
    """Split prose into sentences on terminal punctuation followed by a capital, backtick, quote or parenthesis."""
    flattened = " ".join(str(text).split())
    return [part for part in SENTENCE_BOUNDARY.split(flattened) if part]


def numbered(prefix: str, items: list[str]) -> dict[str, str]:
    """Return items keyed `<prefix>1`, `<prefix>2`, and so on."""
    return {f"{prefix}{index}": item for index, item in enumerate(items, start=1)}


def audit_slots(prefix: str, questions: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Return one blank verdict slot per question, keyed `<prefix>.<question id>`."""
    return {
        f"{prefix}.{question_id}": {"question": question, "verdict": None, "evidence": None}
        for question_id, question in questions.items()
    }


def criterion_template() -> dict[str, Any]:
    """Return the blank entry an agent copies for each proposed criterion."""
    return {
        "question": None,
        "source": None,
        "method": None,
        "na_when": None,
        "kind": None,
        "enforced_by": None,
        "positive_example": None,
        "negative_example": None,
        "example_not_applicable": None,
        "audit": {
            question_id: {"question": question, "verdict": None, "evidence": None}
            for question_id, question in CRITERION_QUESTIONS.items()
        },
    }


def old_snapshot(rule: dict[str, Any]) -> dict[str, Any]:
    """Freeze the fields of a rule that the edit may change, with its prose split into numbered sentences."""
    return {
        "title": rule.get("title"),
        "severity": rule.get("severity"),
        "domain": rule.get("domain"),
        "scope": rule.get("scope"),
        "guideline": " ".join(str(rule.get("guideline", "")).split()),
        "guideline_sentences": numbered("G", split_sentences(rule.get("guideline", ""))),
        "evaluation_criteria": " ".join(str(rule.get("evaluation_criteria", "")).split()),
        "criteria_clauses": numbered("K", split_sentences(rule.get("evaluation_criteria", ""))),
        "positive_example": rule.get("positive_example"),
        "negative_example": rule.get("negative_example"),
        "citation": rule.get("citation"),
    }


def snapshot_sha256(snapshot: dict[str, Any]) -> str:
    """Return a stable hash of a rule snapshot, so an edit to another rule never invalidates this block."""
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode("utf-8")).hexdigest()


def policy_change_template() -> dict[str, Any]:
    """Return the blank entry an agent copies for each change that alters what is enforced."""
    return {
        "kind": f"one of: {', '.join(POLICY_CHANGE_KINDS)}",
        "from": None,
        "to": None,
        "reason": None,
        "approved_by": None,
    }


def rule_block(rule: dict[str, Any]) -> dict[str, Any]:
    """Build the blank audit block for one rule."""
    snapshot = old_snapshot(rule)
    return {
        "id": rule["id"],
        "old": snapshot,
        "old_sha256": snapshot_sha256(snapshot),
        "audit": audit_slots(rule["id"], RULE_QUESTIONS),
        "proposed": {"intent": None, "criteria": {}, "policy_changes": []},
    }


def prefix_of(rule_id: str) -> str:
    """Return the alphabetic prefix of a rule id, for example `TEST` for `TEST-020`."""
    return rule_id.rsplit("-", 1)[0]


def group_by_prefix(rules: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group rules by id prefix, keeping each group sorted by rule id."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for rule in sorted(rules, key=lambda item: item["id"]):
        groups.setdefault(prefix_of(rule["id"]), []).append(rule)
    return groups


def is_migrated(rule: dict[str, Any]) -> bool:
    """Return True when the rule is already written as an intent plus criteria, so its old text no longer exists."""
    return "criteria" in rule


def rule_path(rule_id: str) -> str:
    """Return the path, relative to the audit directory, of a rule's file: `rules/<PREFIX>/<RULE-ID>.json`."""
    return f"rules/{prefix_of(rule_id)}/{rule_id}.json"


def build_rule_file(rule: dict[str, Any]) -> dict[str, Any]:
    """Build the blank audit file for one rule, which the agent fills in and writes back whole."""
    return {"audit": "rules-spec-edit-rule", "read_first": "../../shared.json", "round": 0, **rule_block(rule)}


def build_shared() -> dict[str, Any]:
    """Build the file of instructions and templates that every rule file points to."""
    return {
        "audit": "rules-spec-edit-shared",
        "verdicts": list(VERDICTS),
        "intent_word_limit": INTENT_WORD_LIMIT,
        "instructions": INSTRUCTIONS,
        "criterion_template": criterion_template(),
        "policy_change_template": policy_change_template(),
    }


def build_index(pending: list[dict[str, Any]], migrated: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the index that lists every expected rule file, the prefixes, and each rule's snapshot hash."""
    return {
        "audit": "rules-spec-edit-index",
        "shared": "shared.json",
        "cross_references": "cross-references.json",
        "rules": {rule["id"]: rule_path(rule["id"]) for rule in pending},
        "prefixes": {prefix: [rule["id"] for rule in rules] for prefix, rules in group_by_prefix(pending).items()},
        "old_sha256": {rule["id"]: snapshot_sha256(old_snapshot(rule)) for rule in pending},
        "already_migrated": sorted(rule["id"] for rule in migrated),
    }


def build_cross_references() -> dict[str, Any]:
    """Build the blank file that records duplicates and contradictions between rules in different audit files."""
    return {
        "audit": "rules-spec-edit-cross-references",
        "instructions": [
            "Record every criterion that duplicates, or contradicts, a criterion in another rule.",
            "Name the owning rule, the rule that defers to it, and the decision. Answer E01 and E02 in each rule's block from this file.",
        ],
        "entries": [],
        "entry_template": {
            "rules": None,
            "kind": None,
            "owner": None,
            "decision": None,
            "evidence": None,
        },
    }


def build_documents(rules: list[dict[str, Any]], prefixes: set[str] | None) -> dict[str, dict[str, Any]]:
    """Build every audit document: the index, shared file, cross-references, and a file per selected unmigrated rule."""
    pending = [rule for rule in rules if not is_migrated(rule)]
    migrated = [rule for rule in rules if is_migrated(rule)]
    documents = {
        "index.json": build_index(pending, migrated),
        "shared.json": build_shared(),
        "cross-references.json": build_cross_references(),
    }
    for rule in sorted(pending, key=lambda item: item["id"]):
        if prefixes is None or prefix_of(rule["id"]) in prefixes:
            documents[rule_path(rule["id"])] = build_rule_file(rule)
    return documents


def write_documents(documents: dict[str, dict[str, Any]], out_dir: Path) -> list[Path]:
    """Write each document as indented JSON under out_dir, creating directories, and return the paths written."""
    written: list[Path] = []
    for name, document in documents.items():
        path = out_dir / name
        write_json(path, document)
        written.append(path)
    return written


def write_json(path: Path, document: dict[str, Any]) -> None:
    """Write a document as indented JSON, creating its directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------------------------------------------
# merge: splice an author's fragment into rules_spec.yaml and prefill the rule's audit file from it
# ---------------------------------------------------------------------------------------------------------------


def load_compiler(root: Path) -> ModuleType:
    """Load scripts/compile_rules.py by path, since scripts/ is not a package."""
    path = root / "scripts" / "compile_rules.py"
    spec = importlib.util.spec_from_file_location("compile_rules_for_audit", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rule_span(spec_text: str, rule_id: str) -> tuple[int, int]:
    """Return the character span of a rule's `- id:` entry, up to the next entry or top-level key."""
    start_match = re.search(rf"^- id: {re.escape(rule_id)}[ \t]*$", spec_text, re.MULTILINE)
    if start_match is None:
        raise ValueError(f"{rule_id} is not in rules_spec.yaml")
    following = re.search(r"^\S", spec_text[start_match.end() :], re.MULTILINE)
    end = len(spec_text) if following is None else start_match.end() + following.start()
    return start_match.start(), end


def parse_fragment(fragment_text: str, rule_id: str, root: Path) -> dict[str, Any]:
    """Parse and validate a fragment: a one-item YAML list holding the migrated rule."""
    try:
        loaded = yaml.safe_load(fragment_text)
    except yaml.YAMLError as error:
        raise ValueError(f"fragment is not valid YAML: {error}") from error
    if not (isinstance(loaded, list) and len(loaded) == 1 and isinstance(loaded[0], dict)):
        raise ValueError("fragment must be a YAML list with exactly one rule, starting with `- id:`")
    rule = loaded[0]
    if rule.get("id") != rule_id:
        raise ValueError(f"fragment id {rule.get('id')!r} is not {rule_id!r}")
    compiler = load_compiler(root)
    if not compiler.is_migrated(rule):
        raise ValueError("fragment must be written in the migrated shape (intent plus criteria)")
    compiler.validate_migrated_rules([rule], root)
    return rule


def merged_spec_text(spec_text: str, fragment_text: str, rule_id: str, root: Path) -> str:
    """Return spec_text with the rule replaced by the fragment, after checking that no other rule changed."""
    rule = parse_fragment(fragment_text, rule_id, root)
    start, end = rule_span(spec_text, rule_id)
    merged = spec_text[:start] + fragment_text.strip("\n") + "\n" + spec_text[end:]
    before = {entry["id"]: entry for entry in yaml.safe_load(spec_text)["rules"]}
    after = {entry["id"]: entry for entry in yaml.safe_load(merged)["rules"]}
    if list(before) != list(after) or any(before[key] != after[key] for key in before if key != rule_id):
        raise ValueError("merging would change a rule other than " + rule_id)
    if after[rule_id] != rule:
        raise ValueError("the merged rule differs from the fragment")
    return merged


def write_atomic(path: Path, text: str) -> None:
    """Replace a file's content through a temporary sibling, so a reader never sees a partial write."""
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(temporary, path)


def prepare_proposed(doc: dict[str, Any], rule: dict[str, Any]) -> None:
    """Prefill an audit file's `proposed` section from the merged rule and blank every answer for a fresh review."""
    template = criterion_template()
    criteria: dict[str, Any] = {}
    for criterion in rule["criteria"]:
        entry = {**template, "audit": json.loads(json.dumps(template["audit"]))}
        for key in template:
            if key in criterion:
                entry[key] = criterion[key]
        criteria[criterion["id"]] = entry
    doc["proposed"] = {"intent": rule["intent"], "criteria": criteria, "policy_changes": []}
    doc["audit"] = audit_slots(doc["id"], RULE_QUESTIONS)


def archive_round(out_dir: Path, doc: dict[str, Any]) -> None:
    """Keep the previous review of a rule under history/ before it is replaced."""
    write_json(out_dir / "history" / f"{doc['id']}.round{doc.get('round', 0)}.json", doc)


def refresh_audit_file(out_dir: Path, rule: dict[str, Any]) -> bool:
    """Archive the rule's audit file and prefill the next round from the merged rule; False if there is no file."""
    audit_path = out_dir / rule_path(rule["id"])
    if not audit_path.exists():
        return False
    doc = json.loads(audit_path.read_text(encoding="utf-8"))
    archive_round(out_dir, doc)
    prepare_proposed(doc, rule)
    doc["round"] = doc.get("round", 0) + 1
    write_json(audit_path, doc)
    return True


def merge_rule(out_dir: Path, root: Path, rule_id: str) -> str:
    """Splice out_dir/fragments/<rule_id>.yaml into rules_spec.yaml under a lock and refresh the rule's audit file."""
    fragment_path = out_dir / FRAGMENTS_DIR / f"{rule_id}.yaml"
    if not fragment_path.exists():
        raise ValueError(f"no fragment at {fragment_path}")
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / ".merge.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        spec_path = root / SPEC_RELATIVE
        fragment_text = fragment_path.read_text(encoding="utf-8")
        write_atomic(spec_path, merged_spec_text(spec_path.read_text(encoding="utf-8"), fragment_text, rule_id, root))
        refreshed = refresh_audit_file(out_dir, parse_fragment(fragment_text, rule_id, root))
    return f"merged {rule_id}" + (" and refreshed its audit file" if refreshed else "")


# ---------------------------------------------------------------------------------------------------------------
# check: validate a filled audit file against the generator, the merged spec and the repo
# ---------------------------------------------------------------------------------------------------------------


def slot_errors(slots: dict[str, Any], expected: dict[str, str]) -> list[str]:
    """Return what is wrong with a dict of audit slots compared with the questions the generator would write."""
    if list(slots) != list(expected):
        return [f"slot ids differ from the generator: {sorted(set(slots) ^ set(expected)) or 'order'}"]
    errors: list[str] = []
    for key, slot in slots.items():
        if slot.get("question") != expected[key]:
            errors.append(f"{key}: the question was edited")
        if slot.get("verdict") not in VERDICTS:
            errors.append(f"{key}: verdict must be one of {', '.join(VERDICTS)}")
        if not (isinstance(slot.get("evidence"), str) and slot["evidence"].strip()):
            errors.append(f"{key}: evidence is empty")
    return errors


def citation_errors(label: str, evidence: str, root: Path) -> list[str]:
    """Return the `path:line` citations in evidence that point at a missing file, a missing line or an absent quote."""
    problems = (citation_error(label, match, root) for match in PATH_LINE.finditer(evidence))
    return [problem for problem in problems if problem]


def citation_error(label: str, match: re.Match[str], root: Path) -> str | None:
    """Return what is wrong with one `path:line` citation, or None when it holds."""
    path = root / match["path"]
    if not path.is_file():
        return f"{label}: cites {match['path']}, which does not exist"
    text = path.read_text(encoding="utf-8", errors="replace")
    quote = " ".join(match["quote"].split()) if match["quote"] else ""
    if is_volatile(match["path"]):
        absent = bool(quote) and quote not in " ".join(text.split())
        return f"{label}: {match['path']} does not contain the quoted text" if absent else None
    return line_error(label, match["path"], int(match["line"]), text.splitlines(), quote)


def line_error(label: str, path: str, line: int, lines: list[str], quote: str) -> str | None:
    """Return an error if a cited line does not exist or does not contain the quoted text."""
    if not 1 <= line <= len(lines):
        return f"{label}: {path} has no line {line}"
    if quote and quote not in " ".join(lines[line - 1].split()):
        return f"{label}: line {line} of {path} does not contain the quoted text"
    return None


def is_volatile(path: str) -> bool:
    """Return True for a file whose line numbers change during a migration run: the spec and its generated outputs."""
    return path in VOLATILE_PATHS or path.endswith("/RULES.md")


def resolve_reference(reference: str, doc: dict[str, Any], shared: dict[str, Any]) -> bool:
    """Return True if an `@<audit id>.<field>` reference points at a key of the audit file or of shared.json."""
    head, *rest = reference.split(".")
    if head == "shared":
        return walk_keys(shared, rest)
    if head != doc["id"]:
        return False
    if rest and re.fullmatch(r"[A-H]\d\d", rest[0]):
        return walk_keys(doc["audit"], [f"{head}.{rest[0]}", *rest[1:]])
    if rest and re.fullmatch(r"Q\d+", rest[0]):
        return walk_keys(doc["proposed"]["criteria"], [f"{head}.{rest[0]}", *rest[1:]])
    return walk_keys(doc, rest)


def walk_keys(node: Any, keys: list[str]) -> bool:
    """Return True if every key exists in turn through nested dicts from node; a null value still counts as existing."""
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return False
        node = node[key]
    return True


def all_slots(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return every audit slot of a rule file, rule-level and per-criterion, keyed by its full id."""
    criterion_slots = {
        f"{criterion_id}.{key}": slot
        for criterion_id, criterion in doc["proposed"]["criteria"].items()
        for key, slot in criterion["audit"].items()
    }
    return {**doc["audit"], **criterion_slots}


def evidence_errors(doc: dict[str, Any], shared: dict[str, Any], root: Path) -> list[str]:
    """Return citation and reference errors across every evidence string of an audit file."""
    errors: list[str] = []
    for label, slot in all_slots(doc).items():
        evidence = slot.get("evidence") or ""
        errors += citation_errors(label, evidence, root)
        errors += [
            f"{label}: reference @{match['ref']} does not resolve"
            for match in AUDIT_REF.finditer(evidence)
            if not resolve_reference(match["ref"], doc, shared)
        ]
    return errors


def proposed_errors(doc: dict[str, Any], rule: dict[str, Any]) -> list[str]:
    """Return where the audit file's `proposed` section disagrees with the merged spec."""
    proposed, errors = doc["proposed"], []
    if proposed.get("intent") != rule["intent"]:
        errors.append("proposed.intent differs from the spec")
    if list(proposed["criteria"]) != [criterion["id"] for criterion in rule["criteria"]]:
        return [*errors, "proposed.criteria ids differ from the spec"]
    for criterion in rule["criteria"]:
        errors += criterion_audit_errors(criterion, proposed["criteria"][criterion["id"]])
    return errors


def criterion_audit_errors(criterion: dict[str, Any], entry: dict[str, Any]) -> list[str]:
    """Return where one criterion's audit entry disagrees with the spec or leaves a required field empty."""
    label = criterion["id"]
    errors = [
        f"{label}: {key} differs from the spec"
        for key, value in criterion.items()
        if key != "id" and entry.get(key) != value
    ]
    errors += [f"{label}.{error}" for error in slot_errors(entry["audit"], dict(CRITERION_QUESTIONS))]
    errors += [f"{label}: `{key}` is empty" for key in ("source", "method") if not entry.get(key)]
    return errors


def policy_errors(doc: dict[str, Any], rule: dict[str, Any]) -> list[str]:
    """Return policy-change problems: malformed entries, and severity, scope or domain changes that are not listed."""
    changes = doc["proposed"]["policy_changes"]
    errors = [
        f"policy change {index}: kind must be one of {', '.join(POLICY_CHANGE_KINDS)} and a reason is required"
        for index, change in enumerate(changes, start=1)
        if change.get("kind") not in POLICY_CHANGE_KINDS or not change.get("reason")
    ]
    listed = {change.get("kind") for change in changes}
    errors += [
        f"{field} changed from {doc['old'].get(field)!r} to {rule.get(field)!r} but no policy change of kind '{field}' is listed"
        for field in IDENTITY_POLICY_FIELDS
        if doc["old"].get(field) != rule.get(field) and field not in listed
    ]
    return errors


def snapshot_errors(doc: dict[str, Any], expected_hash: str | None) -> list[str]:
    """Return an error if the frozen `old` snapshot was edited or no longer matches the hash in the index."""
    if snapshot_sha256(doc["old"]) != doc.get("old_sha256") or (expected_hash and doc["old_sha256"] != expected_hash):
        return ["old snapshot was edited or does not match the index hash"]
    return []


def check_rule_file(
    doc: dict[str, Any], rule: dict[str, Any], shared: dict[str, Any], expected_hash: str | None, root: Path
) -> dict[str, Any]:
    """Check one filled audit file and return its errors, FAIL slots, BLOCKED slots and unapproved policy changes."""
    errors = snapshot_errors(doc, expected_hash)
    errors += slot_errors(doc["audit"], {f"{doc['id']}.{key}": question for key, question in RULE_QUESTIONS.items()})
    errors += proposed_errors(doc, rule)
    errors += policy_errors(doc, rule)
    errors += evidence_errors(doc, shared, root)
    slots = all_slots(doc)
    return {
        "errors": errors,
        "fails": [key for key, slot in slots.items() if slot.get("verdict") == "FAIL"],
        "blocked": [key for key, slot in slots.items() if slot.get("verdict") == "BLOCKED"],
        "unapproved": sum(1 for change in doc["proposed"]["policy_changes"] if not change.get("approved_by")),
    }


def audit_rule_ids(out_dir: Path, rule_ids: list[str], prefixes: list[str] | None) -> list[str]:
    """Resolve which rule ids to check: those named, those under the prefixes, or every audit file present."""
    if rule_ids:
        return rule_ids
    found = sorted(path.stem for path in (out_dir / "rules").glob("*/*.json"))
    return [rule_id for rule_id in found if not prefixes or prefix_of(rule_id) in {p.upper() for p in prefixes}]


def check_command(
    out_dir: Path, root: Path, rule_ids: list[str], prefixes: list[str] | None, require_approval: bool
) -> int:
    """Check audit files and print one block per rule; exit 1 on any error, FAIL, or (if required) unapproved change."""
    rules = {rule["id"]: rule for rule in yaml.safe_load((root / SPEC_RELATIVE).read_text(encoding="utf-8"))["rules"]}
    shared = json.loads((out_dir / "shared.json").read_text(encoding="utf-8"))
    hashes = json.loads((out_dir / "index.json").read_text(encoding="utf-8")).get("old_sha256", {})
    failed = False
    for rule_id in audit_rule_ids(out_dir, rule_ids, prefixes):
        doc = json.loads((out_dir / rule_path(rule_id)).read_text(encoding="utf-8"))
        rule = rules.get(rule_id, {})
        result = (
            check_rule_file(doc, rule, shared, hashes.get(rule_id), root)
            if "criteria" in rule
            else {
                "errors": ["the rule is not merged into rules_spec.yaml yet"],
                "fails": [],
                "blocked": [],
                "unapproved": 0,
            }
        )
        bad = bool(result["errors"] or result["fails"] or (require_approval and result["unapproved"]))
        failed = failed or bad
        print_result(rule_id, result, bad)
    return 1 if failed else 0


def print_result(rule_id: str, result: dict[str, Any], bad: bool) -> None:
    """Print a one-line summary of a rule's check followed by each error, FAIL and BLOCKED slot."""
    counts = f"{len(result['errors'])} errors, {len(result['fails'])} FAIL, {len(result['blocked'])} BLOCKED"
    sys.stdout.write(f"{'FAIL' if bad else 'ok  '} {rule_id}: {counts}, {result['unapproved']} unapproved\n")
    for line in [
        *result["errors"],
        *(f"FAIL {key}" for key in result["fails"]),
        *(f"BLOCKED {key}" for key in result["blocked"]),
    ]:
        sys.stdout.write(f"    {line}\n")


# ---------------------------------------------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------------------------------------------


def cell(text: object) -> str:
    """Make text safe for a one-line markdown table cell: collapse whitespace, escape pipes, cap the length."""
    flat = " ".join(str(text if text is not None else "").split()).replace("|", "\\|")
    return flat if len(flat) <= REPORT_CELL_LIMIT else flat[: REPORT_CELL_LIMIT - 1].rstrip() + "…"


def table(headers: list[str], rows: list[list[str]]) -> str:
    """Render rows as a markdown table, or 'none' when there are no rows."""
    if not rows:
        return "none\n"
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines) + "\n"


def load_audit_docs(out_dir: Path, rule_ids: list[str], prefixes: list[str] | None) -> list[dict[str, Any]]:
    """Load the audit files that exist for the selected rules, in rule id order."""
    selected = audit_rule_ids(out_dir, rule_ids, prefixes)
    paths = [out_dir / rule_path(rule_id) for rule_id in selected]
    return [json.loads(path.read_text(encoding="utf-8")) for path in paths if path.exists()]


def policy_rows(docs: list[dict[str, Any]]) -> list[list[str]]:
    """Return one table row per policy change: rule, kind, change, reason and who approved it."""
    return [
        [
            cell(doc["id"]),
            cell(change.get("kind")),
            cell(f"{change.get('from') or '(none)'} -> {change.get('to') or '(none)'}"),
            cell(change.get("reason")),
            cell(change.get("approved_by") or "**pending**"),
        ]
        for doc in docs
        for change in doc["proposed"]["policy_changes"]
    ]


def slot_rows(docs: list[dict[str, Any]], verdict: str) -> list[list[str]]:
    """Return one table row per audit slot with the given verdict: slot, question and evidence."""
    return [
        [cell(f"`{key}`"), cell(slot.get("question")), cell(slot.get("evidence"))]
        for doc in docs
        for key, slot in all_slots(doc).items()
        if slot.get("verdict") == verdict
    ]


def cross_reference_rows(out_dir: Path, docs: list[dict[str, Any]]) -> list[list[str]]:
    """Return one table row per cross-reference entry that names one of the reviewed rules."""
    path = out_dir / "cross-references.json"
    if not path.exists():
        return []
    reviewed = {doc["id"] for doc in docs}
    entries = json.loads(path.read_text(encoding="utf-8")).get("entries", [])
    return [
        [
            cell(", ".join(entry.get("rules") or [])),
            cell(entry.get("kind")),
            cell(entry.get("owner")),
            cell(entry.get("decision")),
        ]
        for entry in entries
        if reviewed & {name.split(".")[0] for name in entry.get("rules") or []}
    ]


def build_report(out_dir: Path, rule_ids: list[str], prefixes: list[str] | None) -> str:
    """Render the migration report: what changed in meaning, what needs a human, and what is still failing."""
    docs = load_audit_docs(out_dir, rule_ids, prefixes)
    policy = policy_rows(docs)
    pending = sum(1 for row in policy if row[-1] == "**pending**")
    sections = [
        f"# Rules migration report\n\n{len(docs)} rules reviewed, {len(policy)} policy changes ({pending} pending approval).\n",
        "## Policy changes\n\n" + table(["Rule", "Kind", "Change", "Reason", "Approved by"], policy),
        "## Blocked: needs a human\n\n" + table(["Slot", "Question", "Evidence"], slot_rows(docs, "BLOCKED")),
        "## Open FAIL\n\n" + table(["Slot", "Question", "Evidence"], slot_rows(docs, "FAIL")),
        "## Cross-rule findings\n\n"
        + table(["Rules", "Kind", "Owner", "Decision"], cross_reference_rows(out_dir, docs)),
    ]
    return "\n".join(sections)


def report_command(out_dir: Path, rule_ids: list[str], prefixes: list[str] | None, output: Path | None) -> int:
    """Print the migration report, or write it to output when one is given."""
    text = build_report(out_dir, rule_ids, prefixes)
    if output is None:
        sys.stdout.write(text)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        sys.stdout.write(f"Wrote {output}\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser with the generate, merge, check and report commands."""
    parser = argparse.ArgumentParser(description="Audit files for editing rules_spec.yaml one rule at a time")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help="Audit directory.")
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate", help="Write blank audit files for unmigrated rules.")
    generate.add_argument("--prefix", action="append", help="Only this rule id prefix (repeatable).")
    merge = commands.add_parser("merge", help="Splice fragments/<RULE-ID>.yaml into rules_spec.yaml.")
    merge.add_argument("rule_ids", nargs="+")
    check = commands.add_parser("check", help="Validate filled audit files.")
    check.add_argument("rule_ids", nargs="*")
    check.add_argument("--prefix", action="append")
    check.add_argument("--require-approval", action="store_true", help="Fail while any policy change is unapproved.")
    report = commands.add_parser("report", help="Render policy changes, BLOCKED items and open FAILs as markdown.")
    report.add_argument("rule_ids", nargs="*")
    report.add_argument("--prefix", action="append")
    report.add_argument("--output", type=Path, help="Write the report to this file instead of printing it.")
    return parser


def generate_command(out_dir: Path, root: Path, prefixes: list[str] | None) -> int:
    """Write blank audit files for the unmigrated rules, optionally for some prefixes only."""
    rules = yaml.safe_load((root / SPEC_RELATIVE).read_text(encoding="utf-8"))["rules"]
    selected = {prefix.upper() for prefix in prefixes} if prefixes else None
    pending_prefixes = {prefix_of(rule["id"]) for rule in rules if not is_migrated(rule)}
    if selected is not None and not selected <= pending_prefixes:
        sys.stderr.write(f"No unmigrated rules for prefix in {sorted(selected - pending_prefixes)}\n")
        return 1
    for path in write_documents(build_documents(rules, selected), out_dir):
        sys.stdout.write(f"Generated {path}\n")
    return 0


def merge_command(out_dir: Path, root: Path, rule_ids: list[str]) -> int:
    """Merge each rule's fragment; stop at the first failure and print why."""
    for rule_id in rule_ids:
        try:
            sys.stdout.write(merge_rule(out_dir, root, rule_id) + "\n")
        except ValueError as error:
            sys.stderr.write(f"cannot merge {rule_id}: {error}\n")
            return 1
    return 0


def main() -> int:
    """Run the generate, merge or check command."""
    args = build_parser().parse_args()
    out_dir = REPO_ROOT / args.out
    if args.command == "generate":
        return generate_command(out_dir, REPO_ROOT, args.prefix)
    if args.command == "merge":
        return merge_command(out_dir, REPO_ROOT, args.rule_ids)
    if args.command == "report":
        return report_command(out_dir, args.rule_ids, args.prefix, args.output)
    return check_command(out_dir, REPO_ROOT, args.rule_ids, args.prefix, args.require_approval)


if __name__ == "__main__":
    sys.exit(main())
