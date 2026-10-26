"""Tier 1 contract: scripts/publish_grade.py computes, checks and posts the PR's one dovo-grade comment."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.lint.astlib import REPO_ROOT

HEAD = "a" * 40
PR_URL = "https://github.com/o/r/pull/7"
NAMES = (
    "correctness",
    "contract_fidelity",
    "test_quality",
    "architecture_fit",
    "simplicity_scope",
    "docs",
    "reviewability",
    "process_efficiency",
)
WEIGHTS = (25, 15, 15, 15, 10, 5, 5, 10)
GATES = {"checks_failed": False, "open_blockers": 0}


def _load_script(name: str, module_name: str) -> ModuleType:
    """Load scripts/<name>.py by path as module_name, since scripts/ is not a package."""
    spec = importlib.util.spec_from_file_location(module_name, REPO_ROOT / "scripts" / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


PUBLISH = _load_script("publish_review", "publish_review")
GRADER = _load_script("publish_grade", "publish_grade_under_test")


def _scores(*scores: int | None) -> dict[str, int | None]:
    """Return the eight dimension scores by name, in rubric order."""
    return dict(zip(NAMES, scores, strict=True))


def _grade(scores: tuple[int | None, ...] = (4, 4, 3, 3, 4, 4, 2, 3), **overrides: object) -> dict[str, object]:
    """Return a consistent grade.json payload for scores, with overrides applied last."""
    grade: dict[str, object] = {
        "rubric": "dovo-grade@1",
        "pr": 7,
        "head": HEAD,
        "run_session": None,
        "provenance": "fresh_session",
        "dimensions": [
            {"id": name, "weight": weight, "score": score, "evidence": "src/dovo/x.py:1"}
            for name, weight, score in zip(NAMES, WEIGHTS, scores, strict=True)
        ],
        "gates": dict(GATES),
        "raw_score": 88,
        "score": 88,
        "grade": "B",
        "caps": [],
        "summary": "Good.",
        "levers": [],
    }
    grade.update(overrides)
    return grade


def _inputs(gates: dict[str, object] | None = None) -> dict[str, object]:
    """Return a grade-inputs.json payload for PR 7 at HEAD with gates."""
    return {"pr": {"number": 7, "head": HEAD}, "gates": dict(GATES) if gates is None else gates}


def _report(grade: str = "B", score: int = 88) -> str:
    """Return a grade.md whose header lines agree with a grade of grade (score) at HEAD."""
    return f"## dovo-grade - PR #7\n\n**Grade:** {grade} ({score}) · **Rubric:** dovo-grade@1\n**Graded commit:** `{HEAD}`\n"


def _write_grade(root: Path, grade: dict[str, object], report: str) -> None:
    """Write grade.json, grade.md and grade-inputs.json under root/.agentic."""
    agentic = root / ".agentic"
    agentic.mkdir()
    (agentic / "grade.json").write_text(json.dumps(grade), encoding="utf-8")
    (agentic / "grade.md").write_text(report, encoding="utf-8")
    (agentic / "grade-inputs.json").write_text(json.dumps(_inputs()), encoding="utf-8")


class PublishGradeScoreTests:
    @pytest.mark.parametrize(
        ("score", "letter"),
        [(100, "A"), (90, "A"), (89, "B"), (80, "B"), (79, "C"), (70, "C"), (69, "D"), (60, "D"), (59, "F"), (0, "F")],
    )
    def test_letter(self, score: int, letter: str) -> None:
        assert GRADER.letter(score) == letter

    @pytest.mark.parametrize(
        ("scores", "gates", "expected"),
        [
            pytest.param(
                _scores(4, 4, 4, 4, 4, 4, 4, 4),
                GATES,
                {"raw_score": 100, "score": 100, "grade": "A", "caps": []},
                id="perfect",
            ),
            pytest.param(
                _scores(4, 4, 3, 3, 4, 4, 2, 3),
                GATES,
                {"raw_score": 88, "score": 88, "grade": "B", "caps": []},
                id="weighted",
            ),
            pytest.param(
                _scores(4, 4, 4, 4, 4, 4, 4, None),
                GATES,
                {"raw_score": 100, "score": 100, "grade": "A", "caps": []},
                id="null-renormalises",
            ),
            pytest.param(
                _scores(3, None, None, None, None, 0, None, None),
                GATES,
                {"raw_score": 63, "score": 63, "grade": "D", "caps": []},
                id="rounds-half-up",
            ),
            pytest.param(
                _scores(4, 4, 4, 4, 4, 4, 4, 4),
                {"checks_failed": True, "open_blockers": 0},
                {"raw_score": 100, "score": 59, "grade": "F", "caps": ["checks_failed"]},
                id="failed-check",
            ),
            pytest.param(
                _scores(4, 4, 4, 4, 4, 4, 4, 4),
                {"checks_failed": False, "open_blockers": 2},
                {"raw_score": 100, "score": 69, "grade": "D", "caps": ["open_blockers"]},
                id="open-blockers",
            ),
            pytest.param(
                _scores(1, 4, 4, 4, 4, 4, 4, 4),
                {"checks_failed": False, "open_blockers": None},
                {"raw_score": 81, "score": 79, "grade": "C", "caps": ["correctness"]},
                id="low-correctness",
            ),
            pytest.param(
                _scores(1, 4, 4, 4, 4, 4, 4, 4),
                {"checks_failed": True, "open_blockers": 1},
                {"raw_score": 81, "score": 59, "grade": "F", "caps": ["checks_failed", "open_blockers", "correctness"]},
                id="every-cap",
            ),
        ],
    )
    def test_expected(
        self, scores: dict[str, int | None], gates: dict[str, object], expected: dict[str, object]
    ) -> None:
        assert GRADER.expected(scores, gates) == expected

    def test_raw_score_rejects_nothing_scored(self) -> None:
        with pytest.raises(GRADER.PublishGradeError, match="scores no dimension"):
            GRADER.raw_score(_scores(None, None, None, None, None, None, None, None))


def _with_dimension(index: int, **fields: object) -> dict[str, object]:
    """Return a consistent grade whose dimensions[index] has fields replaced."""
    grade = _grade()
    dimensions = grade["dimensions"]
    assert isinstance(dimensions, list)
    dimensions[index] = {**dimensions[index], **fields}
    return grade


class PublishGradeCheckTests:
    @pytest.mark.parametrize(
        ("grade", "inputs", "expected"),
        [
            pytest.param(_grade(), _inputs(), None, id="consistent"),
            pytest.param(
                {key: value for key, value in _grade().items() if key != "levers"},
                _inputs(),
                "grade.json has no 'levers'; run /dovo-grade again",
                id="missing-key",
            ),
            pytest.param(
                _grade(rubric="dovo-grade@0"),
                _inputs(),
                "grade.json rubric 'dovo-grade@0' is not dovo-grade@1",
                id="rubric",
            ),
            pytest.param(
                _with_dimension(0, id="docs"),
                _inputs(),
                "grade.json dimensions must list " + ", ".join(NAMES) + " in that order",
                id="order",
            ),
            pytest.param(
                _with_dimension(0, weight=30),
                _inputs(),
                "grade.json weight for correctness is 30; dovo-grade@1 weighs it 25",
                id="weight",
            ),
            pytest.param(
                _with_dimension(1, score=5),
                _inputs(),
                "grade.json score for contract_fidelity is 5; it must be an integer from 0 to 4 or null",
                id="score-range",
            ),
            pytest.param(
                _with_dimension(1, score=True),
                _inputs(),
                "grade.json score for contract_fidelity is True; it must be an integer from 0 to 4 or null",
                id="score-bool",
            ),
            pytest.param(
                _with_dimension(2, evidence=" "),
                _inputs(),
                "grade.json test_quality has no evidence; every score, and every null, cites why",
                id="evidence",
            ),
            pytest.param(
                _grade(head="b" * 40),
                _inputs(),
                f"grade.json grades #7 at {'b' * 40}, but the inputs are #7 at {HEAD}",
                id="head",
            ),
            pytest.param(
                _grade(),
                _inputs({"checks_failed": True, "open_blockers": 0}),
                "grade.json gates {'checks_failed': False, 'open_blockers': 0} differ from the inputs' "
                "{'checks_failed': True, 'open_blockers': 0}",
                id="gates",
            ),
            pytest.param(
                _grade(score=90, grade="A"),
                _inputs(),
                "grade.json says {'raw_score': 88, 'score': 90, 'grade': 'A', 'caps': []}, "
                "but dovo-grade@1 computes {'raw_score': 88, 'score': 88, 'grade': 'B', 'caps': []}",
                id="arithmetic",
            ),
        ],
    )
    def test_grade_problem(self, grade: dict[str, object], inputs: dict[str, object], expected: str | None) -> None:
        assert GRADER.grade_problem(grade, inputs) == expected

    @pytest.mark.parametrize(
        ("report", "expected"),
        [
            pytest.param(_report(), None, id="agrees"),
            pytest.param(
                _report("A", 90),
                "grade.md Grade line is '**Grade:** A (90) · **Rubric:** dovo-grade@1'; "
                "grade.json says '**Grade:** B (88) · **Rubric:** dovo-grade@1'",
                id="disagrees",
            ),
            pytest.param("## dovo-grade\n", "grade.md has no Grade line; run /dovo-grade again", id="missing"),
        ],
    )
    def test_report_problem(self, report: str, expected: str | None) -> None:
        assert GRADER.report_problem(_grade(), report) == expected

    @pytest.mark.parametrize(
        ("session", "session_line"),
        [
            pytest.param("blueprint_abba01ee", "Dovo session: `blueprint_abba01ee`\n", id="with-session"),
            pytest.param(None, "", id="without-session"),
        ],
    )
    def test_comment_body_stamps_header(self, session: str | None, session_line: str) -> None:
        body = GRADER.comment_body(_grade(), "## dovo-grade - PR #7\n", session=session)

        assert body == (
            "<!-- dovo-grade -->\n"
            f"Graded commit: `{HEAD}`\n"
            "Grade: B · Score: 88 · Rubric: dovo-grade@1\n"
            f"{session_line}"
            "\n"
            "## dovo-grade - PR #7\n"
        )

    def test_main_check_accepts_consistent_grade(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_grade(tmp_path, _grade(), _report())
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["publish_grade.py", "--check"])

        assert GRADER.main() == 0
        assert capsys.readouterr().out == "Grade is consistent: B (88), caps none\n"

    def test_main_check_refuses_inconsistent_grade(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_grade(tmp_path, _grade(score=90, grade="A"), _report("A", 90))
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["publish_grade.py", "--check"])

        assert GRADER.main() == 1
        assert capsys.readouterr().err == (
            "publish_grade.py: grade.json says {'raw_score': 88, 'score': 90, 'grade': 'A', 'caps': []}, "
            "but dovo-grade@1 computes {'raw_score': 88, 'score': 88, 'grade': 'B', 'caps': []}\n"
        )


class _FakeGh:
    """Stands in for gh: PR 7 at HEAD viewed by `me` with comments, recording each comment written."""

    def __init__(self, comments: list[dict[str, object]]) -> None:
        self.comments = comments
        self.written: list[tuple[str, str]] = []

    def __call__(self, *args: str) -> str:
        """Answer a pr view, login or comment listing, else record the comment write and return its URL."""
        if args[:3] == ("gh", "pr", "view"):
            return json.dumps({"number": 7, "headRefOid": HEAD, "url": PR_URL})
        if args[-2:] == ("--jq", ".login"):
            return "me"
        if "--paginate" in args:
            return "\n".join(json.dumps(comment) for comment in self.comments)
        body_arg = next(arg for arg in args if arg.startswith("body=@"))
        path = next(arg for arg in args if arg.startswith("repos/"))
        self.written.append((path, Path(body_arg.removeprefix("body=@")).read_text(encoding="utf-8")))
        return f"{PR_URL}#issuecomment-1"


class PublishGradePostTests:
    @pytest.mark.parametrize(
        ("comments", "endpoint"),
        [
            pytest.param([], "repos/o/r/issues/7/comments", id="posts-first-grade"),
            pytest.param(
                [{"id": 41, "user": "me", "body": "<!-- dovo-review -->\nReviewed"}],
                "repos/o/r/issues/7/comments",
                id="never-reuses-review-comment",
            ),
            pytest.param(
                [{"id": 42, "user": "me", "body": "<!-- dovo-grade -->\nGraded"}],
                "repos/o/r/issues/comments/42",
                id="updates-earlier-grade",
            ),
        ],
    )
    def test_main_posts_one_grade_comment(
        self,
        comments: list[dict[str, object]],
        endpoint: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_grade(tmp_path, _grade(), _report())
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["publish_grade.py", "--pr", "7", "--session", "blueprint_abba01ee"])
        fake_gh = _FakeGh(comments)
        monkeypatch.setattr(GRADER, "run", fake_gh)
        monkeypatch.setattr(PUBLISH, "run", fake_gh)

        assert GRADER.main() == 0
        assert [path for path, _ in fake_gh.written] == [endpoint]
        assert fake_gh.written[0][1].splitlines()[:4] == [
            "<!-- dovo-grade -->",
            f"Graded commit: `{HEAD}`",
            "Grade: B · Score: 88 · Rubric: dovo-grade@1",
            "Dovo session: `blueprint_abba01ee`",
        ]

    def test_main_refuses_moved_head(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_grade(tmp_path, _grade(), _report())
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["publish_grade.py", "--pr", "7"])

        def fake_run(*args: str) -> str:
            if args[:3] == ("gh", "pr", "view"):
                return json.dumps({"number": 7, "headRefOid": "b" * 40, "url": PR_URL})
            pytest.fail(f"ran {args}")

        monkeypatch.setattr(GRADER, "run", fake_run)

        assert GRADER.main() == 1
        assert capsys.readouterr().err == (
            f"publish_grade.py: the grade covers #7 at {HEAD}, but #7 is at {'b' * 40}\n"
        )
