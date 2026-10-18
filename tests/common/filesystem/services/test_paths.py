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


def _write_marker(directory: Path, marker: str) -> None:
    if marker == "dovo-config":
        (directory / ".dovo").mkdir(parents=True, exist_ok=True)
        (directory / ".dovo" / "config.json").write_text("{}", encoding="utf-8")
    elif marker == "dovo-dir":
        (directory / ".dovo").mkdir(parents=True, exist_ok=True)
    else:
        (directory / ".git").mkdir(parents=True)


class FindDovoRootGlobalHomeTests:
    @pytest.mark.parametrize(
        "marker",
        [
            pytest.param("dovo-config", id="dovo-config-json"),
            pytest.param("dovo-dir", id="dovo-dir-only"),
            pytest.param("git", id="git"),
        ],
    )
    def test_excluded_directory_is_skipped_for_every_marker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, marker: str
    ) -> None:
        """Return the resolved start when the only marked ancestor is the directory holding DOVO_HOME, whatever its marker."""
        home = tmp_path / "home"
        start = home / "scratch" / "sub"
        start.mkdir(parents=True)
        _write_marker(home, marker)
        monkeypatch.setenv("DOVO_HOME", str(home / ".dovo"))

        assert find_dovo_root(start) == start.resolve()

    def test_walk_continues_to_ancestor_above_excluded_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return the .git ancestor above the excluded directory when discovery starts below it."""
        outer = tmp_path / "outer"
        home = outer / "home"
        start = home / "sub"
        start.mkdir(parents=True)
        (outer / ".git").mkdir()
        (home / ".dovo").mkdir()
        monkeypatch.setenv("DOVO_HOME", str(home / ".dovo"))

        assert find_dovo_root(start) == outer.resolve()

    def test_nested_repo_under_excluded_directory_still_resolves(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return a .git repository nested under the excluded directory when starting inside it."""
        home = tmp_path / "home"
        project = home / "proj"
        start = project / "a" / "b"
        start.mkdir(parents=True)
        (home / ".dovo").mkdir()
        (home / ".git").mkdir()
        (project / ".git").mkdir()
        monkeypatch.setenv("DOVO_HOME", str(home / ".dovo"))

        assert find_dovo_root(start) == project.resolve()

    def test_start_at_excluded_directory_falls_back_to_start(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return the excluded directory itself only as the start fallback when no other ancestor has a marker."""
        home = tmp_path / "home"
        (home / ".dovo").mkdir(parents=True)
        (home / ".git").mkdir()
        monkeypatch.setenv("DOVO_HOME", str(home / ".dovo"))

        assert find_dovo_root(home) == home.resolve()

    @pytest.mark.parametrize(
        "spelling",
        [
            pytest.param("relative", id="relative-dovo-home"),
            pytest.param("tilde", id="tilde-dovo-home"),
            pytest.param("symlinked-dot-dovo", id="symlinked-dot-dovo"),
        ],
    )
    def test_global_home_spelling_is_resolved_before_exclusion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spelling: str
    ) -> None:
        """Skip the home directory when DOVO_HOME is relative, uses ~, or <home>/.dovo symlinks to it."""
        home = tmp_path / "home"
        start = home / "sub"
        start.mkdir(parents=True)
        (home / ".git").mkdir()
        if spelling == "relative":
            (home / ".dovo").mkdir()
            monkeypatch.chdir(home)
            monkeypatch.setenv("DOVO_HOME", ".dovo")
        elif spelling == "tilde":
            (home / ".dovo").mkdir()
            monkeypatch.setenv("HOME", str(home))
            monkeypatch.setenv("DOVO_HOME", "~/.dovo")
        else:
            data_home = tmp_path / "data" / "dovo"
            data_home.mkdir(parents=True)
            (home / ".dovo").symlink_to(data_home, target_is_directory=True)
            monkeypatch.setenv("DOVO_HOME", str(data_home))

        assert find_dovo_root(start) == start.resolve()

    def test_missing_global_home_is_excluded_and_not_created(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Skip the parent of a nonexistent DOVO_HOME and leave that DOVO_HOME uncreated."""
        home = tmp_path / "home"
        start = home / "sub"
        start.mkdir(parents=True)
        (home / ".git").mkdir()
        missing_home = home / "missing-dovo-home"
        monkeypatch.setenv("DOVO_HOME", str(missing_home))

        assert find_dovo_root(start) == start.resolve()
        assert not missing_home.exists()


class GetCatalogTemplatesDirTests:
    def test_returns_traversable_resource_containing_packaged_default_templates(self) -> None:
        root = get_catalog_templates_dir()

        assert (root / "blueprints" / "default.yml").is_file()
        assert (root / "steps" / "default.yml").is_file()
