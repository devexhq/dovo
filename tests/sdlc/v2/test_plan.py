"""Tier 1 contract: sdlc v2's plan.py refuses a slice plan that leaves a question open or a decision malformed."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.lint.astlib import REPO_ROOT

SCRIPTS = REPO_ROOT / ".dovo/catalog/scripts/sdlc/v2"


def _load_script(name: str, module_name: str) -> ModuleType:
    """Load sdlc v2's <name>.py by path as module_name, since its folder is not a package."""
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_load_script("plan_formats", "plan_formats")
PLAN = _load_script("plan", "sdlc_v2_plan")

HUMAN = {
    "id": "D-3",
    "was": "OQ-4",
    "decision": "Required config's ConfigError carries the load's diagnostic records.",
    "settled_by": "human",
    "answer": "accept challenge",
    "rejected": ["A raise without the records (diagnostics: [])."],
    "phases": [6],
    "date": "2026-10-29",
}
ASSUMED = {
    "id": "D-4",
    "was": "OQ-5",
    "decision": "Discarded sessions stay hidden unless --all is passed.",
    "settled_by": "assumed_accepted",
    "rejected": ["Show them with a discarded status column."],
    "phases": [1],
    "date": "2026-10-29",
}
PLANNER = {
    "id": "D-5",
    "was": None,
    "decision": "The list reuses worktree_list's empty-state line.",
    "settled_by": "planner",
    "rejected": [],
    "phases": [],
    "date": "2026-10-29",
}


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Return an empty working directory with an .agentic folder."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agentic").mkdir()
    return tmp_path


def _write_plan(root: Path, open_questions: list[object], decisions: list[object]) -> None:
    """Write a slice plan.json with open_questions and decisions."""
    plan = {"issue": 914, "phases": 2, "open_questions": open_questions, "decisions": decisions}
    (root / ".agentic" / "plan.json").write_text(json.dumps(plan), encoding="utf-8")


def _main(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    """Run plan.py's main with argv."""
    monkeypatch.setattr(sys, "argv", ["plan.py", *argv])
    return PLAN.main()


def _check(monkeypatch: pytest.MonkeyPatch) -> int:
    """Run plan.py check-questions on .agentic/plan.json."""
    return _main(monkeypatch, "check-questions", "--plan", ".agentic/plan.json")


class PlanCheckQuestionsTests:
    def test_check_questions_passes_a_plan_with_every_question_settled(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_plan(workspace, [], [HUMAN, ASSUMED, PLANNER])

        assert _check(monkeypatch) == 0

        assert capsys.readouterr() == ("", "")

    def test_check_questions_passes_a_plan_with_no_decisions(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_plan(workspace, [], [])

        assert _check(monkeypatch) == 0

        assert capsys.readouterr() == ("", "")

    def test_check_questions_reads_the_default_plan_path(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_plan(workspace, [{"id": "OQ-2"}], [])

        assert _main(monkeypatch, "check-questions") == 1

        assert capsys.readouterr().err.startswith("plan.py: .agentic/plan.json still lists open questions OQ-2;")

    def test_check_questions_refuses_a_question_still_open(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_plan(workspace, [{"id": "OQ-2", "summary": "Hide them? (assumed: yes)"}, {"summary": "No id"}], [HUMAN])

        assert _check(monkeypatch) == 1

        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == (
            "plan.py: .agentic/plan.json still lists open questions OQ-2, at position 2; "
            "fold-answers must settle each one as a decision.\n"
        )

    @pytest.mark.parametrize(
        ("decision", "message"),
        [
            pytest.param(
                {key: value for key, value in HUMAN.items() if key != "settled_by"},
                "Decision D-3 has no settled_by.",
                id="no-settled-by",
            ),
            pytest.param(
                {**HUMAN, "settled_by": "reviewer"},
                "Decision D-3 has settled_by 'reviewer'; it must be one of human, assumed_accepted, planner.",
                id="unknown-settled-by",
            ),
            pytest.param(
                {**HUMAN, "id": "OQ-4"},
                "Decision 1 has id 'OQ-4'; it must be D-<n>.",
                id="question-id",
            ),
            pytest.param("D-3", "Decision 1 is not a JSON object.", id="not-an-object"),
            pytest.param({**HUMAN, "decision": " "}, "Decision D-3 has no decision.", id="empty-decision"),
            pytest.param(
                {key: value for key, value in HUMAN.items() if key != "answer"},
                "Decision D-3 has no answer.",
                id="human-without-answer",
            ),
            pytest.param(
                {**ASSUMED, "answer": "yes"},
                "Decision D-4 has an answer, but only a decision settled_by human has one.",
                id="assumed-with-answer",
            ),
            pytest.param(
                {**ASSUMED, "was": None},
                "Decision D-4 has was None; it must be the id of the open question it settles.",
                id="assumed-without-question",
            ),
            pytest.param(
                {**PLANNER, "was": "D-2"},
                "Decision D-5 has was 'D-2'; it must be the id of the open question it settles.",
                id="planner-was-not-a-question",
            ),
            pytest.param(
                {**HUMAN, "rejected": "A raise"},
                "Decision D-3 has no rejected list of strings.",
                id="rejected-not-a-list",
            ),
            pytest.param(
                {**HUMAN, "phases": [True]},
                "Decision D-3 has no phases list of phase numbers.",
                id="phases-not-numbers",
            ),
            pytest.param(
                {**HUMAN, "date": "2026-02-30"},
                "Decision D-3 has date '2026-02-30'; it must be a YYYY-MM-DD date.",
                id="impossible-date",
            ),
            pytest.param(
                {**HUMAN, "date": "20261029"},
                "Decision D-3 has date '20261029'; it must be a YYYY-MM-DD date.",
                id="compact-date",
            ),
        ],
    )
    def test_check_questions_refuses_a_malformed_decision_naming_the_field(
        self,
        decision: object,
        message: str,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_plan(workspace, [], [decision])

        assert _check(monkeypatch) == 1

        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == f"plan.py: .agentic/plan.json: {message}\n"

    def test_check_questions_refuses_a_decision_id_used_twice(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_plan(workspace, [], [HUMAN, {**ASSUMED, "id": "D-3"}])

        assert _check(monkeypatch) == 1

        assert capsys.readouterr().err == "plan.py: .agentic/plan.json: Decision D-3 appears twice.\n"

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            pytest.param(
                "not json",
                ".agentic/plan.json cannot be read: Expecting value: line 1 column 1 (char 0).",
                id="not-json",
            ),
            pytest.param("[]", ".agentic/plan.json is not a JSON object.", id="not-an-object"),
            pytest.param('{"decisions": []}', ".agentic/plan.json has no open_questions list.", id="no-open-questions"),
            pytest.param('{"open_questions": []}', ".agentic/plan.json has no decisions list.", id="no-decisions"),
        ],
    )
    def test_check_questions_refuses_an_unreadable_plan(
        self,
        text: str,
        message: str,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        (workspace / ".agentic" / "plan.json").write_text(text, encoding="utf-8")

        assert _check(monkeypatch) == 1

        assert capsys.readouterr() == ("", f"plan.py: {message}\n")

    def test_check_questions_refuses_a_missing_plan(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert _check(monkeypatch) == 1

        assert capsys.readouterr().err.startswith("plan.py: .agentic/plan.json cannot be read: ")
