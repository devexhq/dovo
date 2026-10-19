"""Integration tests for GitRunner plumbing commands against real git repositories."""

from __future__ import annotations

from pathlib import Path

from dovo.core.git.models import GitWorktreeEntry
from dovo.core.git.runner import GitRunner


class GitRunnerPlumbingTests:
    """Integration tests for GitRunner plumbing commands against real git repositories."""

    def test_worktree_add_creates_new_working_tree_and_branch(self, git_repo: Path, tmp_path: Path) -> None:
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
        worktree_path = tmp_path / "feature-remove"
        GitRunner.worktree_add(git_repo, worktree_path, "feature-remove", "main")
        assert worktree_path.is_dir() is True

        GitRunner.worktree_remove(git_repo, worktree_path, force=True)

        assert worktree_path.exists() is False
        entries = GitRunner.worktree_list(git_repo)
        matching = [e for e in entries if e.path.resolve() == worktree_path.resolve()]
        assert matching == []

    def test_branch_exists_reports_present_and_absent_local_branches(self, git_repo: Path) -> None:
        GitRunner.run(["branch", "dovo/dovo_present"], path=git_repo)

        assert GitRunner.branch_exists(git_repo, "dovo/dovo_present") is True
        assert GitRunner.branch_exists(git_repo, "dovo/dovo_absent") is False

    def test_has_uncommitted_changes_detects_staged_and_unstaged_files(self, git_repo: Path) -> None:
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
        base_sha = GitRunner.rev_parse(git_repo, rev="HEAD")
        readme = git_repo / "README.md"
        readme.write_text("# Test Repo\n\nAdded line in patch\n", encoding="utf-8")

        patch = GitRunner.diff(git_repo, base_commit=base_sha)

        assert patch.startswith("diff --git a/README.md b/README.md")
        assert "--- a/README.md" in patch
        assert "+++ b/README.md" in patch
        assert "+Added line in patch" in patch

    def test_get_current_branch_handles_detached_head_gracefully(self, git_repo: Path) -> None:
        GitRunner.run(["checkout", "--detach", "HEAD"], path=git_repo)

        assert GitRunner.get_current_branch(git_repo) == "HEAD (detached)"

    def test_commit_records_only_literal_paths_and_keeps_other_index_entries(self, git_repo: Path) -> None:
        (git_repo / "*.md").write_text("literal\n", encoding="utf-8")
        GitRunner.run(["--literal-pathspecs", "add", "*.md"], path=git_repo)
        (git_repo / "other.md").write_text("untracked\n", encoding="utf-8")
        (git_repo / "README.md").write_text("# Unstaged edit\n", encoding="utf-8")
        (git_repo / "staged.txt").write_text("staged\n", encoding="utf-8")
        GitRunner.run(["add", "staged.txt"], path=git_repo)

        GitRunner.commit(git_repo, "Commit literal path", paths=["*.md"])

        committed = GitRunner.run(["diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"], path=git_repo)
        assert committed == "*.md"
        assert GitRunner.status_porcelain(git_repo) == [" M README.md", "A  staged.txt", "?? other.md"]

    def test_dirty_paths_lists_unquoted_names_and_both_sides_of_rename(self, git_repo: Path) -> None:
        (git_repo / "orig.txt").write_text("rename me\n", encoding="utf-8")
        GitRunner.run(["add", "orig.txt"], path=git_repo)
        GitRunner.run(["commit", "-m", "Add orig.txt"], path=git_repo)
        GitRunner.run(["mv", "orig.txt", "moved.txt"], path=git_repo)
        (git_repo / "é_staged.txt").write_text("staged\n", encoding="utf-8")
        GitRunner.run(["add", "é_staged.txt"], path=git_repo)
        (git_repo / "é_untracked.txt").write_text("untracked\n", encoding="utf-8")
        readme = git_repo / "README.md"
        content = readme.read_text(encoding="utf-8")
        readme.unlink()
        (git_repo / "README.rst").write_text(content, encoding="utf-8")
        GitRunner.run(["add", "-N", "README.rst"], path=git_repo)

        assert GitRunner.dirty_paths(git_repo) == [
            "README.md",
            "README.rst",
            "moved.txt",
            "orig.txt",
            "é_staged.txt",
            "é_untracked.txt",
        ]

    def test_patch_paths_lists_unquoted_names_and_both_sides_of_rename(self, git_repo: Path) -> None:
        base_sha = GitRunner.rev_parse(git_repo, rev="HEAD")
        GitRunner.run(["mv", "README.md", "é renamed.md"], path=git_repo)
        (git_repo / "new\tfile.txt").write_text("new\n", encoding="utf-8")
        GitRunner.run(["add", "-A"], path=git_repo)
        diff_text = GitRunner.run(["diff", "--binary", "--cached", base_sha], path=git_repo)
        (git_repo / "unrelated.txt").write_text("not in the patch\n", encoding="utf-8")

        assert GitRunner.patch_paths(git_repo, diff_text) == ["README.md", "new\tfile.txt", "é renamed.md"]
