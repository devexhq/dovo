"""Tier 1 contract: scripts/change_audit.py generates, answers, validates and compares the agents' change audits."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.lint.astlib import REPO_ROOT

SRC_RULE: dict[str, Any] = {
    "id": "ARCH-001",
    "name": "Layers",
    "severity": "BLOCKER",
    "scope": "src/dovo/core/",
    "criteria": [
        {"id": "ARCH-001.Q01", "statement": "core imports nothing from cli.", "kind": "mechanical"},
        {
            "id": "ARCH-001.Q02",
            "statement": "core imports nothing from rich.",
            "kind": "judgment",
            "na_when": "No import.",
        },
    ],
}
TEST_RULE: dict[str, Any] = {
    "id": "TEST-003",
    "name": "Names",
    "severity": "WARNING",
    "scope": "tests/",
    "criteria": [{"id": "TEST-003.Q01", "statement": "Classes end in Tests.", "kind": "judgment"}],
}
PROCESS_RULE: dict[str, Any] = {
    "id": "CI-002",
    "name": "Commits",
    "severity": "BLOCKER",
    "scope": "Git commits & PRs",
    "criteria": [{"id": "CI-002.Q01", "statement": "Titles are semantic.", "kind": "judgment"}],
}
CITED_FAIL = "src/dovo/core/db/models.py:30 imports dovo.cli"


def _load_script() -> ModuleType:
    """Load scripts/change_audit.py by path, since scripts/ is not a package."""
    path = REPO_ROOT / "scripts" / "change_audit.py"
    spec = importlib.util.spec_from_file_location("change_audit_under_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _root_with_checklist(tmp_path: Path, rules: list[dict[str, Any]]) -> Path:
    """Write a REVIEW_CHECKLIST.json holding the given rules under a fresh repo root."""
    checklist = tmp_path / "docs" / "agents" / "REVIEW_CHECKLIST.json"
    checklist.parent.mkdir(parents=True)
    checklist.write_text(json.dumps({"rules": rules}), encoding="utf-8")
    return tmp_path


def _audit(script: ModuleType, root: Path, role: str, files: list[str]) -> dict[str, Any]:
    """Build a fresh audit for the given changed files."""
    scope = {"kind": "paths", "ref": None, "files": files}
    document, _ = script.build_audit(root, role, scope, None)
    return document


def _answer_all(document: dict[str, Any], verdict: str = "PASS") -> None:
    """Give every criterion in the document the same cited answer."""
    for entry in document["criteria"]:
        entry["verdict"], entry["evidence"] = verdict, CITED_FAIL


class ChangeAuditScopeTests:
    @pytest.mark.parametrize(
        ("path", "pattern", "expected"),
        [
            pytest.param("src/dovo/core/db/models.py", "src/dovo/core/", True, id="directory_prefix"),
            pytest.param("src/dovo/core_extra.py", "src/dovo/core/", False, id="sibling_with_shared_prefix"),
            pytest.param("src/dovo/core/git/models.py", "src/dovo/core/*/models.py", True, id="single_level_glob"),
            pytest.param(
                "src/dovo/engine/models.py", "src/dovo/engine/**/models.py", True, id="double_star_matches_zero_levels"
            ),
            pytest.param("pyproject.toml", "pyproject.toml", True, id="exact_file"),
            pytest.param("docs/agents/a.md", "docs/agents/architecture.md", False, id="different_file"),
        ],
    )
    def test_path_matches_scope_pattern_by_prefix_glob_or_exact_file(
        self, path: str, pattern: str, expected: bool
    ) -> None:
        script = _load_script()

        assert script.path_matches(path, pattern) is expected

    @pytest.mark.parametrize(
        ("scope", "expected"),
        [
            pytest.param("src/dovo/core/, src/dovo/engine/", ["src/dovo/core/", "src/dovo/engine/"], id="comma_list"),
            pytest.param("AGENTS.md, docs/, src/ docstrings", ["AGENTS.md", "docs/", "src/"], id="prose_after_a_path"),
            pytest.param("common/, internal src/dovo/cli/", ["common/", "src/dovo/cli/"], id="prose_before_a_path"),
            pytest.param("Git commits & PRs", None, id="process_scope_names_no_path"),
            pytest.param("Whole repository", [""], id="whole_repository"),
        ],
    )
    def test_scope_patterns_extracts_paths_and_flags_scopes_with_none(
        self, scope: str, expected: list[str] | None
    ) -> None:
        script = _load_script()

        assert script.scope_patterns(scope) == expected

    def test_dirty_tree_scope_lists_modified_and_untracked_files_but_not_agent_directories(
        self, git_repo: Path
    ) -> None:
        script = _load_script()
        (git_repo / "tracked.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.py"], cwd=git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "add tracked"], cwd=git_repo, check=True, capture_output=True)
        (git_repo / "tracked.py").write_text("x = 2\n", encoding="utf-8")
        (git_repo / "new.py").write_text("y = 1\n", encoding="utf-8")
        (git_repo / ".agentic").mkdir()
        (git_repo / ".agentic" / "plan.md").write_text("plan\n", encoding="utf-8")

        scope = script.resolve_scope(git_repo, None, None)

        assert scope == {"kind": "worktree", "ref": None, "files": ["new.py", "tracked.py"]}

    def test_explicit_range_scope_lists_the_files_changed_in_that_range(self, git_repo: Path) -> None:
        script = _load_script()
        (git_repo / "a.py").write_text("a = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "a.py"], cwd=git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "add a"], cwd=git_repo, check=True, capture_output=True)

        scope = script.resolve_scope(git_repo, "HEAD~1...HEAD", None)

        assert scope == {"kind": "range", "ref": "HEAD~1...HEAD", "files": ["a.py"]}


class ChangeAuditGenerateTests:
    def test_only_rules_whose_scope_contains_a_changed_file_get_criteria(self, tmp_path: Path) -> None:
        script = _load_script()
        root = _root_with_checklist(tmp_path, [SRC_RULE, TEST_RULE])

        document = _audit(script, root, "coding", ["src/dovo/core/db/models.py"])

        assert [entry["id"] for entry in document["criteria"]] == ["ARCH-001.Q01", "ARCH-001.Q02"]

    def test_process_rules_are_listed_as_skipped_instead_of_silently_dropped(self, tmp_path: Path) -> None:
        script = _load_script()
        root = _root_with_checklist(tmp_path, [SRC_RULE, PROCESS_RULE])

        document = _audit(script, root, "coding", ["src/dovo/core/db/models.py"])

        assert document["skipped_rules"] == [{"rule": "CI-002", "scope": "Git commits & PRs"}]

    def test_every_new_criterion_is_unanswered_and_carries_its_na_condition(self, tmp_path: Path) -> None:
        script = _load_script()
        root = _root_with_checklist(tmp_path, [SRC_RULE])

        document = _audit(script, root, "review", ["src/dovo/core/db/models.py"])

        assert [(e["verdict"], e["evidence"], e["na_when"]) for e in document["criteria"]] == [
            (None, None, None),
            (None, None, "No import."),
        ]

    def test_regenerating_keeps_answers_whose_statement_is_unchanged(self, tmp_path: Path) -> None:
        script = _load_script()
        root = _root_with_checklist(tmp_path, [SRC_RULE])
        first = _audit(script, root, "coding", ["src/dovo/core/db/models.py"])
        _answer_all(first)

        again, dropped = script.build_audit(root, "coding", first["scope"], first)

        assert [e["verdict"] for e in again["criteria"]] == ["PASS", "PASS"]
        assert dropped == []

    def test_regenerating_drops_the_answer_of_a_criterion_whose_statement_changed(self, tmp_path: Path) -> None:
        script = _load_script()
        root = _root_with_checklist(tmp_path, [SRC_RULE])
        first = _audit(script, root, "coding", ["src/dovo/core/db/models.py"])
        _answer_all(first)
        reworded = {
            **SRC_RULE,
            "criteria": [{**SRC_RULE["criteria"][0], "statement": "Changed."}, SRC_RULE["criteria"][1]],
        }
        (root / "docs" / "agents" / "REVIEW_CHECKLIST.json").write_text(
            json.dumps({"rules": [reworded]}), encoding="utf-8"
        )

        again, dropped = script.build_audit(root, "coding", first["scope"], first)

        assert [e["verdict"] for e in again["criteria"]] == [None, "PASS"]
        assert dropped == ["ARCH-001.Q01"]


class ChangeAuditAnswerTests:
    def test_answering_one_criterion_leaves_every_other_answer_untouched(self, tmp_path: Path) -> None:
        script = _load_script()
        document = _audit(script, _root_with_checklist(tmp_path, [SRC_RULE]), "coding", ["src/dovo/core/a.py"])
        document["criteria"][0]["verdict"], document["criteria"][0]["evidence"] = "PASS", "src/dovo/core/a.py:1 ok"

        problems = script.apply_answers(document, {"ARCH-001.Q02": {"verdict": "N/A", "evidence": "no import added"}})

        assert problems == []
        assert [(e["verdict"], e["evidence"]) for e in document["criteria"]] == [
            ("PASS", "src/dovo/core/a.py:1 ok"),
            ("N/A", "no import added"),
        ]

    @pytest.mark.parametrize(
        ("answers", "message"),
        [
            pytest.param(
                {"NOPE.Q01": {"verdict": "PASS", "evidence": "x"}}, "not a criterion of this audit", id="unknown_id"
            ),
            pytest.param(
                {"ARCH-001.Q01": {"verdict": "MAYBE", "evidence": "x"}}, "verdict must be one of", id="bad_verdict"
            ),
            pytest.param(
                {"ARCH-001.Q01": {"verdict": "PASS", "evidence": "  "}}, "evidence is empty", id="blank_evidence"
            ),
            pytest.param(
                {"ARCH-001.Q01": {"verdict": "FAIL", "evidence": "looks wrong"}},
                "needs a path:line",
                id="fail_without_citation",
            ),
        ],
    )
    def test_an_invalid_answer_is_rejected_and_nothing_is_written(
        self, tmp_path: Path, answers: dict[str, dict[str, str]], message: str
    ) -> None:
        script = _load_script()
        document = _audit(script, _root_with_checklist(tmp_path, [SRC_RULE]), "coding", ["src/dovo/core/a.py"])

        problems = script.apply_answers(document, answers)

        assert any(message in problem for problem in problems)
        assert [e["verdict"] for e in document["criteria"]] == [None, None]

    def test_answers_may_be_given_as_a_list_of_objects_with_ids(self) -> None:
        script = _load_script()

        parsed = script.parse_answers([{"id": "A.Q01", "verdict": "PASS", "evidence": "x"}])

        assert parsed == {"A.Q01": {"id": "A.Q01", "verdict": "PASS", "evidence": "x"}}


class ChangeAuditValidateTests:
    def test_a_complete_audit_has_no_problems(self, tmp_path: Path) -> None:
        script = _load_script()
        root = _root_with_checklist(tmp_path, [SRC_RULE])
        document = _audit(script, root, "review", ["src/dovo/core/a.py"])
        _answer_all(document)

        assert script.validation_problems(document, "review", root) == []

    def test_each_unanswered_criterion_is_reported_by_id(self, tmp_path: Path) -> None:
        script = _load_script()
        root = _root_with_checklist(tmp_path, [SRC_RULE])
        document = _audit(script, root, "review", ["src/dovo/core/a.py"])

        problems = script.validation_problems(document, "review", root)

        assert problems == ["ARCH-001.Q01: unanswered", "ARCH-001.Q02: unanswered"]

    def test_a_criterion_deleted_from_the_file_is_reported_as_missing(self, tmp_path: Path) -> None:
        script = _load_script()
        root = _root_with_checklist(tmp_path, [SRC_RULE])
        document = _audit(script, root, "review", ["src/dovo/core/a.py"])
        del document["criteria"][0]
        _answer_all(document)

        problems = script.validation_problems(document, "review", root)

        assert problems == ["ARCH-001.Q01: missing from the audit file"]

    def test_an_edited_statement_is_reported_even_when_answered(self, tmp_path: Path) -> None:
        script = _load_script()
        root = _root_with_checklist(tmp_path, [SRC_RULE])
        document = _audit(script, root, "review", ["src/dovo/core/a.py"])
        _answer_all(document)
        document["criteria"][1]["statement"] = "Reworded to be easier to pass."

        problems = script.validation_problems(document, "review", root)

        assert problems == ["ARCH-001.Q02: statement differs from the checklist"]

    def test_a_file_written_for_the_other_role_is_rejected(self, tmp_path: Path) -> None:
        script = _load_script()
        root = _root_with_checklist(tmp_path, [SRC_RULE])
        document = _audit(script, root, "coding", ["src/dovo/core/a.py"])

        problems = script.validation_problems(document, "review", root)

        assert problems == ["file role is 'coding', expected 'review'"]


class ChangeAuditCompareTests:
    def _write_pair(self, script: ModuleType, root: Path, review_verdict: str) -> tuple[Path, Path]:
        """Write a fully answered coding audit (PASS) and review audit (given verdict) and return their paths."""
        files = ["src/dovo/core/a.py"]
        coding, review = _audit(script, root, "coding", files), _audit(script, root, "review", files)
        _answer_all(coding)
        _answer_all(review, review_verdict)
        coding_path, review_path = root / "coding.json", root / "review.json"
        script.write_json(coding_path, coding)
        script.write_json(review_path, review)
        return coding_path, review_path

    def test_compare_refuses_to_open_the_coding_audit_until_the_review_audit_validates(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        script = _load_script()
        root = _root_with_checklist(tmp_path, [SRC_RULE])
        review = _audit(script, root, "review", ["src/dovo/core/a.py"])
        script.write_json(root / "review.json", review)
        (root / "coding.json").write_text("not json at all", encoding="utf-8")

        code = script.compare_command(root, root / "coding.json", root / "review.json")

        assert code == 2
        assert "must exist and validate before the coding audit is read" in capsys.readouterr().err

    def test_compare_lists_each_criterion_where_the_verdicts_differ(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        script = _load_script()
        root = _root_with_checklist(tmp_path, [SRC_RULE])
        coding_path, review_path = self._write_pair(script, root, "FAIL")

        code = script.compare_command(root, coding_path, review_path)

        output = capsys.readouterr().out
        assert code == 0
        assert "2 disagreements across 2 review criteria" in output
        assert "coding: PASS" in output
        assert "review: FAIL" in output

    def test_compare_reports_no_disagreements_when_the_audits_match(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        script = _load_script()
        root = _root_with_checklist(tmp_path, [SRC_RULE])
        coding_path, review_path = self._write_pair(script, root, "PASS")

        script.compare_command(root, coding_path, review_path)

        assert "0 disagreements" in capsys.readouterr().out


class ChangeAuditCommandLineTests:
    def test_generate_answer_validate_round_trip_through_the_command_line(self, tmp_path: Path) -> None:
        root = _root_with_checklist(tmp_path, [SRC_RULE])
        script_path = REPO_ROOT / "scripts" / "change_audit.py"
        base = [sys.executable, "-I", str(script_path), "--root", str(root)]

        generated = subprocess.run(
            [*base, "generate", "--role", "coding", "--paths", "src/dovo/core/a.py"], capture_output=True, text=True
        )
        early = subprocess.run([*base, "validate", "--role", "coding"], capture_output=True, text=True)
        answers = root / "answers.json"
        answers.write_text(
            json.dumps(
                {
                    "ARCH-001.Q01": {"verdict": "PASS", "evidence": "a.py:1"},
                    "ARCH-001.Q02": {"verdict": "N/A", "evidence": "none"},
                }
            ),
            encoding="utf-8",
        )
        answered = subprocess.run(
            [*base, "answer", "--role", "coding", "--from", str(answers)], capture_output=True, text=True
        )
        final = subprocess.run([*base, "validate", "--role", "coding"], capture_output=True, text=True)

        assert [generated.returncode, early.returncode, answered.returncode, final.returncode] == [0, 1, 0, 0]
        assert "OK" in final.stdout

    def test_answer_without_a_source_or_a_complete_single_answer_exits_two(self, tmp_path: Path) -> None:
        root = _root_with_checklist(tmp_path, [SRC_RULE])
        script_path = REPO_ROOT / "scripts" / "change_audit.py"

        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(script_path),
                "--root",
                str(root),
                "answer",
                "--role",
                "coding",
                "--id",
                "ARCH-001.Q01",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 2
