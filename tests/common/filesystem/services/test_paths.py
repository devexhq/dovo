"""Contract tests for repository-root discovery and derived path helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.common.filesystem.services.paths import (
    find_dovo_root,
    get_catalog_templates_dir,
    get_dovo_config_file,
    get_dovo_dir,
    get_gitignore_file,
)


class FindDovoRootTests:
    def test_dovo_config_json_present_returns_that_directory(self, tmp_path: Path) -> None:
        """[tier-1/integration] find_dovo_root: an ancestor with .dovo/config.json is returned directly."""
        root = tmp_path / "repo"
        (root / ".dovo").mkdir(parents=True)
        (root / ".dovo" / "config.json").write_text("{}", encoding="utf-8")

        assert find_dovo_root(root) == root.resolve()

    def test_dovo_dir_without_config_json_still_returns_that_directory(self, tmp_path: Path) -> None:
        """[tier-1/integration] find_dovo_root: an ancestor with a bare .dovo directory (no config.json yet) is returned directly."""
        root = tmp_path / "repo"
        (root / ".dovo").mkdir(parents=True)

        assert find_dovo_root(root) == root.resolve()

    def test_git_directory_present_without_dovo_returns_that_directory(self, tmp_path: Path) -> None:
        """[tier-1/integration] find_dovo_root: no .dovo anywhere, but an ancestor has .git -> that ancestor is returned."""
        root = tmp_path / "repo"
        (root / ".git").mkdir(parents=True)

        assert find_dovo_root(root) == root.resolve()

    def test_searches_upward_from_nested_start_directory(self, tmp_path: Path) -> None:
        """[tier-1/integration] find_dovo_root: starting several directories below the root, the nearest ancestor with .git is still found."""
        root = tmp_path / "repo"
        nested = root / "a" / "b" / "c"
        (root / ".git").mkdir(parents=True)
        nested.mkdir(parents=True)

        assert find_dovo_root(nested) == root.resolve()

    def test_dovo_marker_takes_precedence_over_git_marker(self, tmp_path: Path) -> None:
        """[tier-1/integration] find_dovo_root: when a nested ancestor has .dovo and a further ancestor has .git, the nearer .dovo ancestor wins."""
        outer = tmp_path / "outer"
        inner = outer / "inner"
        (outer / ".git").mkdir(parents=True)
        (inner / ".dovo").mkdir(parents=True)

        assert find_dovo_root(inner) == inner.resolve()

    def test_neither_marker_found_falls_back_to_resolved_start(self, tmp_path: Path) -> None:
        """[tier-1/integration] find_dovo_root: no .dovo or .git in any ancestor up to filesystem root -> returns the resolved start path unchanged."""
        isolated = tmp_path / "no-markers-here"
        isolated.mkdir()

        assert find_dovo_root(isolated) == isolated.resolve()

    def test_none_start_defaults_to_current_working_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] find_dovo_root: start=None resolves from the process CWD, not an arbitrary default."""
        root = tmp_path / "repo"
        (root / ".git").mkdir(parents=True)
        monkeypatch.chdir(root)

        assert find_dovo_root(None) == root.resolve()


class DerivedPathHelperTests:
    def test_get_dovo_dir_joins_dot_dovo_onto_cwd(self, tmp_path: Path) -> None:
        """[tier-1/unit] get_dovo_dir: returns cwd / '.dovo'."""
        assert get_dovo_dir(tmp_path) == tmp_path / ".dovo"

    def test_get_dovo_config_file_joins_config_json_under_dovo_dir(self, tmp_path: Path) -> None:
        """[tier-1/unit] get_dovo_config_file: returns cwd / '.dovo' / 'config.json'."""
        assert get_dovo_config_file(tmp_path) == tmp_path / ".dovo" / "config.json"

    def test_get_gitignore_file_joins_gitignore_onto_cwd(self, tmp_path: Path) -> None:
        """[tier-1/unit] get_gitignore_file: returns cwd / '.gitignore'."""
        assert get_gitignore_file(tmp_path) == tmp_path / ".gitignore"


class GetCatalogTemplatesDirTests:
    def test_returns_traversable_resource_containing_packaged_default_templates(self) -> None:
        """[tier-1/integration] get_catalog_templates_dir: returned Traversable exposes the packaged blueprints/default.yml and steps/default.yml resources."""
        root = get_catalog_templates_dir()

        assert (root / "blueprints" / "default.yml").is_file()
        assert (root / "steps" / "default.yml").is_file()
