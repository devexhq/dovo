"""Contract tests for global Dovo root provisioning."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dovo.common.filesystem import InvalidGlobalRootError
from dovo.common.filesystem.services.global_root import ensure_global_layout, resolve_global_paths

REQUIRED_LAYOUT_RELATIVE_PATHS = (
    Path("."),
    Path("global"),
    Path("global/catalog"),
    Path("global/catalog/blueprints"),
    Path("global/catalog/steps"),
    Path("user"),
    Path("user/catalog"),
    Path("user/catalog/blueprints"),
    Path("user/catalog/steps"),
    Path("data"),
    Path("storage"),
    Path("storage/projects"),
)


class GlobalRootTests:
    """Contract tests for global Dovo root resolution and layout creation."""

    def test_resolve_global_paths_override_precedes_dovo_home(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        override_root = tmp_path / "override"
        monkeypatch.setenv("DOVO_HOME", str(tmp_path / "environment"))

        paths = resolve_global_paths(override_root)

        expected_root = override_root.resolve()
        assert paths.root == expected_root
        assert paths.global_dir == expected_root / "global"
        assert paths.user_dir == expected_root / "user"
        assert paths.user_catalog_dir == expected_root / "user" / "catalog"
        assert paths.data_dir == expected_root / "data"
        assert paths.storage_dir == expected_root / "storage"

    def test_resolve_global_paths_uses_dovo_home_without_override(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        dovo_home = tmp_path / "environment"
        monkeypatch.setenv("DOVO_HOME", str(dovo_home))

        paths = resolve_global_paths()

        expected_root = dovo_home.resolve()
        assert paths.root == expected_root
        assert paths.global_dir == expected_root / "global"
        assert paths.user_dir == expected_root / "user"
        assert paths.user_catalog_dir == expected_root / "user" / "catalog"
        assert paths.data_dir == expected_root / "data"
        assert paths.storage_dir == expected_root / "storage"

    def test_resolve_global_paths_uses_home_dot_dovo_without_override_or_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DOVO_HOME", raising=False)

        paths = resolve_global_paths()

        expected_root = (Path.home() / ".dovo").resolve()
        assert paths.root == expected_root
        assert paths.global_dir == expected_root / "global"
        assert paths.user_dir == expected_root / "user"
        assert paths.user_catalog_dir == expected_root / "user" / "catalog"
        assert paths.data_dir == expected_root / "data"
        assert paths.storage_dir == expected_root / "storage"

    def test_ensure_global_layout_creates_required_directories_with_umask_limited_mode(self, tmp_path: Path) -> None:
        original_umask = os.umask(0o027)
        try:
            paths = ensure_global_layout(tmp_path / "global-root")
        finally:
            os.umask(original_umask)

        modes = {
            relative_path: (paths.root / relative_path).stat().st_mode & 0o777
            for relative_path in REQUIRED_LAYOUT_RELATIVE_PATHS
        }

        assert modes == dict.fromkeys(REQUIRED_LAYOUT_RELATIVE_PATHS, 0o750)

    def test_ensure_global_layout_repeated_call_returns_same_paths_and_preserves_layout(self, tmp_path: Path) -> None:
        root = tmp_path / "global-root"

        first_paths = ensure_global_layout(root)
        second_paths = ensure_global_layout(root)

        assert first_paths == second_paths
        assert all((second_paths.root / relative_path).is_dir() for relative_path in REQUIRED_LAYOUT_RELATIVE_PATHS)

    def test_ensure_global_layout_root_file_collision_raises_file_exists_error(self, tmp_path: Path) -> None:
        root = tmp_path / "global-root"
        root.write_text("occupied", encoding="utf-8")

        with pytest.raises(FileExistsError):
            ensure_global_layout(root)

        assert root.is_file()

    def test_ensure_global_layout_unwritable_parent_raises_diagnostic_permission_error(self, tmp_path: Path) -> None:
        parent = tmp_path / "unwritable"
        parent.mkdir()
        parent.chmod(0o555)
        root = parent / "global-root"
        try:
            with pytest.raises(PermissionError) as exc_info:
                ensure_global_layout(root)
        finally:
            parent.chmod(0o755)

        assert str(root) in str(exc_info.value)
        assert "Check directory permissions." in str(exc_info.value)

    def test_ensure_global_layout_git_directory_raises_invalid_global_root_error(self, tmp_path: Path) -> None:
        root = tmp_path / "global-root"
        (root / ".git").mkdir(parents=True)

        with pytest.raises(InvalidGlobalRootError) as exc_info:
            ensure_global_layout(root)

        assert str(exc_info.value) == (
            f"Global Dovo root '{root.resolve()}' must not be a Git repository; "
            "remove its .git directory or choose a different DOVO_HOME."
        )
        assert not (root / "user" / "catalog" / "blueprints").exists()

    def test_ensure_global_layout_ignores_git_directory_when_config_option_is_true(self, tmp_path: Path) -> None:
        root = tmp_path / "global-root"
        (root / ".git").mkdir(parents=True)

        paths = ensure_global_layout(root, ignore_global_root_error=True)

        assert all((paths.root / relative_path).is_dir() for relative_path in REQUIRED_LAYOUT_RELATIVE_PATHS)

    def test_ensure_global_layout_git_file_does_not_trigger_directory_guard(self, tmp_path: Path) -> None:
        root = tmp_path / "global-root"
        root.mkdir()
        (root / ".git").write_text("gitdir: elsewhere", encoding="utf-8")

        paths = ensure_global_layout(root)

        assert all((paths.root / relative_path).is_dir() for relative_path in REQUIRED_LAYOUT_RELATIVE_PATHS)
