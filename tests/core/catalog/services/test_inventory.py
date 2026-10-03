"""Contract tests for the disk-backed multi-tier catalog index services."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.common.filesystem import WorkspacePaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.catalog.models import CatalogIndexEntry, CatalogItemType, CatalogTier
from dovo.core.catalog.services.inventory import (
    compute_catalog_sha,
    create_catalog_item,
    load_catalog_index,
    resolve_catalog_records,
    scan_and_index_catalog,
    scan_and_index_tier,
    tier_root,
)

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]


def _write_blueprint(tier_dir: Path, stem: str, content: str) -> None:
    """Write one blueprint YAML file under a tier's blueprints/ subdirectory."""
    blueprints_dir = tier_dir / "blueprints"
    blueprints_dir.mkdir(parents=True, exist_ok=True)
    (blueprints_dir / f"{stem}.yml").write_text(content, encoding="utf-8")


class ScanAndIndexTierTests:
    """[tier-1/unit] Layer discovery contracts for scan_and_index_tier."""

    def test_scan_and_index_tier_repo_rewrites_index_json_from_disk_walk(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        paths = workspace_paths_factory(tmp_path / "repo", None)
        content = 'version: "1.0"\nname: sample\ndescription: Sample blueprint\nsteps: []\n'
        tier_dir = tier_root(CatalogTier.REPO, paths)
        _write_blueprint(tier_dir, "sample", content)

        result = scan_and_index_tier(CatalogTier.REPO, paths)

        expected_sha, expected_checksum = compute_catalog_sha(CatalogItemType.BLUEPRINT, content)
        index = load_catalog_index(tier_dir)
        assert index.items == [
            CatalogIndexEntry(
                sha=expected_sha,
                key="sample",
                item_type=CatalogItemType.BLUEPRINT,
                name="sample",
                namespace=None,
                path=Path("blueprints/sample.yml"),
                checksum=expected_checksum,
            )
        ]
        assert result.errors == []


class CatalogReindexSingleResolutionTests:
    """[tier-2/unit] Reindexing consumes its supplied workspace snapshot."""

    def test_scan_and_index_catalog_resolves_global_paths_once(
        self, workspace_paths: WorkspacePaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The three-tier scan does not perform a fresh global-root resolution."""

        def _unexpected_resolution(*args: object, **kwargs: object) -> object:
            raise AssertionError("catalog reindex must use the supplied WorkspacePaths")

        monkeypatch.setattr("dovo.common.filesystem.services.global_root.resolve_global_paths", _unexpected_resolution)

        result = scan_and_index_catalog(workspace_paths)

        assert result.errors == []

    def test_scan_and_index_tier_removes_stale_entry_when_file_deleted_since_last_sync(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        paths = workspace_paths_factory(tmp_path / "repo", None)
        tier_dir = tier_root(CatalogTier.REPO, paths)
        _write_blueprint(tier_dir, "sample", 'version: "1.0"\nname: sample\ndescription: Sample\nsteps: []\n')
        scan_and_index_tier(CatalogTier.REPO, paths)

        (tier_dir / "blueprints" / "sample.yml").unlink()
        scan_and_index_tier(CatalogTier.REPO, paths)

        index = load_catalog_index(tier_dir)
        assert index.items == []


class ResolveCatalogRecordsTests:
    """[tier-1/unit] Multi-tier precedence contracts for resolve_catalog_records."""

    def test_resolve_catalog_records_orders_repo_before_user_before_global(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        paths = workspace_paths_factory(tmp_path / "repo", tmp_path / "global_home")
        content = 'version: "1.0"\nname: shared\ndescription: Shared\nsteps: []\n'

        for tier in (CatalogTier.REPO, CatalogTier.USER, CatalogTier.GLOBAL):
            tier_dir = tier_root(tier, paths)
            _write_blueprint(tier_dir, "shared", content)
            scan_and_index_tier(tier, paths)

        records = resolve_catalog_records(paths)
        shared_tiers = [record.tier for record in records if record.key == "shared"]

        assert shared_tiers == [CatalogTier.REPO, CatalogTier.USER, CatalogTier.GLOBAL]


class CreateCatalogItemTests:
    """[tier-1/unit] Multi-tier write, reindex, and lock-root contracts for create_catalog_item."""

    @pytest.mark.parametrize(
        ("tier", "expected_tier_dir_attr"),
        [
            pytest.param(CatalogTier.REPO, None, id="repo"),
            pytest.param(CatalogTier.USER, "user_catalog_dir", id="user"),
            pytest.param(CatalogTier.GLOBAL, "global_catalog_dir", id="global"),
        ],
    )
    def test_create_catalog_item_writes_yaml_under_selected_tier(
        self,
        tmp_path: Path,
        tier: CatalogTier,
        expected_tier_dir_attr: str | None,
        workspace_paths_factory: WorkspacePathsFactory,
    ) -> None:
        """create_catalog_item: writes blueprints/<name>.yml under the selected tier's root and returns a CatalogRecord tagged with that tier."""
        repo_root = tmp_path / "repo"
        global_root = tmp_path / "global_home"
        paths = workspace_paths_factory(repo_root, global_root)

        record = create_catalog_item(CatalogItemType.BLUEPRINT, "my-blueprint", tier, paths)

        expected_tier_dir = (
            getattr(resolve_global_paths(global_root), expected_tier_dir_attr)
            if expected_tier_dir_attr is not None
            else tier_root(CatalogTier.REPO, paths)
        )
        assert record.tier == tier
        assert (expected_tier_dir / "blueprints" / "my-blueprint.yml").is_file()

    def test_create_catalog_item_user_tier_leaves_repo_and_global_index_untouched(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """create_catalog_item(tier=USER): only <global_root>/user/index.json is written; repo and global index.json files do not exist afterward."""
        repo_root = tmp_path / "repo"
        global_root = tmp_path / "global_home"
        paths = workspace_paths_factory(repo_root, global_root)

        create_catalog_item(CatalogItemType.BLUEPRINT, "my-blueprint", CatalogTier.USER, paths)

        global_paths = resolve_global_paths(global_root)
        assert not (tier_root(CatalogTier.REPO, paths) / "index.json").exists()
        assert not (global_paths.global_catalog_dir / "index.json").exists()
        assert (global_paths.user_catalog_dir / "index.json").exists()

    def test_create_catalog_item_user_tier_acquires_lock_under_global_data_dir_not_repo_root(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """create_catalog_item(tier=USER): <global_root>/data/.dovo/.lock exists afterward; <repo_root>/.dovo/.lock does not."""
        repo_root = tmp_path / "repo"
        global_root = tmp_path / "global_home"
        paths = workspace_paths_factory(repo_root, global_root)

        create_catalog_item(CatalogItemType.BLUEPRINT, "my-blueprint", CatalogTier.USER, paths)

        assert (global_root / "data" / ".dovo" / ".lock").exists()
        assert not (repo_root / ".dovo" / ".lock").exists()

    def test_create_catalog_item_collision_at_selected_tier_raises_file_exists_error(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """create_catalog_item(tier=USER): a pre-existing file at the USER tier's target path raises FileExistsError naming the USER-relative path, independent of any REPO-tier file at the same relative path."""
        paths = workspace_paths_factory(tmp_path / "repo", tmp_path / "global_home")
        create_catalog_item(CatalogItemType.BLUEPRINT, "dup", CatalogTier.USER, paths)

        with pytest.raises(FileExistsError, match=r"blueprints/dup\.yml"):
            create_catalog_item(CatalogItemType.BLUEPRINT, "dup", CatalogTier.USER, paths)

    def test_create_catalog_item_auto_provisions_missing_user_tier_directories(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """create_catalog_item(tier=USER): a global_root with no existing user/catalog/{blueprints,steps} directories succeeds, creating both before the write."""
        global_root = tmp_path / "global_home"
        paths = workspace_paths_factory(tmp_path / "repo", global_root)

        create_catalog_item(CatalogItemType.BLUEPRINT, "first", CatalogTier.USER, paths)

        user_catalog_dir = resolve_global_paths(global_root).user_catalog_dir
        assert (user_catalog_dir / "blueprints").is_dir()
        assert (user_catalog_dir / "steps").is_dir()
