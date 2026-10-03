from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dovo.core.agents.mutation_git import (
    MutationGitError,
    capture_diff_since,
    discard_since,
    resolve_pre_agent_baseline,
)


class DirectMutationGitSafetyTests:
    """Integration tests verifying pre-agent WIP preservation during direct mutation rollbacks."""

    def test_mutation_git_preserves_wip_baseline_when_agent_discards_changes(
        self,
        git_repo: Path,
    ) -> None:
        """Rollback restores pre-existing uncommitted WIP files and discards agent edits."""
        readme_file = git_repo / "README.md"
        readme_file.write_text("# Test Repo\nUncommitted WIP edit in README\n", encoding="utf-8")

        wip_file = git_repo / "feature_wip.py"
        wip_file.write_text("print('uncommitted WIP code')\n", encoding="utf-8")

        baseline = resolve_pre_agent_baseline(git_repo)

        readme_file.write_text("# Test Repo\nAgent corrupted README\n", encoding="utf-8")
        wip_file.write_text("print('agent modified this file')\n", encoding="utf-8")
        agent_junk = git_repo / "agent_garbage.tmp"
        agent_junk.write_text("agent temporary junk\n", encoding="utf-8")

        agent_diff, agent_touched = capture_diff_since(git_repo, baseline)
        assert agent_diff != ""
        assert agent_touched == ["README.md", "agent_garbage.tmp", "feature_wip.py"]

        discard_since(git_repo, baseline)

        assert readme_file.read_text(encoding="utf-8") == "# Test Repo\nUncommitted WIP edit in README\n"
        assert wip_file.read_text(encoding="utf-8") == "print('uncommitted WIP code')\n"
        assert agent_junk.exists() is False

        post_discard_diff, post_discard_touched = capture_diff_since(git_repo, baseline)
        assert post_discard_diff == ""
        assert post_discard_touched == []


class ResolvePreAgentBaselineTests:
    def test_clean_tree_baselines_to_head(self, git_repo: Path) -> None:
        """A clean working tree baselines to the current HEAD, unchanged."""
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(git_repo),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        baseline = resolve_pre_agent_baseline(git_repo)

        assert baseline == head

    def test_dirty_tree_creates_marker_commit(self, git_repo: Path) -> None:
        """A dirty tree is committed under a fixed marker message, preserving its content."""
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(git_repo),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        (git_repo / "README.md").write_text("wip change\n", encoding="utf-8")

        baseline = resolve_pre_agent_baseline(git_repo)

        assert baseline != head
        log = subprocess.run(
            ["git", "log", "-1", "--pretty=%s"],
            cwd=str(git_repo),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert log == "dovo: pre-agent baseline"
        assert (git_repo / "README.md").read_text(encoding="utf-8") == "wip change\n"

    def test_raises_on_git_failure(self, tmp_path: Path) -> None:
        """A non-repository path raises MutationGitError."""
        not_a_repo = tmp_path / "not-a-repo"
        not_a_repo.mkdir()

        with pytest.raises(MutationGitError):
            resolve_pre_agent_baseline(not_a_repo)

    def test_raises_on_git_timeout(self, git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A git subprocess timeout raises MutationGitError tagged GIT_TIMEOUT."""
        import dovo.core.agents.mutation_git as mutation_mod

        def _timeout(*_args: object, **_kwargs: object) -> object:
            raise subprocess.TimeoutExpired(cmd=["git"], timeout=120)

        monkeypatch.setattr(mutation_mod.subprocess, "run", _timeout)

        with pytest.raises(MutationGitError, match="GIT_TIMEOUT"):
            resolve_pre_agent_baseline(git_repo)


class CaptureDiffSinceTests:
    def test_captures_modified_and_new_files(self, git_repo: Path) -> None:
        """Diff since baseline includes both a modified tracked file and a new file."""
        baseline = resolve_pre_agent_baseline(git_repo)
        (git_repo / "README.md").write_text("agent edit\n", encoding="utf-8")
        (git_repo / "new_file.py").write_text("new file\n", encoding="utf-8")

        diff, touched = capture_diff_since(git_repo, baseline)

        assert touched == ["README.md", "new_file.py"]
        assert "agent edit" in diff
        assert "new file" in diff

    def test_captures_agent_own_commits(self, git_repo: Path) -> None:
        """Diff since baseline includes changes the agent itself committed."""
        baseline = resolve_pre_agent_baseline(git_repo)
        (git_repo / "README.md").write_text("committed by agent\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(git_repo), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "agent commit"], cwd=str(git_repo), check=True, capture_output=True)

        diff, touched = capture_diff_since(git_repo, baseline)

        assert touched == ["README.md"]
        assert "committed by agent" in diff

    def test_empty_diff_when_no_changes(self, git_repo: Path) -> None:
        """No changes since baseline yields an empty diff and no touched files."""
        baseline = resolve_pre_agent_baseline(git_repo)

        diff, touched = capture_diff_since(git_repo, baseline)

        assert diff == ""
        assert touched == []


class DiscardSinceTests:
    def test_discards_agent_edits_on_clean_baseline(self, git_repo: Path) -> None:
        """Discard resets a modified tracked file and removes an untracked file."""
        baseline = resolve_pre_agent_baseline(git_repo)
        (git_repo / "README.md").write_text("bad agent edit\n", encoding="utf-8")
        (git_repo / "junk.txt").write_text("untracked\n", encoding="utf-8")

        discard_since(git_repo, baseline)

        assert (git_repo / "README.md").read_text(encoding="utf-8") == "# Test Repo\n"
        assert not (git_repo / "junk.txt").exists()

    def test_discard_preserves_wip_baseline(self, git_repo: Path) -> None:
        """Discard restores the WIP-overlay baseline, not the original committed tip."""
        (git_repo / "README.md").write_text("wip content\n", encoding="utf-8")
        baseline = resolve_pre_agent_baseline(git_repo)
        (git_repo / "README.md").write_text("agent overwrote wip\n", encoding="utf-8")

        discard_since(git_repo, baseline)

        assert (git_repo / "README.md").read_text(encoding="utf-8") == "wip content\n"

    def test_raises_on_git_failure(self, git_repo: Path) -> None:
        """Discarding to a nonexistent ref raises MutationGitError."""
        with pytest.raises(MutationGitError):
            discard_since(git_repo, "not-a-real-ref")
