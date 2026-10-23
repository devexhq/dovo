"""Tier 1 contract: scripts/pr.py records the pull request it pushed to in .agentic/pr-url."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from tests.lint.astlib import REPO_ROOT

BRANCH = "alice/fix"
PR_URL = "https://github.com/o/r/pull/7"


def _load_script() -> ModuleType:
    """Load scripts/pr.py by path, since scripts/ is not a package."""
    spec = importlib.util.spec_from_file_location("pr_under_test", REPO_ROOT / "scripts" / "pr.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PR = _load_script()


class PrTests:
    @pytest.mark.parametrize(
        ("existing", "expected_out"),
        [
            pytest.param(True, f"Open PR already exists: {PR_URL}\n", id="found"),
            pytest.param(False, f"Opened PR: {PR_URL}\n", id="created"),
        ],
    )
    def test_main_writes_pr_url(
        self,
        existing: bool,
        expected_out: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        agentic = tmp_path / ".agentic"
        agentic.mkdir()
        for name in ("commit-msg", "pr-title", "pr-description"):
            (agentic / name).write_text("feat(x): y\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["pr.py"])
        monkeypatch.setattr(PR, "run", _fake_run(existing=existing))

        assert PR.main() == 0

        assert capsys.readouterr().out.endswith(expected_out)
        assert (agentic / "pr-url").read_text(encoding="utf-8") == f"{PR_URL}\n"
        assert sorted(path.name for path in agentic.iterdir()) == ["pr-url"]


def _fake_run(*, existing: bool) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Return a `run` double answering the git and gh calls of one pr.py run on BRANCH.

    The branch has a staged change and an upstream; `gh pr view` finds PR_URL when existing,
    otherwise it fails and `gh pr create` prints PR_URL.
    """

    def fake_run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        returncode, stdout = _answer(args, existing)
        return subprocess.CompletedProcess(list(args), returncode, stdout=stdout, stderr="")

    return fake_run


def _answer(args: tuple[str, ...], existing: bool) -> tuple[int, str]:
    """Return the exit code and stdout of one git or gh call."""
    answers: dict[tuple[str, ...], tuple[int, str]] = {
        ("gh", "repo", "view"): (0, "main\n"),
        ("git", "rev-parse", "--abbrev-ref", "HEAD"): (0, f"{BRANCH}\n"),
        ("git", "diff", "--cached", "--quiet"): (1, ""),
        ("git", "rev-parse", "--short", "HEAD"): (0, "abc1234\n"),
        ("gh", "pr", "view", BRANCH): (0, f"{PR_URL}\n") if existing else (1, ""),
        ("gh", "pr", "create"): (0, f"Creating pull request\n{PR_URL}\n"),
    }
    for prefix, answer in answers.items():
        if args[: len(prefix)] == prefix:
            return answer
    assert args[0] == "git", f"ran {args}"
    return 0, ""
