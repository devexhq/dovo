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
        artifacts = _build(tmp_path, [_rule("ALL-001", "all"), _rule("ENG-001", "engine"), _rule("COR-001", "core")])

        engine_rules = artifacts[tmp_path / "src" / "dovo" / "engine" / "docs" / "RULES.md"]

        assert "(Engine Domain)" in engine_rules
        assert "ALL-001" in engine_rules
        assert "ENG-001" in engine_rules
        assert "COR-001" not in engine_rules

    def test_list_domain_rule_appears_in_every_listed_package_rules_md(self, tmp_path: Path) -> None:
        artifacts = _build(tmp_path, [_rule("BOTH-001", ["core", "engine"])])

        package_docs = {
            package: artifacts[tmp_path / "src" / "dovo" / package / "docs" / "RULES.md"]
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
        with pytest.raises(ValueError, match=r"BAD-001.*invalid or missing domain"):
            _build(tmp_path, [_rule("BAD-001", domain)])


def _migrated_rule(**overrides: Any) -> dict[str, Any]:
    rule: dict[str, Any] = {
        "id": "NEW-001",
        "title": "Title NEW-001",
        "severity": "BLOCKER",
        "category": "Test",
        "domain": "all",
        "scope": "src/",
        "intent": "Keeps callers on the public surface.",
        "criteria": [
            {
                "id": "NEW-001.Q01",
                "question": "Does every caller avoid reading private members?",
                "kind": "judgment",
                "positive_example": "caller.is_ready",
                "negative_example": "caller._ready",
            },
            {
                "id": "NEW-001.Q02",
                "question": "Is the layer check green?",
                "kind": "mechanical",
                "enforced_by": "scripts/check.py::layers",
                "example_not_applicable": "the check itself is the example",
            },
        ],
    }
    return {**rule, **overrides}


def _build_with_enforcer(tmp_path: Path, rule: dict[str, Any]) -> dict[Path, str]:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "check.py").write_text("", encoding="utf-8")
    return _build(tmp_path, [rule])


def _with_criterion(**overrides: Any) -> dict[str, Any]:
    rule = _migrated_rule()
    rule["criteria"] = [{**rule["criteria"][0], **overrides}]
    return rule


class MigratedRuleSchemaTests:
    def test_migrated_rule_renders_intent_and_every_question_in_rules_md(self, tmp_path: Path) -> None:
        artifacts = _build_with_enforcer(tmp_path, _migrated_rule())

        rules_md = artifacts[tmp_path / "src" / "dovo" / "core" / "docs" / "RULES.md"]

        assert "Keeps callers on the public surface." in rules_md
        assert "`NEW-001.Q01` (judgment) Does every caller avoid reading private members?" in rules_md
        assert "`NEW-001.Q02` (mechanical) Is the layer check green?" in rules_md

    def test_migrated_rule_lists_its_criteria_as_objects_in_the_review_checklist(self, tmp_path: Path) -> None:
        artifacts = _build_with_enforcer(tmp_path, _migrated_rule())

        entry = json.loads(artifacts[tmp_path / "docs" / "agents" / "REVIEW_CHECKLIST.json"])["rules"][0]

        assert entry["criteria"] == [
            {"id": "NEW-001.Q01", "question": "Does every caller avoid reading private members?", "kind": "judgment"},
            {"id": "NEW-001.Q02", "question": "Is the layer check green?", "kind": "mechanical"},
        ]
        assert entry["bad_pattern"] == "caller._ready"

    @pytest.mark.parametrize(
        ("rule", "message"),
        [
            pytest.param(
                _migrated_rule(criteria="1. Does x? 2. Does y?"), "criteria must be a non-empty list", id="string"
            ),
            pytest.param(_migrated_rule(criteria=[]), "criteria must be a non-empty list", id="empty-list"),
            pytest.param(
                _migrated_rule(intent=" ".join(["word"] * 41)), "intent must be 40 words or fewer", id="long-intent"
            ),
            pytest.param(_migrated_rule(guideline="Old prose."), "must not keep 'guideline'", id="legacy-guideline"),
            pytest.param(
                _with_criterion(question="Flag any caller reading private members?"),
                "must start with one of",
                id="imperative",
            ),
            pytest.param(
                _with_criterion(question="Does every caller avoid private members."), "only '?'", id="no-question-mark"
            ),
            pytest.param(_with_criterion(question="Does a? Does b?"), "only '?'", id="two-questions"),
            pytest.param(
                _with_criterion(question="Does x avoid 1. y 2. z?"), "embedded numbered list", id="embedded-numbering"
            ),
            pytest.param(_with_criterion(question="Does x avoid y; or z?"), "embedded numbered list", id="semicolon"),
            pytest.param(_with_criterion(id="NEW-001.Q09"), "id must be NEW-001.Q01", id="wrong-id"),
            pytest.param(_with_criterion(owner="me"), "unknown keys", id="unknown-key"),
            pytest.param(_with_criterion(kind="maybe"), "kind must be one of", id="bad-kind"),
            pytest.param(_with_criterion(kind="mechanical"), "needs enforced_by", id="mechanical-without-enforcer"),
            pytest.param(
                _with_criterion(kind="mechanical", enforced_by="scripts/missing.py"),
                "needs enforced_by",
                id="missing-enforcer-path",
            ),
            pytest.param(
                _with_criterion(enforced_by="scripts/check.py"), "only for a mechanical", id="judgment-with-enforcer"
            ),
            pytest.param(
                _with_criterion(negative_example=None), "both positive_example and negative_example", id="half-pair"
            ),
            pytest.param(_with_criterion(example_not_applicable="no snippet"), "not both", id="pair-and-reason"),
        ],
    )
    def test_invalid_migrated_rule_raises_value_error_naming_the_problem(
        self, tmp_path: Path, rule: dict[str, Any], message: str
    ) -> None:
        with pytest.raises(ValueError, match=message):
            _build_with_enforcer(tmp_path, rule)

    def test_multi_line_example_stays_indented_inside_its_code_fence(self, tmp_path: Path) -> None:
        rule = _with_criterion(positive_example="@property\ndef ready(self): ...", negative_example="a\n\nb")

        artifacts = _build(tmp_path, [rule])

        rules_md = artifacts[tmp_path / "src" / "dovo" / "core" / "docs" / "RULES.md"]
        assert "    @property\n    def ready(self): ...\n" in rules_md
        assert "    a\n\n    b\n" in rules_md
