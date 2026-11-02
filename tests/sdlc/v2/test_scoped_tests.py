"""Tier 1 contract: sdlc v2's scoped_tests.py names the tests that cover the files a phase changed."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.lint.astlib import REPO_ROOT


def _load_script() -> ModuleType:
    """Load sdlc v2's scoped_tests.py by path, since its folder is not a package."""
    spec = importlib.util.spec_from_file_location(
        "sdlc_v2_scoped_tests", REPO_ROOT / ".dovo/catalog/scripts/sdlc/v2/scoped_tests.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["sdlc_v2_scoped_tests"] = module
    spec.loader.exec_module(module)
    return module


SCOPED = _load_script()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """Return a tree with the test layout the mapping reads: package and module test paths, tests/lint and tests/sdlc."""
    for directory in ("tests/core/sessions", "tests/cli/session", "tests/lint", "tests/sdlc", "tests/harness"):
        (tmp_path / directory).mkdir(parents=True)
    for test_file in ("tests/engine/test_guards.py", "tests/cli/session/test_list.py"):
        (tmp_path / test_file).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / test_file).write_text("", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    ("changed", "expected"),
    [
        pytest.param([], [], id="nothing-changed"),
        pytest.param(
            ["src/dovo/core/sessions/services/read_logs.py"], ["tests/core/sessions", "tests/lint"], id="package"
        ),
        pytest.param(["src/dovo/engine/guards.py"], ["tests/engine/test_guards.py", "tests/lint"], id="module"),
        pytest.param(["src/dovo/engine/notify.py"], ["tests/lint"], id="module-without-tests"),
        pytest.param(
            ["tests/cli/session/test_list.py"], ["tests/cli/session/test_list.py", "tests/lint"], id="test-file"
        ),
        pytest.param(["tests/harness/fakes.py"], ["tests/harness", "tests/lint"], id="test-helper"),
        pytest.param(["tests/conftest.py"], ["tests/lint"], id="root-conftest"),
        pytest.param(["tests/core/sessions/test_gone.py"], ["tests/lint"], id="deleted-test-file"),
        pytest.param([".dovo/catalog/blueprints/issue-code-v2.yml"], ["tests/lint", "tests/sdlc"], id="catalog"),
        pytest.param(["docs/cli/run.md"], ["tests/lint"], id="docs"),
        pytest.param(
            ["src/dovo/cli/session/commands/list.py", "tests/cli/session/test_list.py"],
            ["tests/cli/session", "tests/lint"],
            id="test-file-inside-package",
        ),
    ],
)
def test_scoped_paths_map_changed_files_to_the_tests_that_cover_them(
    root: Path, changed: list[str], expected: list[str]
) -> None:
    assert SCOPED.scoped_paths(changed, root) == expected


def test_changed_files_are_the_working_tree_changes_outside_agentic_and_run(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(git_repo)
    (git_repo / "README.md").write_text("changed\n", encoding="utf-8")
    for path in ("src/new.py", ".agentic/gate.log", ".dovo/run/state.json"):
        (git_repo / path).parent.mkdir(parents=True, exist_ok=True)
        (git_repo / path).write_text("", encoding="utf-8")
    subprocess.run(["git", "add", "src/new.py"], check=True, cwd=git_repo)

    assert sorted(SCOPED.changed_files()) == ["README.md", "src/new.py"]
