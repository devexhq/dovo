#!/usr/bin/env python3
"""Compiles rules_spec.yaml into static docs/agents/ and package docs/ artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

VALID_DOMAINS: set[str] = {"all", "core", "common", "cli", "engine", "tests"}

PACKAGE_DOMAINS: dict[str, tuple[str, tuple[str, ...]]] = {
    "core": ("Core Domain", ("src", "dovo", "core", "docs", "RULES.md")),
    "common": ("Common Domain", ("src", "dovo", "common", "docs", "RULES.md")),
    "cli": ("CLI Domain", ("src", "dovo", "cli", "docs", "RULES.md")),
    "engine": ("Engine Domain", ("src", "dovo", "engine", "docs", "RULES.md")),
    "tests": ("Tests Domain", ("tests", "docs", "RULES.md")),
}


def _rule_domains(rule: dict[str, Any]) -> list[str]:
    """Return the rule's domain tag(s) as a list, accepting a single string or a list of strings; empty when missing."""
    domain = rule.get("domain")
    if isinstance(domain, str):
        return [domain]
    if isinstance(domain, list):
        return [str(item) for item in domain]
    return []


def _validate_rule_domains(rules: list[dict[str, Any]]) -> None:
    """Raise ValueError for the first rule whose domain tag(s) are missing or outside VALID_DOMAINS."""
    for rule in rules:
        domains = _rule_domains(rule)
        if not domains or not set(domains) <= VALID_DOMAINS:
            raise ValueError(f"Rule '{rule.get('id')}' has invalid or missing domain: {rule.get('domain')}")


def _format_example_block(rule: dict[str, Any]) -> list[str]:
    """Format positive and negative code examples into markdown lines."""
    has_pos = "positive_example" in rule
    has_neg = "negative_example" in rule
    if not has_pos and not has_neg:
        return []

    lines = ["", "```python"]
    if has_pos:
        pos = str(rule["positive_example"]).strip()
        sep = "\n" if "\n" in pos else " "
        lines.append(f"# ✅ DO:{sep}{pos}")
    if has_neg:
        neg = str(rule["negative_example"]).strip()
        sep = "\n" if "\n" in neg else " "
        lines.append(f"# ❌ DO NOT:{sep}{neg}")
    lines.append("```")
    return lines


def generate_coding_rules_md(rules: list[dict[str, Any]], title_suffix: str = "") -> str:
    """Generate Markdown text for package-specific RULES.md.

    Args:
        rules: List of coding and review rule dictionaries.
        title_suffix: Optional suffix appended to document title.

    Returns:
        Formatted markdown document content.
    """
    md_lines = [
        "<!-- AUTO-GENERATED FROM rules_spec.yaml. DO NOT EDIT DIRECTLY. -->",
        f"# Architectural & Coding Invariants{title_suffix}",
        "",
        "> **Notice for Agents:** Code violating `BLOCKER` rules will fail verification.",
        "",
    ]
    for rule in rules:
        md_lines.append(f"- **[{rule['id']}] {rule['title']} ({rule['severity']}):**")
        md_lines.append(f"  {rule['guideline']}")
        md_lines.extend(_format_example_block(rule))
        md_lines.append("")

    return "\n".join(md_lines).strip() + "\n"


def generate_review_checklist_json(rules: list[dict[str, Any]]) -> str:
    """Generate JSON text for REVIEW_CHECKLIST.json.

    Args:
        rules: List of coding and review rule dictionaries.

    Returns:
        Formatted JSON string.
    """
    audit_data = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "description": "AUTO-GENERATED FROM rules_spec.yaml. Static checklist for Review Agent.",
        "rules": [
            {
                "id": rule["id"],
                "name": rule["title"],
                "severity": rule["severity"],
                "domain": rule.get("domain", "all"),
                "scope": rule["scope"],
                "evaluation_criteria": rule["evaluation_criteria"],
                "bad_pattern": rule.get("negative_example"),
            }
            for rule in rules
        ],
    }
    return json.dumps(audit_data, indent=2) + "\n"


def _format_planner_example(example: Any) -> list[str]:
    """Format a single planner rule example without nesting code fences.

    Some examples in rules_spec.yaml are already fenced ```python blocks; wrapping
    those in another ```markdown fence breaks out of the outer fence early and
    corrupts everything rendered after it. Plain (unfenced) examples still get
    wrapped so they render as a block.

    Args:
        example: Raw example text from rules_spec.yaml.

    Returns:
        Markdown lines for this example, fenced only if the source wasn't already.
    """
    text = str(example).strip()
    if text.startswith("```"):
        return [text]
    return ["```markdown", text, "```"]


def generate_planner_rules_md(planner_rules: list[dict[str, Any]]) -> str:
    """Generate Markdown text for PLANNER_RULES.md.

    Args:
        planner_rules: List of planner rule dictionaries.

    Returns:
        Formatted markdown document content.
    """
    plan_lines = [
        "<!-- AUTO-GENERATED FROM rules_spec.yaml. DO NOT EDIT DIRECTLY. -->",
        "# Planning Invariants & Specification Rules",
        "",
        "> **Notice for Agents:** Plans failing `deliverable_contract` or `validation_check` will be rejected.",
        "",
    ]
    for rule in planner_rules:
        plan_lines.append(f"## [{rule['id']}] {rule['title']}")
        plan_lines.append(f"- **Phase:** `{rule['phase']}`")
        plan_lines.append(f"- **Scope:** `{rule['scope']}`")
        plan_lines.append(f"- **Requirement:** {rule['requirement']}")
        plan_lines.append(f"- **Deliverable Contract:** {rule['deliverable_contract']}")
        plan_lines.append(f"- **Validation Check:** {rule['validation_check']}")
        if "positive_example" in rule:
            plan_lines.append("")
            plan_lines.append("<!-- ✅ POSITIVE EXAMPLE -->")
            plan_lines.extend(_format_planner_example(rule["positive_example"]))
        if "negative_example" in rule:
            plan_lines.append("")
            plan_lines.append("<!-- ❌ NEGATIVE EXAMPLE -->")
            plan_lines.extend(_format_planner_example(rule["negative_example"]))
        plan_lines.append("")

    return "\n".join(plan_lines).strip() + "\n"


def build_artifacts(spec_path: Path, root: Path | None = None) -> dict[Path, str]:
    """Load rules_spec.yaml and generate all static documentation artifacts.

    Args:
        spec_path: Path to canonical rules_spec.yaml file.
        root: Optional repository root directory. Inferred if omitted.

    Returns:
        Dictionary mapping destination file path to compiled content string.
    """
    resolved_root = spec_path.parent.parent.parent if root is None else root

    with open(spec_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    rules: list[dict[str, Any]] = data.get("rules", [])
    planner_rules: list[dict[str, Any]] = data.get("planner_rules", [])

    _validate_rule_domains(rules)

    artifacts: dict[Path, str] = {
        resolved_root / "docs" / "agents" / "REVIEW_CHECKLIST.json": generate_review_checklist_json(rules),
    }

    if planner_rules:
        planner_path = resolved_root / "docs" / "agents" / "PLANNER_RULES.md"
        artifacts[planner_path] = generate_planner_rules_md(planner_rules)

    for domain_name, (domain_label, path_parts) in PACKAGE_DOMAINS.items():
        domain_rules = [r for r in rules if {"all", domain_name} & set(_rule_domains(r))]
        target_path = resolved_root.joinpath(*path_parts)
        artifacts[target_path] = generate_coding_rules_md(domain_rules, title_suffix=f" ({domain_label})")

    return artifacts


def _resolve_spec_path(root: Path) -> Path | None:
    """Resolve the canonical or fallback rules_spec.yaml file path."""
    canonical = root / "docs" / "agents" / "rules_spec.yaml"
    if canonical.exists():
        return canonical
    fallback = root / "rules_spec.yaml"
    if fallback.exists():
        return fallback
    return None


def check_artifacts_parity(artifacts: dict[Path, str]) -> list[Path]:
    """Check if existing artifact files on disk match expected content exactly.

    Args:
        artifacts: Mapping of file path to expected string content.

    Returns:
        List of paths that are missing or out of sync.
    """
    mismatched: list[Path] = []
    for path, expected in artifacts.items():
        if not path.exists():
            mismatched.append(path)
            continue
        current = path.read_text(encoding="utf-8")
        if current != expected:
            mismatched.append(path)
    return mismatched


def write_artifacts(artifacts: dict[Path, str]) -> None:
    """Write generated artifact contents to target paths.

    Args:
        artifacts: Mapping of file path to expected string content.
    """
    for path, content in artifacts.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        sys.stdout.write(f"Generated {path}\n")


def main() -> int:
    """Compile rules_spec.yaml or check parity with existing artifacts."""
    parser = argparse.ArgumentParser(description="Agent rules compiler")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check if generated files match rules_spec.yaml without writing.",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    spec_path = _resolve_spec_path(root)
    if spec_path is None:
        sys.stderr.write("❌ Error: Rule specification file not found in docs/agents/ or root.\n")
        return 1

    artifacts = build_artifacts(spec_path, root=root)

    if args.check:
        mismatched = check_artifacts_parity(artifacts)
        if mismatched:
            sys.stderr.write("❌ Agent rule artifacts are out of sync with rules_spec.yaml:\n")
            for p in mismatched:
                sys.stderr.write(f"   - {p}\n")
            sys.stderr.write("Run `uv run python scripts/compile_rules.py` to regenerate.\n")
            return 1
        return 0

    write_artifacts(artifacts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
