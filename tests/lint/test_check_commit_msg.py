"""Tier 1 contract: scripts/check_commit_msg.py accepts semantic titles and rejects any other title."""

from __future__ import annotations

import importlib.util
from types import ModuleType

import pytest

from tests.lint.astlib import REPO_ROOT


def _load_checker() -> ModuleType:
    """Load scripts/check_commit_msg.py by path, since scripts/ is not a package."""
    path = REPO_ROOT / "scripts" / "check_commit_msg.py"
    spec = importlib.util.spec_from_file_location("check_commit_msg_under_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECKER = _load_checker()


class CommitMessageTests:
    @pytest.mark.parametrize(
        "message",
        [
            pytest.param("feat(agents): add claude provider\n\nBody.\n", id="scoped"),
            pytest.param("docs: trim AGENTS.md\n", id="unscoped"),
            pytest.param("refactor(core)!: drop legacy loader\n", id="breaking"),
            pytest.param("# comment line from git\nfix: handle empty config\n", id="leading-comment"),
            pytest.param("Merge pull request #1 from devexhq/branch\n", id="merge"),
            pytest.param('Revert "feat: add thing"\n', id="revert"),
            pytest.param("fixup! feat: add thing\n", id="fixup"),
            pytest.param("feat: add thing\n\nCo-Authored-By: Someone <a@b.c>\n", id="trailers-unchecked"),
        ],
    )
    def test_accepts(self, message: str) -> None:
        assert CHECKER.problems(message) == []

    @pytest.mark.parametrize(
        "message",
        [
            pytest.param("Update agent model\n", id="no-type"),
            pytest.param("feature: add thing\n", id="unknown-type"),
            pytest.param("feat:add thing\n", id="missing-space"),
        ],
    )
    def test_rejects(self, message: str) -> None:
        found = CHECKER.problems(message)

        assert len(found) == 1
        assert found[0].startswith("title must start with")
