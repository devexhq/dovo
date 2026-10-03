"""Contract tests for centralized database path resolution."""

from __future__ import annotations

from pathlib import Path

from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.db.connection import resolve_db_path


class ConnectionTests:
    """Contract tests for resolve_db_path's centralized global data directory resolution."""

    def test_resolve_db_path_targets_global_data_directory(self, tmp_path: Path) -> None:
        """[tier-1/unit] resolve_db_path: global_paths.data_dir/<default filename>."""
        global_paths = resolve_global_paths(tmp_path)

        result = resolve_db_path(global_paths)

        assert result == tmp_path.resolve() / "data" / "dovo.db"

    def test_resolve_db_path_accepts_custom_filename(self, tmp_path: Path) -> None:
        """[tier-1/unit] resolve_db_path: an explicit db_filename overrides the default filename."""
        global_paths = resolve_global_paths(tmp_path)

        result = resolve_db_path(global_paths, db_filename="custom.db")

        assert result == tmp_path.resolve() / "data" / "custom.db"


class ResolveDbPathNoMkdirTests:
    """[tier-1/unit] resolve_db_path(global_paths): NFR-5 no-eager-directory-creation contract."""

    def test_resolve_db_path_does_not_create_parent_directory(self, tmp_path: Path) -> None:
        """[tier-1/unit] resolve_db_path(global_paths): returned path's parent directory does not exist on disk after the call (NFR-5)."""
        global_paths = resolve_global_paths(tmp_path / "fresh")

        result = resolve_db_path(global_paths)

        assert not result.parent.exists()
