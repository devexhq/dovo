"""Tier 1 contract: scripts/rules_audit.py generates blank per-rule audit files from rules_spec.yaml."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
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
            pytest.param(
                "For payloads (e.g. CLI JSON output), pin them. Next.",
                ["For payloads (e.g. CLI JSON output), pin them.", "Next."],
                id="capital-after-e-g",
            ),
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


_SPEC_TEXT = """# header comment that must survive a merge
rules:
- id: A-001
  title: Title A-001
  severity: BLOCKER
  category: Test
  domain: all
  scope: src/
  guideline: Do the thing.
  evaluation_criteria: Check the thing.
- id: B-001
  title: Title B-001
  severity: WARNING
  category: Test
  domain: all
  scope: src/
  guideline: Do the other thing.
  evaluation_criteria: Check the other thing.
"""

_FRAGMENT_TEXT = """- id: A-001
  title: Title A-001
  severity: BLOCKER
  category: Test
  domain: all
  scope: src/
  intent: Protects something.
  criteria:
  - id: A-001.Q01
    question: Does the thing hold?
    kind: judgment
    example_not_applicable: nothing to show
"""


def _migrated_a001() -> dict[str, Any]:
    return {
        "id": "A-001",
        "title": "Title A-001",
        "severity": "BLOCKER",
        "category": "Test",
        "domain": "all",
        "scope": "src/",
        "intent": "Protects something.",
        "criteria": [
            {
                "id": "A-001.Q01",
                "question": "Does the thing hold?",
                "kind": "judgment",
                "example_not_applicable": "nothing to show",
            }
        ],
    }


def _complete(doc: dict[str, Any]) -> dict[str, Any]:
    """Answer every slot of a prepared audit file with PASS and evidence, as a finished review would."""
    slots = [*doc["audit"].values()]
    for criterion in doc["proposed"]["criteria"].values():
        criterion["source"], criterion["method"] = "G1", "read it"
        slots += criterion["audit"].values()
    for slot in slots:
        slot["verdict"], slot["evidence"] = "PASS", "ran: true -> ok"
    return doc


def _filled_doc(generator: ModuleType) -> dict[str, Any]:
    doc = generator.build_rule_file(_rule("A-001"))
    generator.prepare_proposed(doc, _migrated_a001())
    return _complete(doc)


class RulesAuditMergeTests:
    def test_merge_replaces_only_the_target_rule_and_keeps_the_header_comment(self) -> None:
        generator = _load_generator()

        merged = generator.merged_spec_text(_SPEC_TEXT, _FRAGMENT_TEXT, "A-001", REPO_ROOT)

        assert merged.startswith("# header comment that must survive a merge\nrules:\n- id: A-001\n")
        assert "guideline: Do the thing." not in merged
        assert "- id: B-001\n  title: Title B-001\n  severity: WARNING" in merged
        assert merged.endswith("evaluation_criteria: Check the other thing.\n")

    @pytest.mark.parametrize(
        ("fragment", "message"),
        [
            pytest.param(_FRAGMENT_TEXT.replace("- id: A-001\n", "- id: B-001\n", 1), "is not 'A-001'", id="wrong-id"),
            pytest.param(
                _FRAGMENT_TEXT.replace("Does the thing hold?", "Check the thing."), "must start with", id="bad-question"
            ),
            pytest.param("- id: A-001\n  guideline: Old prose.\n", "migrated shape", id="legacy-shape"),
            pytest.param("id: A-001\n", "YAML list with exactly one rule", id="not-a-list"),
            pytest.param("- id: [unclosed\n", "not valid YAML", id="invalid-yaml"),
        ],
    )
    def test_invalid_fragment_raises_value_error_and_names_the_problem(self, fragment: str, message: str) -> None:
        generator = _load_generator()

        with pytest.raises(ValueError, match=message):
            generator.merged_spec_text(_SPEC_TEXT, fragment, "A-001", REPO_ROOT)

    def test_merge_of_a_rule_missing_from_the_spec_raises_value_error(self) -> None:
        generator = _load_generator()
        fragment = _FRAGMENT_TEXT.replace("A-001", "Z-001")

        with pytest.raises(ValueError, match=r"Z-001 is not in rules_spec\.yaml"):
            generator.merged_spec_text(_SPEC_TEXT, fragment, "Z-001", REPO_ROOT)

    def test_merge_rule_writes_the_spec_and_prefills_a_new_review_round(self, tmp_path: Path) -> None:
        generator = _load_generator()
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "compile_rules.py").write_text(
            (REPO_ROOT / "scripts" / "compile_rules.py").read_text(encoding="utf-8"), encoding="utf-8"
        )
        spec_path = tmp_path / "docs" / "agents" / "rules_spec.yaml"
        spec_path.parent.mkdir(parents=True)
        spec_path.write_text(_SPEC_TEXT, encoding="utf-8")
        out_dir = tmp_path / "audit"
        generator.write_documents(generator.build_documents([_rule("A-001")], None), out_dir)
        (out_dir / "fragments").mkdir()
        (out_dir / "fragments" / "A-001.yaml").write_text(_FRAGMENT_TEXT, encoding="utf-8")

        message = generator.merge_rule(out_dir, tmp_path, "A-001")

        audit = json.loads((out_dir / "rules" / "A" / "A-001.json").read_text(encoding="utf-8"))
        assert message == "merged A-001 and refreshed its audit file"
        assert "intent: Protects something." in spec_path.read_text(encoding="utf-8")
        assert (audit["round"], audit["proposed"]["intent"]) == (1, "Protects something.")
        assert (out_dir / "history" / "A-001.round0.json").exists()

    def test_prepare_proposed_rebuilds_stale_audit_slots_from_the_generator(self) -> None:
        generator = _load_generator()
        doc = generator.build_rule_file(_rule("A-001"))
        doc["audit"] = {"A-001.Z99": {"question": "Stale?", "verdict": "PASS", "evidence": "x"}}

        generator.prepare_proposed(doc, _migrated_a001())

        assert list(doc["audit"]) == [f"A-001.{key}" for key in generator.RULE_QUESTIONS]
        assert [slot["verdict"] for slot in doc["audit"].values() if slot["verdict"] is not None] == []


def _check(generator: ModuleType, doc: dict[str, Any], rule: dict[str, Any] | None = None) -> dict[str, Any]:
    shared = generator.build_shared()
    return generator.check_rule_file(doc, rule or _migrated_a001(), shared, doc["old_sha256"], REPO_ROOT)


def _drop_verdict(doc: dict[str, Any]) -> None:
    doc["audit"]["A-001.A01"]["verdict"] = None


def _edit_question(doc: dict[str, Any]) -> None:
    doc["audit"]["A-001.B01"]["question"] = "Is it fine?"


def _edit_snapshot(doc: dict[str, Any]) -> None:
    doc["old"]["guideline"] = "Rewritten after the fact."


def _cite_missing_file(doc: dict[str, Any]) -> None:
    doc["audit"]["A-001.A01"]["evidence"] = "nowhere/missing.py:3 `x`"


def _cite_absent_quote(doc: dict[str, Any]) -> None:
    doc["audit"]["A-001.A01"]["evidence"] = "scripts/rules_audit.py:1 `this text is not on line one`"


def _cite_unresolved_reference(doc: dict[str, Any]) -> None:
    doc["audit"]["A-001.A01"]["evidence"] = "see @A-001.proposed.nothing_here"


def _leave_method_empty(doc: dict[str, Any]) -> None:
    doc["proposed"]["criteria"]["A-001.Q01"]["method"] = ""


class RulesAuditCheckTests:
    def test_a_fully_answered_audit_file_has_no_errors_fails_or_blocked_slots(self) -> None:
        generator = _load_generator()

        result = _check(generator, _filled_doc(generator))

        assert (result["errors"], result["fails"], result["blocked"]) == ([], [], [])

    @pytest.mark.parametrize(
        ("mutate", "message"),
        [
            pytest.param(_drop_verdict, "A-001.A01: verdict must be one of", id="null-verdict"),
            pytest.param(_edit_question, "A-001.B01: the question was edited", id="edited-question"),
            pytest.param(_edit_snapshot, "old snapshot was edited", id="edited-snapshot"),
            pytest.param(_cite_missing_file, "nowhere/missing.py, which does not exist", id="missing-cited-file"),
            pytest.param(_cite_absent_quote, "does not contain the quoted text", id="absent-quote"),
            pytest.param(_cite_unresolved_reference, "does not resolve", id="unresolved-reference"),
            pytest.param(_leave_method_empty, "`method` is empty", id="empty-method"),
        ],
    )
    def test_check_reports_each_kind_of_incomplete_or_tampered_audit_file(self, mutate: Any, message: str) -> None:
        generator = _load_generator()
        doc = _filled_doc(generator)
        mutate(doc)

        result = _check(generator, doc)

        assert any(message in error for error in result["errors"])

    def test_check_reports_a_severity_change_that_is_not_listed_as_a_policy_change(self) -> None:
        generator = _load_generator()
        doc = _filled_doc(generator)
        rule = {**_migrated_a001(), "severity": "WARNING"}

        result = _check(generator, doc, rule)

        assert any("severity changed" in error for error in result["errors"])

    def test_check_lists_fail_and_blocked_slots_and_counts_unapproved_policy_changes(self) -> None:
        generator = _load_generator()
        doc = _filled_doc(generator)
        doc["audit"]["A-001.A01"]["verdict"] = "FAIL"
        doc["audit"]["A-001.G01"]["verdict"] = "BLOCKED"
        doc["proposed"]["policy_changes"] = [{"kind": "exemption", "reason": "added", "approved_by": None}]

        result = _check(generator, doc)

        assert (result["fails"], result["blocked"], result["unapproved"]) == (["A-001.A01"], ["A-001.G01"], 1)


class RulesAuditReferenceTests:
    @pytest.mark.parametrize(
        ("reference", "resolves"),
        [
            pytest.param("A-001.Q01.method", True, id="criterion-field"),
            pytest.param("A-001.Q01.enforced_by", True, id="null-valued-field-still-exists"),
            pytest.param("A-001.A01", True, id="rule-level-slot"),
            pytest.param("A-001.proposed.intent", True, id="top-level-path"),
            pytest.param("A-001.Q09.method", False, id="unknown-criterion"),
            pytest.param("A-001.Q01.nothing_here", False, id="unknown-field"),
            pytest.param("OTHER-001.Q01.method", False, id="other-rule"),
            pytest.param("shared.intent_word_limit", True, id="shared-field"),
        ],
    )
    def test_reference_resolves_only_when_the_key_path_exists_in_the_audit_file(
        self, reference: str, resolves: bool
    ) -> None:
        generator = _load_generator()
        doc = _filled_doc(generator)

        assert generator.resolve_reference(reference, doc, generator.build_shared()) is resolves


class RulesAuditCitationTests:
    @pytest.mark.parametrize(
        ("evidence", "expected_errors"),
        [
            pytest.param(
                "scripts/rules_audit.py:2 `Generates blank per-rule audit files`", 0, id="stable-file-quote-on-its-line"
            ),
            pytest.param(
                "scripts/rules_audit.py:99 `Generates blank per-rule audit files`",
                1,
                id="stable-file-quote-on-another-line",
            ),
            pytest.param("docs/agents/rules_spec.yaml:1 `id: TEST-007`", 0, id="volatile-file-quote-anywhere"),
            pytest.param(
                "docs/agents/rules_spec.yaml:1 `no such text anywhere in the spec`", 1, id="volatile-file-quote-absent"
            ),
            pytest.param("docs/agents/rules_spec.yaml:99999", 0, id="volatile-file-line-not-checked"),
        ],
    )
    def test_line_numbers_are_checked_only_for_files_that_do_not_shift_during_a_run(
        self, evidence: str, expected_errors: int
    ) -> None:
        generator = _load_generator()

        assert len(generator.citation_errors("slot", evidence, REPO_ROOT)) == expected_errors


def _write_audit(generator: ModuleType, out_dir: Path, doc: dict[str, Any]) -> None:
    generator.write_json(out_dir / generator.rule_path(doc["id"]), doc)


class RulesAuditReportTests:
    def test_report_lists_each_policy_change_with_its_approval_state(self, tmp_path: Path) -> None:
        generator = _load_generator()
        doc = _filled_doc(generator)
        doc["proposed"]["policy_changes"] = [
            {"kind": "exemption", "from": None, "to": "Q01 na_when added", "reason": "scoping", "approved_by": None},
            {"kind": "scope", "from": "src/", "to": "src/dovo/", "reason": "narrower", "approved_by": "reviewer"},
        ]
        _write_audit(generator, tmp_path, doc)

        report = generator.build_report(tmp_path, ["A-001"], None)

        assert "1 rules reviewed, 2 policy changes (1 pending approval)." in report
        assert "| A-001 | exemption | (none) -> Q01 na_when added | scoping | **pending** |" in report
        assert "| A-001 | scope | src/ -> src/dovo/ | narrower | reviewer |" in report

    def test_report_separates_blocked_slots_from_open_fail_slots(self, tmp_path: Path) -> None:
        generator = _load_generator()
        doc = _filled_doc(generator)
        doc["audit"]["A-001.G01"].update(verdict="BLOCKED", evidence="docs/x.md:1 owner: docs team")
        doc["audit"]["A-001.A01"].update(verdict="FAIL", evidence="the author must fix this")
        _write_audit(generator, tmp_path, doc)

        report = generator.build_report(tmp_path, ["A-001"], None)

        blocked, open_fail = report.split("## Blocked: needs a human")[1].split("## Open FAIL")
        assert "A-001.G01" in blocked and "owner: docs team" in blocked
        assert "A-001.A01" in open_fail.split("## Cross-rule findings")[0]

    def test_report_says_none_for_a_section_with_no_rows(self, tmp_path: Path) -> None:
        generator = _load_generator()
        _write_audit(generator, tmp_path, _filled_doc(generator))

        report = generator.build_report(tmp_path, ["A-001"], None)

        assert report.count("\nnone\n") == 4

    def test_report_includes_only_cross_references_that_name_a_reviewed_rule(self, tmp_path: Path) -> None:
        generator = _load_generator()
        _write_audit(generator, tmp_path, _filled_doc(generator))
        generator.write_json(
            tmp_path / "cross-references.json",
            {
                "entries": [
                    {"rules": ["A-001.Q01", "B-002"], "kind": "duplicate", "owner": "A-001", "decision": "keep both"},
                    {"rules": ["C-003", "D-004"], "kind": "duplicate", "owner": "C-003", "decision": "unrelated"},
                ]
            },
        )

        report = generator.build_report(tmp_path, ["A-001"], None)

        assert "keep both" in report
        assert "unrelated" not in report

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            pytest.param("a | b", "a \\| b", id="pipe-is-escaped"),
            pytest.param("line one\nline   two", "line one line two", id="whitespace-collapses"),
            pytest.param(None, "", id="none-is-empty"),
            pytest.param("x" * 400, "x" * 299 + "…", id="long-text-is-capped"),
        ],
    )
    def test_cell_makes_text_safe_for_a_one_line_table_cell(self, text: str | None, expected: str) -> None:
        assert _load_generator().cell(text) == expected

    def test_report_command_writes_the_report_to_the_requested_file(self, tmp_path: Path) -> None:
        generator = _load_generator()
        _write_audit(generator, tmp_path, _filled_doc(generator))
        output = tmp_path / "out" / "report.md"

        generator.report_command(tmp_path, ["A-001"], None, output)

        assert output.read_text(encoding="utf-8").startswith("# Rules migration report")
