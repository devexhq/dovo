"""Tier 1 contract: scripts/compile_rules.py domain routing for package RULES.md artifacts."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from tests.lint.astlib import REPO_ROOT


def _load_compiler() -> ModuleType:
    """Load scripts/compile_rules.py by path, since scripts/ is not a package."""
    path = REPO_ROOT / "scripts" / "compile_rules.py"
    spec = importlib.util.spec_from_file_location("compile_rules_under_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rule(rule_id: str, domain: Any) -> dict[str, Any]:
    return {
        "id": rule_id,
        "title": f"Title {rule_id}",
        "severity": "BLOCKER",
        "category": "Test",
        "domain": domain,
        "scope": "src/",
        "guideline": f"Guideline {rule_id}.",
        "evaluation_criteria": f"Criteria {rule_id}.",
    }


def _build(tmp_path: Path, rules: list[dict[str, Any]]) -> dict[Path, str]:
    spec_path = tmp_path / "docs" / "agents" / "rules_spec.yaml"
    spec_path.parent.mkdir(parents=True)
    spec_path.write_text(yaml.safe_dump({"rules": rules}), encoding="utf-8")
    return _load_compiler().build_artifacts(spec_path, root=tmp_path)


class CompileRulesTests:
    def test_engine_domain_emits_engine_rules_md_with_all_and_engine_rules(self, tmp_path: Path) -> None:
        """[tier-1/unit] build_artifacts: a spec with one 'all' rule, one 'engine' rule, and one 'core' rule emits <root>/src/worktree/engine/docs/RULES.md titled '(Engine Domain)' containing the 'all' and 'engine' rule ids and not the 'core' rule id."""
        artifacts = _build(tmp_path, [_rule("ALL-001", "all"), _rule("ENG-001", "engine"), _rule("COR-001", "core")])

        engine_rules = artifacts[tmp_path / "src" / "worktree" / "engine" / "docs" / "RULES.md"]

        assert "(Engine Domain)" in engine_rules
        assert "ALL-001" in engine_rules
        assert "ENG-001" in engine_rules
        assert "COR-001" not in engine_rules

    def test_list_domain_rule_appears_in_every_listed_package_rules_md(self, tmp_path: Path) -> None:
        """[tier-1/unit] build_artifacts: a rule with domain ['core', 'engine'] appears in both <root>/src/worktree/core/docs/RULES.md and <root>/src/worktree/engine/docs/RULES.md and in neither the cli nor tests RULES.md; the checklist JSON carries "domain": ["core", "engine"]."""
        artifacts = _build(tmp_path, [_rule("BOTH-001", ["core", "engine"])])

        package_docs = {
            package: artifacts[tmp_path / "src" / "worktree" / package / "docs" / "RULES.md"]
            for package in ("core", "engine", "cli")
        }
        tests_docs = artifacts[tmp_path / "tests" / "docs" / "RULES.md"]
        checklist = json.loads(artifacts[tmp_path / "docs" / "agents" / "REVIEW_CHECKLIST.json"])

        assert "BOTH-001" in package_docs["core"]
        assert "BOTH-001" in package_docs["engine"]
        assert "BOTH-001" not in package_docs["cli"]
        assert "BOTH-001" not in tests_docs
        assert json.dumps(checklist).count('"domain": ["core", "engine"]') == 1

    @pytest.mark.parametrize(
        "domain", [pytest.param("platform", id="string"), pytest.param(["core", "platform"], id="list")]
    )
    def test_unknown_domain_raises_value_error_naming_the_rule(self, tmp_path: Path, domain: Any) -> None:
        """[tier-1/unit] build_artifacts: a rule with domain 'platform', and a rule with domain ['core', 'platform'], each raise ValueError whose message contains the rule id and 'invalid or missing domain'."""
        with pytest.raises(ValueError, match=r"BAD-001.*invalid or missing domain"):
            _build(tmp_path, [_rule("BAD-001", domain)])
