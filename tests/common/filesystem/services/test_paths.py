"""Contract tests for repository-root discovery and derived path helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.common.filesystem.services.paths import find_dovo_root, get_catalog_templates_dir


class FindDovoRootTests:
    def test_dovo_config_json_present_returns_that_directory(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        (root / ".dovo").mkdir(parents=True)
        (root / ".dovo" / "config.json").write_text("{}", encoding="utf-8")

        assert find_dovo_root(root) == root.resolve()

    def test_dovo_dir_without_config_json_still_returns_that_directory(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        (root / ".dovo").mkdir(parents=True)

        assert find_dovo_root(root) == root.resolve()

    def test_git_directory_present_without_dovo_returns_that_directory(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        (root / ".git").mkdir(parents=True)

        assert find_dovo_root(root) == root.resolve()

    def test_searches_upward_from_nested_start_directory(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        nested = root / "a" / "b" / "c"
        (root / ".git").mkdir(parents=True)
        nested.mkdir(parents=True)

        assert find_dovo_root(nested) == root.resolve()

    def test_dovo_marker_takes_precedence_over_git_marker(self, tmp_path: Path) -> None:
        outer = tmp_path / "outer"
        inner = outer / "inner"
        (outer / ".git").mkdir(parents=True)
        (inner / ".dovo").mkdir(parents=True)

        assert find_dovo_root(inner) == inner.resolve()

    def test_neither_marker_found_falls_back_to_resolved_start(self, tmp_path: Path) -> None:
        isolated = tmp_path / "no-markers-here"
        isolated.mkdir()

        assert find_dovo_root(isolated) == isolated.resolve()

    def test_none_start_defaults_to_current_working_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "repo"
        (root / ".git").mkdir(parents=True)
        monkeypatch.chdir(root)

        assert find_dovo_root(None) == root.resolve()


class GetCatalogTemplatesDirTests:
    def test_returns_traversable_resource_containing_packaged_default_templates(self) -> None:
        root = get_catalog_templates_dir()

        assert (root / "blueprints" / "default.yml").is_file()
        assert (root / "steps" / "default.yml").is_file()
