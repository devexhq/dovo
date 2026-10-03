"""Contract tests for seeding packaged catalog dovo/ templates into a repository."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dovo.common.filesystem import WorkspacePaths
from dovo.core.catalog.models import CatalogItemType
from dovo.core.catalog.services.seeder import seed_all_catalog_templates, seed_catalog_templates

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]


class SeedCatalogTemplatesTests:
    def test_fresh_repository_creates_every_packaged_wt_step_file(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/integration] seed_catalog_templates: no existing files -> every packaged steps/dovo/*.yml is copied and listed in created_files, none skipped or overwritten."""
        result = seed_catalog_templates(CatalogItemType.STEP, workspace_paths_factory(tmp_path, None))

        assert result.ok
        assert result.skipped_existing_files == []
        assert result.overwritten_files == []
        created_names = {p.name for p in result.created_files}
        assert "run-tests.yml" in created_names
        for created in result.created_files:
            assert created.is_file()

    def test_existing_file_without_force_is_skipped_not_overwritten(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/integration] seed_catalog_templates: force=False and a target file already exists -> file is recorded in skipped_existing_files, its on-disk content is left untouched."""
        target = tmp_path / ".dovo" / "catalog" / "steps" / "dovo" / "run-tests.yml"
        target.parent.mkdir(parents=True)
        target.write_text("custom: content\n", encoding="utf-8")

        result = seed_catalog_templates(CatalogItemType.STEP, workspace_paths_factory(tmp_path, None), force=False)

        assert any(p.name == "run-tests.yml" for p in result.skipped_existing_files)
        assert result.created_files == [] or all(p.name != "run-tests.yml" for p in result.created_files)
        assert target.read_text(encoding="utf-8") == "custom: content\n"

    def test_existing_file_with_force_is_overwritten(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/integration] seed_catalog_templates: force=True and a target file already exists -> file is recorded in overwritten_files and its content is replaced with the packaged template."""
        target = tmp_path / ".dovo" / "catalog" / "steps" / "dovo" / "run-tests.yml"
        target.parent.mkdir(parents=True)
        target.write_text("custom: content\n", encoding="utf-8")

        result = seed_catalog_templates(CatalogItemType.STEP, workspace_paths_factory(tmp_path, None), force=True)

        assert any(p.name == "run-tests.yml" for p in result.overwritten_files)
        assert target.read_text(encoding="utf-8") != "custom: content\n"

    def test_target_path_existing_as_directory_records_error_and_skips_file(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/integration] seed_catalog_templates: a target path that already exists as a directory records an error for that file and is not treated as created/skipped/overwritten."""
        target = tmp_path / ".dovo" / "catalog" / "steps" / "dovo" / "run-tests.yml"
        target.mkdir(parents=True)

        result = seed_catalog_templates(CatalogItemType.STEP, workspace_paths_factory(tmp_path, None), force=True)

        assert any("run-tests.yml" in err for err in result.errors)
        assert not result.ok
        assert all(p.name != "run-tests.yml" for p in result.created_files)
        assert all(p.name != "run-tests.yml" for p in result.overwritten_files)


class SeedAllCatalogTemplatesTests:
    def test_aggregates_created_files_across_blueprint_and_step_types(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/integration] seed_all_catalog_templates: aggregate result's created_files includes packaged dovo/ templates from both BLUEPRINT and STEP item types."""
        result = seed_all_catalog_templates(workspace_paths_factory(tmp_path, None))

        assert result.ok
        blueprint_dir = tmp_path / ".dovo" / "catalog" / "blueprints" / "dovo"
        step_dir = tmp_path / ".dovo" / "catalog" / "steps" / "dovo"
        assert blueprint_dir.is_dir()
        assert step_dir.is_dir()
        assert any(p.is_relative_to(blueprint_dir) for p in result.created_files)
        assert any(p.is_relative_to(step_dir) for p in result.created_files)

    def test_second_call_without_force_skips_everything_already_seeded(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/integration] seed_all_catalog_templates: calling twice without force -> the second call's created_files is empty and skipped_existing_files covers every previously created file."""
        paths = workspace_paths_factory(tmp_path, None)
        first = seed_all_catalog_templates(paths)
        second = seed_all_catalog_templates(paths, force=False)

        assert second.created_files == []
        assert len(second.skipped_existing_files) == len(first.created_files)
