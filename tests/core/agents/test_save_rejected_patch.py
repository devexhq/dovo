"""Tests for saving a rejected direct-mutation edit as a patch."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.core.agents.mutation_git import MutationGitError, resolve_pre_agent_baseline
from dovo.core.agents.save_rejected_patch import save_rejected_patch


class SaveRejectedPatchTests:
    def test_changed_worktree_writes_patch_to_destination_and_returns_it(self, git_repo: Path, tmp_path: Path) -> None:
        baseline = resolve_pre_agent_baseline(git_repo)
        (git_repo / "README.md").write_text("# Test Repo\nedited\n", encoding="utf-8")
        (git_repo / "added.txt").write_text("added\n", encoding="utf-8")
        destination = tmp_path / "rejected" / "code_attempt_1.patch"

        saved = save_rejected_patch(git_repo, baseline, destination)

        patch_text = destination.read_text(encoding="utf-8")
        assert saved == destination
        assert "README.md" in patch_text
        assert "added.txt" in patch_text

    def test_empty_change_set_writes_no_file_and_returns_none(self, git_repo: Path, tmp_path: Path) -> None:
        baseline = resolve_pre_agent_baseline(git_repo)
        destination = tmp_path / "rejected" / "code_attempt_1.patch"

        saved = save_rejected_patch(git_repo, baseline, destination)

        assert saved is None
        assert not destination.exists()

    def test_unwritable_destination_raises_mutation_git_error_without_touching_worktree(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        baseline = resolve_pre_agent_baseline(git_repo)
        (git_repo / "README.md").write_text("agent text\n", encoding="utf-8")
        blocker = tmp_path / "blocker"
        blocker.write_text("file", encoding="utf-8")

        with pytest.raises(MutationGitError, match="cannot write"):
            save_rejected_patch(git_repo, baseline, blocker / "code_attempt_1.patch")

        assert (git_repo / "README.md").read_text(encoding="utf-8") == "agent text\n"
