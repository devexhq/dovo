"""Integration tests for catalog scaffolding, protection, discovery fallback, and collisions."""

from __future__ import annotations

from pathlib import Path

import pytest

from worktree.common.filesystem import Filesystem, WorkspacePaths
from worktree.common.filesystem.services.global_root import resolve_global_paths
from worktree.common.models import DefinitionResolutionStatus
from worktree.core.catalog import Catalog
from worktree.core.catalog.models import CatalogItemType, CatalogTier
from worktree.core.catalog.services.inventory import compute_catalog_sha


class CatalogTemplateScaffoldingTests:
    """Tests verifying scaffolding of blueprints and steps under correct type subdirectories."""

    @pytest.mark.parametrize(
        ("item_type", "name", "expected_rel_path"),
        [
            pytest.param(
                CatalogItemType.BLUEPRINT, "scaffold-task", Path("blueprints/scaffold-task.yml"), id="blueprint"
            ),
            pytest.param(CatalogItemType.STEP, "scaffold-step", Path("steps/scaffold-step.yml"), id="step"),
        ],
    )
    def test_catalog_create_scaffolds_yaml_template_under_correct_type_dir(
        self,
        workspace_paths: WorkspacePaths,
        item_type: CatalogItemType,
        name: str,
        expected_rel_path: Path,
    ) -> None:
        """Create scaffolds YAML templates under .worktree/catalog/blueprints and steps."""
        catalog = Catalog(workspace_paths)
        result = catalog.create(item_type, name)

        target_file = workspace_paths.catalog_dir / expected_rel_path
        assert target_file.is_file()

        content = target_file.read_text(encoding="utf-8")
        expected_sha, expected_checksum = compute_catalog_sha(item_type, content)

        assert result.item is not None
        assert result.item.key == name
        assert result.item.sha == expected_sha
        assert result.item.item_type == item_type
        assert result.item.name == name
        assert result.item.namespace is None
        assert result.item.path == expected_rel_path
        assert result.item.checksum == expected_checksum
        assert result.item.tier == CatalogTier.REPO
        assert result.errors == []
        assert result.warnings == []
        assert result.fixes == []


class CatalogProtectionTests:
    """Tests verifying protection of bundled templates in the wt/ namespace."""

    @pytest.mark.parametrize(
        "template_name",
        [
            pytest.param("wt/starter-task", id="starter-task"),
            pytest.param("wt/fix-tests", id="fix-tests"),
        ],
    )
    def test_catalog_delete_rejects_deletion_of_bundled_packaged_templates(
        self, workspace_paths: WorkspacePaths, template_name: str
    ) -> None:
        """catalog.delete rejects deleting templates in the wt/ namespace with ok=False."""
        catalog = Catalog(workspace_paths)
        result = catalog.delete(template_name)

        assert result.item is None
        assert result.deleted is False
        assert result.cancelled is False
        assert result.errors == [f"Cannot delete bundled catalog template '{template_name}'."]
        assert result.warnings == []
        assert result.fixes == []


class CatalogDiscoveryFallbackTests:
    """Tests verifying fallback to bundled templates in package resources."""

    @pytest.mark.parametrize(
        "template_name",
        [
            pytest.param("fix-tests", id="bare-name"),
            pytest.param("wt/fix-tests", id="namespaced-name"),
        ],
    )
    def test_catalog_show_falls_back_to_bundled_template(
        self, workspace_paths: WorkspacePaths, template_name: str
    ) -> None:
        """catalog.show falls back to bundled templates via importlib.resources."""
        catalog = Catalog(workspace_paths)
        result = catalog.show(template_name)

        expected_file = Filesystem().catalog_templates_dir / "blueprints" / "wt" / "fix-tests.yml"
        expected_content = expected_file.read_text(encoding="utf-8")

        assert result.item is not None
        assert result.item.tier == CatalogTier.PACKAGED
        assert result.content == expected_content
        assert result.template_matches == [("blueprints/wt/fix-tests.yml", expected_content)]
        assert result.errors == []
        assert result.warnings == []
        assert result.fixes == []


class CatalogCollisionTests:
    """Tests verifying collision detection during template creation."""

    @pytest.mark.parametrize(
        ("item_type", "name", "expected_rel_path"),
        [
            pytest.param(
                CatalogItemType.BLUEPRINT,
                "collision-task",
                Path("blueprints/collision-task.yml"),
                id="blueprint",
            ),
            pytest.param(
                CatalogItemType.STEP,
                "collision-step",
                Path("steps/collision-step.yml"),
                id="step",
            ),
        ],
    )
    def test_catalog_create_collision_returns_error(
        self,
        workspace_paths: WorkspacePaths,
        item_type: CatalogItemType,
        name: str,
        expected_rel_path: Path,
    ) -> None:
        """catalog.create returns an error when target template already exists."""
        catalog = Catalog(workspace_paths)
        result_first = catalog.create(item_type, name)
        assert result_first.item is not None

        target_file = workspace_paths.catalog_dir / expected_rel_path
        assert target_file.is_file()

        result_second = catalog.create(item_type, name)
        assert result_second.item is None
        assert result_second.errors == [f"Catalog blueprint collision at path '{expected_rel_path.as_posix()}'"]
        assert result_second.warnings == []
        assert result_second.fixes == []


class CatalogNamespaceSplittingTests:
    """Tests verifying namespaced identifiers split on the literal '/' boundary, not by character set."""

    def test_name_sharing_trailing_characters_with_namespace_resolves(self, workspace_paths: WorkspacePaths) -> None:
        """catalog.get resolves 'wt/run-test', whose name ends in characters ('t') also present in its 'wt' namespace."""
        catalog = Catalog(workspace_paths)
        catalog.save(
            "wt/run-test",
            {"name": "run-test", "description": "Regression fixture", "action": "run"},
            item_type=CatalogItemType.STEP,
        )

        result = catalog.get("wt/run-test", item_type=CatalogItemType.STEP)

        assert result.status == DefinitionResolutionStatus.OK
        assert result.resolved is not None
        assert result.resolved.name == "run-test"
        assert result.resolved.namespace == "wt"


class CatalogGetTests:
    """Tests verifying multi-tier resolution precedence and duplicate-tier warnings for Catalog.get."""

    def test_get_returns_repo_tier_match_over_user_tier_duplicate(self, workspace_paths: WorkspacePaths) -> None:
        """Catalog.get: same key indexed at REPO and USER tier resolves to the REPO-tagged CatalogRecord with no warning."""
        catalog = Catalog(workspace_paths)
        catalog.create(CatalogItemType.BLUEPRINT, "shared-name")

        user_catalog_dir = resolve_global_paths(None).user_catalog_dir
        (user_catalog_dir / "blueprints").mkdir(parents=True, exist_ok=True)
        (user_catalog_dir / "blueprints" / "shared-name.yml").write_text(
            'version: "1.0"\nname: shared-name\ndescription: User tier duplicate\nsteps: []\n', encoding="utf-8"
        )

        result = catalog.get("shared-name", item_type=CatalogItemType.BLUEPRINT)

        assert result.status == DefinitionResolutionStatus.OK
        assert result.resolved is not None
        assert result.resolved.tier == CatalogTier.REPO
        assert result.warnings == []

    def test_get_returns_user_tier_match_when_no_repo_tier_entry_exists(self, workspace_paths: WorkspacePaths) -> None:
        """Catalog.get: key present only at USER tier resolves to that CatalogRecord tagged tier=user."""
        user_catalog_dir = resolve_global_paths(None).user_catalog_dir
        (user_catalog_dir / "steps").mkdir(parents=True, exist_ok=True)
        (user_catalog_dir / "steps" / "user-only.yml").write_text(
            "name: user-only\ndescription: User tier step\naction: run\n", encoding="utf-8"
        )

        catalog = Catalog(workspace_paths)
        result = catalog.get("user-only", item_type=CatalogItemType.STEP)

        assert result.status == DefinitionResolutionStatus.OK
        assert result.resolved is not None
        assert result.resolved.tier == CatalogTier.USER


class CatalogDeleteTierGuardTests:
    """Tests verifying delete refuses to remove a match resolved from a non-REPO tier."""

    def test_delete_user_tier_match_returns_not_deletable_error(self, workspace_paths: WorkspacePaths) -> None:
        """Catalog.delete: a USER-tier-only match returns deleted=False with a tier-specific error, and the file remains on disk."""
        user_catalog_dir = resolve_global_paths(None).user_catalog_dir
        (user_catalog_dir / "blueprints").mkdir(parents=True, exist_ok=True)
        user_file = user_catalog_dir / "blueprints" / "user-only.yml"
        user_file.write_text(
            'version: "1.0"\nname: user-only\ndescription: User tier blueprint\nsteps: []\n', encoding="utf-8"
        )

        catalog = Catalog(workspace_paths)
        result = catalog.delete("user-only")

        assert result.deleted is False
        assert result.errors == [
            "Catalog item 'user-only' resolved from tier 'user'; only repo-tier items can be deleted."
        ]
        assert user_file.is_file()
