"""Contract tests for dovo.core.bootstrap.services.bootstrap.bootstrap_dovo."""

from __future__ import annotations

from pathlib import Path

from dovo.common.constants import DOVO_GITIGNORE_CONTENT
from dovo.core.bootstrap.services.bootstrap import bootstrap_dovo


class BootstrapDovoTests:
    """Contract tests for bootstrap_dovo's directory trimming and local .gitignore seeding."""

    def test_bootstrap_dovo_fresh_root_creates_gitignore_with_expected_content(self, tmp_path: Path) -> None:
        """bootstrap_dovo: fresh root creates only .meta/ and writes .dovo/.gitignore matching DOVO_GITIGNORE_CONTENT exactly, result.gitignore_created=True."""
        root_path = tmp_path / ".dovo"

        result = bootstrap_dovo(root_path)

        assert result.gitignore_created is True
        assert result.dirs_created == [result.root_path / ".meta"]
        assert (result.root_path / ".gitignore").read_text(encoding="utf-8") == DOVO_GITIGNORE_CONTENT

    def test_bootstrap_dovo_rerun_preserves_existing_gitignore(self, tmp_path: Path) -> None:
        """bootstrap_dovo: rerun on a root with a user-edited .gitignore returns gitignore_created=False and leaves its content untouched."""
        root_path = tmp_path / ".dovo"
        first_result = bootstrap_dovo(root_path)
        custom_content = "sandboxes/\ncustom-entry\n"
        (first_result.root_path / ".gitignore").write_text(custom_content, encoding="utf-8")

        result = bootstrap_dovo(root_path)

        assert result.gitignore_created is False
        assert (result.root_path / ".gitignore").read_text(encoding="utf-8") == custom_content
