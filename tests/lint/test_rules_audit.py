"""Tier 1 contract: scripts/rules_audit.py generates blank per-rule audit files from rules_spec.yaml."""

from __future__ import annotations

import importlib.util
from types import ModuleType
from typing import Any

import pytest

from tests.lint.astlib import REPO_ROOT


def _load_generator() -> ModuleType:
    """Load scripts/rules_audit.py by path, since scripts/ is not a package."""
    path = REPO_ROOT / "scripts" / "rules_audit.py"
    spec = importlib.util.spec_from_file_location("rules_audit_under_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rule(rule_id: str, guideline: str = "Do the thing.") -> dict[str, Any]:
    return {
        "id": rule_id,
        "title": f"Title {rule_id}",
        "severity": "BLOCKER",
        "domain": "all",
        "scope": "src/",
        "guideline": guideline,
        "evaluation_criteria": "Check the thing. Flag the other thing.",
    }


class RulesAuditGeneratorTests:
    def test_each_rule_gets_its_own_file_under_its_prefix_directory(self) -> None:
        generator = _load_generator()
        rules = [_rule("TEST-002"), _rule("ENCAP-001"), _rule("TEST-001")]

        documents = generator.build_documents(rules, None)

        assert sorted(name for name in documents if name.startswith("rules/")) == [
            "rules/ENCAP/ENCAP-001.json",
            "rules/TEST/TEST-001.json",
            "rules/TEST/TEST-002.json",
        ]

    def test_every_rule_file_points_to_the_shared_file_and_has_all_questions_unanswered(self) -> None:
        generator = _load_generator()

        documents = generator.build_documents([_rule("ENCAP-001")], None)

        rule_file = documents["rules/ENCAP/ENCAP-001.json"]
        assert rule_file["read_first"] == "../../shared.json"
        assert set(rule_file["audit"]) == {f"ENCAP-001.{question_id}" for question_id in generator.RULE_QUESTIONS}
        assert [slot["verdict"] for slot in rule_file["audit"].values() if slot["verdict"] is not None] == []
        assert rule_file["proposed"] == {"intent": None, "criteria": {}, "policy_changes": []}

    def test_a_rule_snapshot_hash_is_unchanged_by_an_edit_to_another_rule(self) -> None:
        generator = _load_generator()
        before = generator.build_documents([_rule("ENCAP-001"), _rule("ENCAP-002")], None)
        after = generator.build_documents([_rule("ENCAP-001"), _rule("ENCAP-002", guideline="Edited.")], None)

        hashes_before, hashes_after = before["index.json"]["old_sha256"], after["index.json"]["old_sha256"]

        assert hashes_before["ENCAP-001"] == hashes_after["ENCAP-001"]
        assert hashes_before["ENCAP-002"] != hashes_after["ENCAP-002"]

    def test_shared_file_offers_a_blocked_verdict_and_a_policy_change_template(self) -> None:
        generator = _load_generator()

        shared = generator.build_documents([_rule("ENCAP-001")], None)["shared.json"]

        assert "BLOCKED" in shared["verdicts"]
        assert shared["policy_change_template"]["approved_by"] is None

    def test_old_guideline_is_frozen_as_numbered_sentences(self) -> None:
        generator = _load_generator()
        rule = _rule(
            "ENCAP-001", guideline="Expose a property (e.g. is_ready) on classes. Do not read private members."
        )

        block = generator.rule_block(rule)

        assert block["old"]["guideline_sentences"] == {
            "G1": "Expose a property (e.g. is_ready) on classes.",
            "G2": "Do not read private members.",
        }

    def test_prefix_filter_writes_only_that_prefix_but_the_index_lists_every_rule(self) -> None:
        generator = _load_generator()
        rules = [_rule("ENCAP-001"), _rule("DRY-001")]

        documents = generator.build_documents(rules, {"ENCAP"})

        assert sorted(name for name in documents if name.startswith("rules/")) == ["rules/ENCAP/ENCAP-001.json"]
        assert set(documents["index.json"]["rules"]) == {"DRY-001", "ENCAP-001"}

    def test_an_already_migrated_rule_gets_no_file_and_is_listed_in_the_index(self) -> None:
        generator = _load_generator()
        migrated = {**_rule("ENCAP-001"), "criteria": [{"id": "ENCAP-001.Q01"}]}

        documents = generator.build_documents([migrated, _rule("DRY-001")], None)

        assert sorted(name for name in documents if name.startswith("rules/")) == ["rules/DRY/DRY-001.json"]
        assert documents["index.json"]["already_migrated"] == ["ENCAP-001"]

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            pytest.param("One. Two.", ["One.", "Two."], id="capital-after-period"),
            pytest.param("Use e.g. a value here.", ["Use e.g. a value here."], id="lowercase-after-abbreviation"),
        ],
    )
    def test_split_sentences_breaks_only_before_a_capital_quote_or_parenthesis(
        self, text: str, expected: list[str]
    ) -> None:
        assert _load_generator().split_sentences(text) == expected


class RulesAuditSchemaParityTests:
    def test_criterion_template_keys_are_the_spec_keys_plus_the_audit_only_ones(self) -> None:
        generator = _load_generator()
        spec_path = REPO_ROOT / "scripts" / "compile_rules.py"
        loader = importlib.util.spec_from_file_location("compile_rules_for_parity", spec_path)
        assert loader is not None
        assert loader.loader is not None
        compiler = importlib.util.module_from_spec(loader)
        loader.loader.exec_module(compiler)

        template_keys = set(generator.criterion_template()) - {"source", "method", "audit"}

        assert template_keys == set(compiler.CRITERION_KEYS) - {"id"}
