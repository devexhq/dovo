"""Contract tests for the disk-backed multi-tier catalog index services."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import Filesystem, WorkspacePaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.catalog.exceptions import CatalogError
from dovo.core.catalog.models import (
    CatalogIndex,
    CatalogIndexEntry,
    CatalogItemType,
    CatalogRecord,
    CatalogScan,
    CatalogTier,
)
from dovo.core.catalog.services.inventory import (
    compute_catalog_sha,
    create_catalog_item,
    load_catalog_index,
    resolve_catalog_records,
    scan_and_index_catalog,
    scan_and_index_tier,
    tier_root,
)
from tests.harness.diagnostic_log import read_single_event

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]


def _write_blueprint(tier_dir: Path, stem: str, content: str) -> None:
    """Write one blueprint YAML file under a tier's blueprints/ subdirectory."""
    blueprints_dir = tier_dir / "blueprints"
    blueprints_dir.mkdir(parents=True, exist_ok=True)
    (blueprints_dir / f"{stem}.yml").write_text(content, encoding="utf-8")


class ScanAndIndexTierTests:
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
        assert result == CatalogScan(
            records=(CatalogRecord(**index.items[0].model_dump(), tier=CatalogTier.REPO),), skipped_files=()
        )

    @pytest.mark.parametrize("ancestor", ["steps", "blueprints"])
    def test_scan_and_index_tier_keys_ignore_ancestor_named_like_item_type_directory(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory, ancestor: str
    ) -> None:
        paths = workspace_paths_factory(tmp_path / ancestor / "repo", None)
        tier_dir = tier_root(CatalogTier.REPO, paths)
        _write_blueprint(tier_dir, "sample", 'version: "1.0"\nname: sample\ndescription: Sample\nsteps: []\n')
        step_dir = tier_dir / "steps" / "team"
        step_dir.mkdir(parents=True)
        (step_dir / "lint.yml").write_text("name: lint\nrun: echo lint\n", encoding="utf-8")

        result = scan_and_index_tier(CatalogTier.REPO, paths)

        index = load_catalog_index(tier_dir)
        assert [(entry.key, entry.namespace) for entry in index.items] == [("sample", None), ("team/lint", "team")]
        assert result.skipped_files == ()

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permission bits")
    def test_unreadable_file_is_skipped_and_scan_continues(self, workspace_paths: WorkspacePaths) -> None:
        """Return CatalogScan whose records hold the readable file and skipped_files holds the unreadable file's read error."""
        tier_dir = tier_root(CatalogTier.REPO, workspace_paths)
        _write_blueprint(tier_dir, "good", 'version: "1.0"\nname: good\ndescription: Good\nsteps: []\n')
        _write_blueprint(tier_dir, "bad", 'version: "1.0"\nname: bad\ndescription: Bad\nsteps: []\n')
        bad_file = tier_dir / "blueprints" / "bad.yml"
        bad_file.chmod(0o000)
        try:
            result = scan_and_index_tier(CatalogTier.REPO, workspace_paths)
        finally:
            bad_file.chmod(0o644)

        assert [record.key for record in result.records] == ["good"]
        assert len(result.skipped_files) == 1
        assert result.skipped_files[0].startswith(f"Failed to read catalog blueprint '{bad_file}': ")

    def test_index_write_failure_raises_catalog_write_failed(
        self, workspace_paths: WorkspacePaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Raise CatalogError CATALOG_WRITE_FAILED naming the index path, caused by the OSError atomic_write_json raised."""
        disk_full = OSError("disk full")

        def _fail_write(path: Path, data: object) -> None:
            raise disk_full

        monkeypatch.setattr(Filesystem, "atomic_write_json", _fail_write)
        index_path = tier_root(CatalogTier.REPO, workspace_paths) / "index.json"

        with pytest.raises(CatalogError) as raised:
            scan_and_index_tier(CatalogTier.REPO, workspace_paths)

        assert raised.value.error_code == ErrorCode.CATALOG_WRITE_FAILED
        assert raised.value.message == f"Failed to write catalog index '{index_path}': disk full"
        assert raised.value.__cause__ is disk_full


class LoadCatalogIndexTests:
    @pytest.mark.parametrize("content", ["{not json", '{"items": 3}'])
    def test_invalid_index_returns_empty_and_records_debug(
        self, tmp_path: Path, frozen_diagnostic_clock: Path, content: str
    ) -> None:
        """Return CatalogIndex() and write one debug event with operation catalog.read_index and context {path}."""
        index_path = tmp_path / "index.json"
        index_path.write_text(content, encoding="utf-8")

        index = load_catalog_index(tmp_path)

        assert index == CatalogIndex()
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "debug"
        assert event["operation"] == "catalog.read_index"
        assert event["context"] == {"path": str(index_path)}

    def test_missing_index_returns_empty_without_record(self, tmp_path: Path) -> None:
        """Return CatalogIndex() for an absent index.json and leave <DOVO_HOME>/logs absent."""
        index = load_catalog_index(tmp_path)

        assert index == CatalogIndex()
        assert not resolve_global_paths(None).logs_dir.exists()


class CatalogReindexSingleResolutionTests:
    """Reindexing consumes the workspace snapshot it is given."""

    def test_scan_and_index_catalog_resolves_global_paths_once(
        self, workspace_paths: WorkspacePaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Scan three tiers without a fresh global-root resolution."""

        def _unexpected_resolution(*args: object, **kwargs: object) -> object:
            raise AssertionError("catalog reindex must use the supplied WorkspacePaths")

        monkeypatch.setattr("dovo.common.filesystem.services.global_root.resolve_global_paths", _unexpected_resolution)

        result = scan_and_index_catalog(workspace_paths)

        assert result.skipped_files == ()

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
        repo_root = tmp_path / "repo"
        global_root = tmp_path / "global_home"
        paths = workspace_paths_factory(repo_root, global_root)

        create_catalog_item(CatalogItemType.BLUEPRINT, "my-blueprint", CatalogTier.USER, paths)

        assert (global_root / "data" / ".dovo" / ".lock").exists()
        assert not (repo_root / ".dovo" / ".lock").exists()

    def test_create_catalog_item_collision_at_selected_tier_raises_already_exists(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """Raise CatalogError CATALOG_ALREADY_EXISTS naming the USER-relative path, independent of a REPO-tier file at the same path."""
        paths = workspace_paths_factory(tmp_path / "repo", tmp_path / "global_home")
        create_catalog_item(CatalogItemType.BLUEPRINT, "dup", CatalogTier.USER, paths)

        with pytest.raises(CatalogError) as raised:
            create_catalog_item(CatalogItemType.BLUEPRINT, "dup", CatalogTier.USER, paths)

        assert raised.value.error_code == ErrorCode.CATALOG_ALREADY_EXISTS
        assert raised.value.message == "Catalog blueprint collision at path 'blueprints/dup.yml'"

    def test_create_catalog_item_auto_provisions_missing_user_tier_directories(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        global_root = tmp_path / "global_home"
        paths = workspace_paths_factory(tmp_path / "repo", global_root)

        create_catalog_item(CatalogItemType.BLUEPRINT, "first", CatalogTier.USER, paths)

        user_catalog_dir = resolve_global_paths(global_root).user_catalog_dir
        assert (user_catalog_dir / "blueprints").is_dir()
        assert (user_catalog_dir / "steps").is_dir()
