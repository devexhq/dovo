"""Contract tests for dovo.core.bootstrap.services.bootstrap.bootstrap_dovo."""

from __future__ import annotations

from pathlib import Path

from dovo.common.constants import DOVO_GITIGNORE_CONTENT
from dovo.core.bootstrap.services.bootstrap import bootstrap_dovo


class BootstrapDovoTests:
    """Contract tests for bootstrap_dovo's directory trimming and local .gitignore seeding."""

    def test_bootstrap_dovo_fresh_root_creates_gitignore_with_expected_content(self, tmp_path: Path) -> None:
        root_path = tmp_path / ".dovo"

        result = bootstrap_dovo(root_path)

        assert result.gitignore_created is True
        assert result.dirs_created == [result.root_path / ".meta"]
        assert (result.root_path / ".gitignore").read_text(encoding="utf-8") == DOVO_GITIGNORE_CONTENT

    def test_bootstrap_dovo_rerun_appends_missing_entries_and_keeps_existing_lines(self, tmp_path: Path) -> None:
        root_path = tmp_path / ".dovo"
        first_result = bootstrap_dovo(root_path)
        (first_result.root_path / ".gitignore").write_text("worktrees/\ncustom-entry\n", encoding="utf-8")

        result = bootstrap_dovo(root_path)

        assert result.gitignore_created is False
        assert (result.root_path / ".gitignore").read_text(encoding="utf-8") == (
            "worktrees/\ncustom-entry\n.meta/\n.lock\nrun\n*.db\n*.db-journal\n*.db-wal\n"
        )

    def test_bootstrap_dovo_rerun_terminates_last_line_before_appending(self, tmp_path: Path) -> None:
        root_path = tmp_path / ".dovo"
        first_result = bootstrap_dovo(root_path)
        complete_but_run = DOVO_GITIGNORE_CONTENT.replace("run\n", "").rstrip("\n")
        (first_result.root_path / ".gitignore").write_text(complete_but_run, encoding="utf-8")

        result = bootstrap_dovo(root_path)

        assert (result.root_path / ".gitignore").read_text(encoding="utf-8") == complete_but_run + "\nrun\n"

    def test_bootstrap_dovo_rerun_leaves_complete_gitignore_unchanged(self, tmp_path: Path) -> None:
        root_path = tmp_path / ".dovo"
        first_result = bootstrap_dovo(root_path)
        complete_content = "# local\n" + DOVO_GITIGNORE_CONTENT + "catalog/index.json\n"
        (first_result.root_path / ".gitignore").write_text(complete_content, encoding="utf-8")

        result = bootstrap_dovo(root_path)

        assert result.gitignore_created is False
        assert (result.root_path / ".gitignore").read_text(encoding="utf-8") == complete_content
