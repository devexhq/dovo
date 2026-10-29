"""Tier 1 contract: scripts/code_progress.py picks, checks and records the phases of a slice the code loop commits."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.lint.astlib import REPO_ROOT


def _load_script() -> ModuleType:
    """Load scripts/code_progress.py by path, since scripts/ is not a package."""
    spec = importlib.util.spec_from_file_location(
        "code_progress_under_test", REPO_ROOT / "scripts" / "code_progress.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["code_progress_under_test"] = module
    spec.loader.exec_module(module)
    return module


PROGRESS = _load_script()


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Return a working directory whose .agentic/plan.json gives slice S3 the phases 1, 2 and 3."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DOVO_OUTPUT", raising=False)
    (tmp_path / ".agentic").mkdir()
    _write_plan(tmp_path, [1, 2, 3])
    return tmp_path


def _write_plan(root: Path, phases: list[int]) -> None:
    """Write a plan.json whose slice S3 lists phases, beside a slice S2 with none."""
    plan = {"issue": 1259, "slices": [{"id": "S2", "phases": []}, {"id": "S3", "phases": phases}]}
    (root / ".agentic" / "plan.json").write_text(json.dumps(plan), encoding="utf-8")


def _write_progress(root: Path, text: str) -> None:
    """Write text as .agentic/code-progress.json."""
    (root / ".agentic" / "code-progress.json").write_text(text, encoding="utf-8")


def _progress(root: Path) -> object:
    """Return the parsed .agentic/code-progress.json."""
    return json.loads((root / ".agentic" / "code-progress.json").read_text(encoding="utf-8"))


def _main(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    """Run code_progress.py's main with argv."""
    monkeypatch.setattr(sys, "argv", ["code_progress.py", *argv])
    return PROGRESS.main()


class CodeProgressNextTests:
    @pytest.mark.parametrize(
        ("done", "phase", "remaining"),
        [([], "1", 2), ([1], "2", 1), ([1, 2], "3", 0), ([1, 2, 3], "done", 0)],
    )
    def test_next_picks_the_first_phase_not_done(
        self,
        done: list[int],
        phase: str,
        remaining: int,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        output = workspace / "output"
        monkeypatch.setenv("DOVO_OUTPUT", str(output))
        _write_progress(workspace, json.dumps({"slice": "S3", "done": done}))

        assert _main(monkeypatch, "next", "--slice", "S3") == 0

        assert json.loads(capsys.readouterr().out) == {"phase": phase, "remaining": remaining}
        assert output.read_text(encoding="utf-8") == f"phase={phase}\nremaining={remaining}\n"

    def test_next_treats_a_missing_progress_record_as_empty(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert _main(monkeypatch, "next", "--slice", "S3") == 0

        assert json.loads(capsys.readouterr().out) == {"phase": "1", "remaining": 2}

    def test_next_refuses_a_slice_with_no_phases(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_plan(workspace, [])

        assert _main(monkeypatch, "next", "--slice", "S3") == 1

        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == (
            "code_progress.py: Slice S3 has no phases in .agentic/plan.json; run /issue-plan --slice S3 first.\n"
        )

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ('{"slice": "S2", "done": [1]}', ".agentic/code-progress.json is for slice S2, not S3."),
            (
                "not json",
                ".agentic/code-progress.json is not a progress record: Expecting value: line 1 column 1 (char 0).",
            ),
            (
                '{"slice": "S3"}',
                ".agentic/code-progress.json is not a progress record: it has no done list of phase numbers.",
            ),
        ],
    )
    def test_next_refuses_an_unreadable_progress_record(
        self,
        text: str,
        message: str,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_progress(workspace, text)

        assert _main(monkeypatch, "next", "--slice", "S3") == 1

        assert capsys.readouterr().err == f"code_progress.py: {message}\n"


class CodeProgressCheckTests:
    def test_check_refuses_a_phase_outside_the_slice(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert _main(monkeypatch, "check", "--slice", "S3", "--phase", "5") == 1

        assert (
            capsys.readouterr().err == "code_progress.py: Phase 5 is not a phase of slice S3; its phases are 1, 2, 3.\n"
        )

    def test_check_accepts_a_phase_of_the_slice(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert _main(monkeypatch, "check", "--slice", "S3", "--phase", "2") == 0

        assert capsys.readouterr() == ("", "")


class CodeProgressRecordTests:
    def test_record_adds_the_phase_to_done(self, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        assert _main(monkeypatch, "record", "--slice", "S3", "--phase", "1") == 0
        assert _progress(workspace) == {"slice": "S3", "done": [1]}

        assert _main(monkeypatch, "record", "--slice", "S3", "--phase", "2") == 0
        assert _main(monkeypatch, "record", "--slice", "S3", "--phase", "2") == 0

        assert _progress(workspace) == {"slice": "S3", "done": [1, 2]}
        assert sorted(path.name for path in (workspace / ".agentic").iterdir()) == ["code-progress.json", "plan.json"]

    def test_record_refuses_another_slice(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_progress(workspace, '{"slice": "S2", "done": [1]}')

        assert _main(monkeypatch, "record", "--slice", "S3", "--phase", "1") == 1

        assert capsys.readouterr().err == "code_progress.py: .agentic/code-progress.json is for slice S2, not S3.\n"
        assert _progress(workspace) == {"slice": "S2", "done": [1]}
