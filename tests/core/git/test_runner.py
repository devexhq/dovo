"""Integration tests for GitRunner plumbing commands against real git repositories."""

from __future__ import annotations

from pathlib import Path

from dovo.core.git.models import GitWorktreeEntry
from dovo.core.git.runner import GitRunner


class GitRunnerPlumbingTests:
    """Integration tests for GitRunner plumbing commands against real git repositories."""

    def test_worktree_add_creates_new_working_tree_and_branch(self, git_repo: Path, tmp_path: Path) -> None:
        """Add worktree creates directory and sets active branch."""
        worktree_path = tmp_path / "feature-worktree"

        GitRunner.worktree_add(git_repo, worktree_path, "feature-worktree", "main")

        assert worktree_path.is_dir() is True
        assert GitRunner.get_current_branch(worktree_path) == "feature-worktree"

        entries = GitRunner.worktree_list(git_repo)
        worktree_entries = [e for e in entries if e.path.resolve() == worktree_path.resolve()]
        assert len(worktree_entries) == 1
        assert worktree_entries[0] == GitWorktreeEntry(
            path=worktree_path.resolve(),
            head_sha=GitRunner.rev_parse(worktree_path, rev="HEAD"),
            branch="feature-worktree",
        )

    def test_worktree_remove_detaches_and_cleans_directory(self, git_repo: Path, tmp_path: Path) -> None:
        """Remove worktree deletes directory and unregisters worktree."""
        worktree_path = tmp_path / "feature-remove"
        GitRunner.worktree_add(git_repo, worktree_path, "feature-remove", "main")
        assert worktree_path.is_dir() is True

        GitRunner.worktree_remove(git_repo, worktree_path, force=True)

        assert worktree_path.exists() is False
        entries = GitRunner.worktree_list(git_repo)
        matching = [e for e in entries if e.path.resolve() == worktree_path.resolve()]
        assert matching == []

    def test_has_uncommitted_changes_detects_staged_and_unstaged_files(self, git_repo: Path) -> None:
        """Detect modified, staged, untracked, and deleted files."""
        assert GitRunner.has_uncommitted_changes(git_repo) is False

        readme = git_repo / "README.md"
        readme.write_text("# Modified\n", encoding="utf-8")
        assert GitRunner.has_uncommitted_changes(git_repo) is True

        GitRunner.run(["add", "README.md"], path=git_repo)
        assert GitRunner.has_uncommitted_changes(git_repo) is True

        GitRunner.run(["commit", "-m", "Commit mod"], path=git_repo)
        assert GitRunner.has_uncommitted_changes(git_repo) is False

        new_file = git_repo / "untracked.txt"
        new_file.write_text("untracked\n", encoding="utf-8")
        assert GitRunner.has_uncommitted_changes(git_repo) is True

        new_file.unlink()
        assert GitRunner.has_uncommitted_changes(git_repo) is False

        readme.unlink()
        assert GitRunner.has_uncommitted_changes(git_repo) is True

    def test_diff_generates_valid_unified_patch(self, git_repo: Path) -> None:
        """Generate valid unified diff against base commit."""
        base_sha = GitRunner.rev_parse(git_repo, rev="HEAD")
        readme = git_repo / "README.md"
        readme.write_text("# Test Repo\n\nAdded line in patch\n", encoding="utf-8")

        patch = GitRunner.diff(git_repo, base_commit=base_sha)

        assert patch.startswith("diff --git a/README.md b/README.md")
        assert "--- a/README.md" in patch
        assert "+++ b/README.md" in patch
        assert "+Added line in patch" in patch

    def test_get_current_branch_handles_detached_head_gracefully(self, git_repo: Path) -> None:
        """Detached HEAD returns 'HEAD (detached)'."""
        GitRunner.run(["checkout", "--detach", "HEAD"], path=git_repo)

        assert GitRunner.get_current_branch(git_repo) == "HEAD (detached)"
