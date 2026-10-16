#!/usr/bin/env python3
"""Generates blank per-rule audit files for editing rules_spec.yaml one rule at a time.

The audit files are templates. The editing agent fills every verdict and evidence field of a rule's file while it
edits that rule, then writes the whole file back.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

DEFAULT_OUT_DIR = Path(".agentic") / "rules-audit"
INTENT_WORD_LIMIT = 40

SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z`\"(])")

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
    "H01": "Was this rule edited in isolation, with no unrelated rule changed in the same step?",
    "H02": "Is every change to severity, scope, domain, an exemption, an enforcement classification, or a requirement (added or removed) listed in `proposed.policy_changes` with a reason?",
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
    "Edit rules_spec.yaml one rule at a time. Open that rule's file under `rules/<PREFIX>/<RULE-ID>.json`, fill it in, and write the whole file back with the Write tool before moving to the next rule.",
    "Write the file whole the first time: keep every key, every audit slot and the key order, and fill in verdicts, evidence and `proposed`. To fix one field afterwards, a single Edit is fine if its old_string includes the slot's audit id so it matches exactly once.",
    "Do not add, remove or rename an audit id, and do not edit a `question`; a checker compares them with this generator.",
    "`old` is a frozen snapshot of the rule before your edit, and `old_sha256` is its hash. Read it, do not change it.",
    "In `proposed.criteria`, add one entry per criterion under the id `<RULE>.Q01`, `<RULE>.Q02`, and so on, copying `criterion_template`.",
    "Each criterion is one yes/no question phrased so that YES means compliant.",
    "Write the result into rules_spec.yaml in the migrated shape documented in the comment at the top of that file: `intent` and `criteria`, with no `guideline`, `evaluation_criteria` or rule-level examples. Ids and questions there must match your `proposed` block. The keys of `criterion_template` are the spec keys, except `source` and `method`, which stay in the audit file only.",
    f"`proposed.intent` is one or two sentences, {INTENT_WORD_LIMIT} words or fewer, saying what the rule protects.",
    "Set each `verdict` to PASS (yes), FAIL (no), N/A (does not apply) or BLOCKED. No verdict may stay null.",
    "Fix every FAIL in this pass. Use BLOCKED only when the answer is no and the fix lies in a file you may not edit; the evidence names that file and line and who owns it, and the rule is not done until a human resolves it.",
    "Every verdict needs `evidence`. For a line-level fact in the repo use `path:line: <verbatim quote>`. For a search use `ran: <command> -> <result>`.",
    "To cite a field of an audit file use `@<audit id>.<field>`, for example `@ENCAP-001.Q01.method`, not a line number, because lines shift as the file is filled in.",
    "For A01 the evidence lists every key in `old.guideline_sentences`, each with its destination or a reason it was dropped.",
    "Each criterion needs its own example pair, or an `example_not_applicable` reason specific to that criterion.",
    "List every change to severity, scope, domain, an exemption, an enforcement classification, or a requirement in `proposed.policy_changes`, copying `policy_change_template`. Leave `approved_by` null; only a human fills it.",
    "Record duplicates and contradictions with other rules in `cross-references.json`, which you may edit, and answer E01 and E02 from it.",
    "To validate your spec edit, run `uv run python scripts/compile_rules.py --check`; it writes nothing. Do not run `scripts/compile_rules.py` without `--check`: the driver regenerates the compiled files once after the last rule of the prefix, so no audit slot asks about it.",
    "Do not cite a rule for a change it does not require; record such a change as a preference outside this file.",
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
    return {"audit": "rules-spec-edit-rule", "read_first": "../../shared.json", **rule_block(rule)}


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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        written.append(path)
    return written


def main() -> int:
    """Generate blank rules-spec audit files from docs/agents/rules_spec.yaml."""
    parser = argparse.ArgumentParser(description="Blank audit files for editing rules_spec.yaml")
    parser.add_argument("--prefix", action="append", help="Generate only this rule id prefix (repeatable).")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help="Output directory.")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    spec_path = root / "docs" / "agents" / "rules_spec.yaml"
    rules = yaml.safe_load(spec_path.read_text(encoding="utf-8"))["rules"]
    selected = {prefix.upper() for prefix in args.prefix} if args.prefix else None
    pending_prefixes = {prefix_of(rule["id"]) for rule in rules if not is_migrated(rule)}
    if selected is not None and not selected <= pending_prefixes:
        sys.stderr.write(f"No unmigrated rules for prefix in {sorted(selected - pending_prefixes)}\n")
        return 1

    documents = build_documents(rules, selected)
    for path in write_documents(documents, root / args.out):
        sys.stdout.write(f"Generated {path}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
