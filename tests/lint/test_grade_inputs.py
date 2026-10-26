"""Tier 1 contract: scripts/grade_inputs.py collects the facts /dovo-grade scores a pull request from."""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import tarfile
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from tests.lint.astlib import REPO_ROOT

HEAD = "a" * 40
SESSION = "blueprint_abba01ee"
PR_URL = "https://github.com/o/r/pull/7"


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
FEEDBACK = _load_script("pr_feedback", "pr_feedback")
INPUTS = _load_script("grade_inputs", "grade_inputs_under_test")

SHOWN_SESSION = {
    "session": {"blueprint_name": "feature-dev", "status": "completed"},
    "steps": [
        {"kind": "step", "step_id": "plan", "state": "completed", "attempts": 1},
        {"kind": "loop", "step_id": "code-loop", "iterations": 2},
        {"kind": "step", "step_id": "verify", "state": "completed", "attempts": 1},
        {"kind": "step", "step_id": "push", "state": "completed", "attempts": 2},
        {"kind": "loop", "step_id": "review-loop", "iterations": 1},
        {"kind": "step", "step_id": "post-review", "state": "failed", "attempts": 1},
    ],
}
PROCESS = {
    "blueprint": "feature-dev",
    "status": "completed",
    "code_loop_iterations": 2,
    "review_loop_iterations": 1,
    "retried_steps": ["push"],
    "failed_steps": ["post-review"],
}


def _review(head: str = HEAD, blocking: int = 0) -> dict[str, object]:
    """Return a review.json payload of head with blocking findings and three coverage rows."""
    return {
        "head": head,
        "verdict": "APPROVE" if blocking == 0 else "CHANGES_REQUIRED",
        "action": "none" if blocking == 0 else "fix",
        "round": 2,
        "counts": {"blocking": blocking, "warnings": 1},
        "coverage": [{"verdict": "met"}, {"verdict": "met"}, {"verdict": "unmet"}],
    }


class GradeInputsSummaryTests:
    def test_check_summary_names_failed_and_pending_checks(self) -> None:
        checks = [
            {"name": "tests", "conclusion": "failure"},
            {"name": "lint", "conclusion": "success"},
            {"name": "docs", "conclusion": None},
            {"name": "build", "conclusion": "timed_out"},
        ]

        assert INPUTS.check_summary(checks) == {"total": 4, "failed": ["build", "tests"], "pending": ["docs"]}

    @pytest.mark.parametrize(
        ("review", "expected"),
        [
            pytest.param(None, None, id="no-review"),
            pytest.param(
                _review(),
                {
                    "head": HEAD,
                    "current": True,
                    "verdict": "APPROVE",
                    "action": "none",
                    "round": 2,
                    "counts": {"blocking": 0, "warnings": 1},
                    "coverage": {"met": 2, "unmet": 1},
                },
                id="current",
            ),
            pytest.param(
                _review(head="b" * 40),
                {
                    "head": "b" * 40,
                    "current": False,
                    "verdict": "APPROVE",
                    "action": "none",
                    "round": 2,
                    "counts": {"blocking": 0, "warnings": 1},
                    "coverage": {"met": 2, "unmet": 1},
                },
                id="stale",
            ),
        ],
    )
    def test_review_summary(self, review: dict[str, object] | None, expected: dict[str, object] | None) -> None:
        assert INPUTS.review_summary(review, HEAD) == expected

    def test_process_summary_reads_loops_and_step_outcomes(self) -> None:
        assert INPUTS.process_summary(SHOWN_SESSION) == PROCESS

    def test_process_summary_without_steps(self) -> None:
        assert INPUTS.process_summary({"session": None, "steps": None}) == {
            "blueprint": None,
            "status": None,
            "code_loop_iterations": None,
            "review_loop_iterations": None,
            "retried_steps": [],
            "failed_steps": [],
        }

    @pytest.mark.parametrize(
        ("failed", "review", "expected"),
        [
            pytest.param([], None, {"checks_failed": False, "open_blockers": None}, id="no-review"),
            pytest.param(["tests"], _review(blocking=2), {"checks_failed": True, "open_blockers": 2}, id="current"),
            pytest.param(
                [], _review(head="b" * 40, blocking=2), {"checks_failed": False, "open_blockers": None}, id="stale"
            ),
        ],
    )
    def test_gates(self, failed: list[str], review: dict[str, object] | None, expected: dict[str, object]) -> None:
        summary = INPUTS.review_summary(review, HEAD)

        assert INPUTS.gates({"total": 1, "failed": failed, "pending": []}, summary) == expected


def _archive(files: dict[str, str]) -> bytes:
    """Return a tar archive holding files, as `git archive --format=tar` would."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _view() -> dict[str, object]:
    """Return the `gh pr view` fields of PR 7 at HEAD."""
    return {
        "number": 7,
        "url": PR_URL,
        "title": "feat: thing",
        "body": "Does the thing.",
        "state": "OPEN",
        "headRefOid": HEAD,
        "baseRefName": "main",
        "additions": 10,
        "deletions": 2,
        "changedFiles": 1,
        "files": [{"path": "src/dovo/x.py", "additions": 10, "deletions": 2}],
    }


def _fake_archive(args: tuple[str, ...], **_: object) -> subprocess.CompletedProcess[bytes]:
    """Stand in for `git archive` of HEAD, returning a tar of one source file."""
    assert args == ("git", "archive", "--format=tar", HEAD)
    return subprocess.CompletedProcess(args, 0, stdout=_archive({"src/dovo/x.py": "x = 1\n"}))


def _restore_review(args: tuple[str, ...]) -> str:
    """Stand in for `dovo artifacts download`: write the review artifact, and fail for every other artifact."""
    if args[6] != "review":
        raise subprocess.CalledProcessError(1, args)
    dest = Path(args[-1]) / ".agentic"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "review.json").write_text(json.dumps(_review(blocking=1)), encoding="utf-8")
    return ""


class _FakeTools:
    """Stands in for every gh, git and dovo command grade_inputs runs, answering by the command's shape."""

    def __init__(self, comments: list[dict[str, object]]) -> None:
        self.routes: list[tuple[Callable[[tuple[str, ...]], bool], Callable[[tuple[str, ...]], str]]] = [
            (lambda a: a[:3] == ("gh", "pr", "view"), lambda _: json.dumps(_view())),
            (lambda a: a[:3] == ("gh", "pr", "diff"), lambda _: "diff --git a/src/dovo/x.py b/src/dovo/x.py"),
            (lambda a: a[-2:] == ("--jq", ".login"), lambda _: "me"),
            (
                lambda a: a[-1].startswith(".check_runs"),
                lambda _: json.dumps({"name": "tests", "conclusion": "failure"}),
            ),
            (lambda a: "--paginate" in a, lambda _: "\n".join(json.dumps(comment) for comment in comments)),
            (lambda a: a[3:5] == ("artifacts", "download"), _restore_review),
            (lambda a: a[3:5] == ("session", "show"), lambda _: json.dumps({"payload": SHOWN_SESSION})),
            (lambda a: a[:2] == ("git", "fetch"), lambda _: ""),
        ]

    def __call__(self, *args: str) -> str:
        """Return the output of the first route args matches."""
        handler = next(handler for matches, handler in self.routes if matches(args))
        return handler(args)


class GradeInputsCollectTests:
    @pytest.mark.parametrize(
        ("comments", "run_record", "review", "gates"),
        [
            pytest.param(
                [{"id": 3, "user": "me", "body": f"<!-- dovo-review -->\nDovo session: `{SESSION}`\n\nbody"}],
                {"session": SESSION, "restored": ["review"], "process": PROCESS},
                {
                    "head": HEAD,
                    "current": True,
                    "verdict": "CHANGES_REQUIRED",
                    "action": "fix",
                    "round": 2,
                    "counts": {"blocking": 1, "warnings": 1},
                    "coverage": {"met": 2, "unmet": 1},
                },
                {"checks_failed": True, "open_blockers": 1},
                id="dovo-run",
            ),
            pytest.param(
                [],
                {"session": None, "restored": [], "process": None},
                None,
                {"checks_failed": True, "open_blockers": None},
                id="no-dovo-run",
            ),
        ],
    )
    def test_main_writes_grade_inputs(
        self,
        comments: list[dict[str, object]],
        run_record: dict[str, object],
        review: dict[str, object] | None,
        gates: dict[str, object],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["grade_inputs.py", "--pr", "7"])
        fake_run = _FakeTools(comments)
        for module in (INPUTS, FEEDBACK, PUBLISH):
            monkeypatch.setattr(module, "run", fake_run)
        monkeypatch.setattr(INPUTS.subprocess, "run", _fake_archive)

        assert INPUTS.main() == 0

        document = json.loads((tmp_path / ".agentic" / "grade-inputs.json").read_text(encoding="utf-8"))
        assert document == {
            "pr": {
                "number": 7,
                "url": PR_URL,
                "title": "feat: thing",
                "body": "Does the thing.",
                "state": "OPEN",
                "baseRefName": "main",
                "additions": 10,
                "deletions": 2,
                "changedFiles": 1,
                "head": HEAD,
                "files": [{"path": "src/dovo/x.py", "additions": 10, "deletions": 2}],
            },
            "checks": {"total": 1, "failed": ["tests"], "pending": []},
            "run": run_record,
            "review": review,
            "gates": gates,
        }
        assert (tmp_path / ".agentic" / "grade-pr" / "src" / "dovo" / "x.py").read_text(encoding="utf-8") == "x = 1\n"
        assert (tmp_path / ".agentic" / "grade-pr.diff").read_text(encoding="utf-8") == (
            "diff --git a/src/dovo/x.py b/src/dovo/x.py\n"
        )
