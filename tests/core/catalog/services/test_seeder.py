"""Contract tests for seeding packaged catalog dovo/ templates into a repository."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import Filesystem, WorkspacePaths
from dovo.common.filesystem.services.paths import get_catalog_templates_dir
from dovo.common.utils import display_path
from dovo.core.catalog.exceptions import CatalogError
from dovo.core.catalog.models import CatalogItemType
from dovo.core.catalog.services.seeder import seed_all_catalog_templates, seed_catalog_templates

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]

_HEADER = "Could not seed the starter catalog templates:"
_TEMPLATES = Path(str(get_catalog_templates_dir()))


def _step_targets(root: Path) -> list[Path]:
    source_dir = _TEMPLATES / "steps" / "dovo"
    target_dir = root / ".dovo" / "catalog" / "steps" / "dovo"
    return [target_dir / p.relative_to(source_dir) for p in sorted(source_dir.rglob("*")) if p.is_file()]


class SeedCatalogTemplatesTests:
    def test_fresh_repository_creates_every_packaged_dovo_step_file(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        summary = seed_catalog_templates(CatalogItemType.STEP, workspace_paths_factory(tmp_path, None))

        assert summary.skipped_existing_files == ()
        assert summary.overwritten_files == ()
        assert list(summary.created_files) == _step_targets(tmp_path)
        for created in summary.created_files:
            assert created.is_file()

    def test_existing_file_without_force_is_skipped_not_overwritten(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        target = tmp_path / ".dovo" / "catalog" / "steps" / "dovo" / "run-tests.yml"
        target.parent.mkdir(parents=True)
        target.write_text("custom: content\n", encoding="utf-8")

        summary = seed_catalog_templates(CatalogItemType.STEP, workspace_paths_factory(tmp_path, None), force=False)

        assert summary.skipped_existing_files == (target,)
        assert target not in summary.created_files
        assert target.read_text(encoding="utf-8") == "custom: content\n"

    def test_existing_file_with_force_is_overwritten(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        target = tmp_path / ".dovo" / "catalog" / "steps" / "dovo" / "run-tests.yml"
        target.parent.mkdir(parents=True)
        target.write_text("custom: content\n", encoding="utf-8")

        summary = seed_catalog_templates(CatalogItemType.STEP, workspace_paths_factory(tmp_path, None), force=True)

        assert summary.overwritten_files == (target,)
        assert target.read_text(encoding="utf-8") != "custom: content\n"

    def test_directory_targets_raise_one_failure_listing_each_file(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """Try every step file, then raise CATALOG_SEED_FAILED listing each directory target on its own line."""
        step_dir = tmp_path / ".dovo" / "catalog" / "steps" / "dovo"
        blocked = [step_dir / "ai-planner.yml", step_dir / "run-tests.yml"]
        for target in blocked:
            target.mkdir(parents=True)

        with pytest.raises(CatalogError) as raised:
            seed_catalog_templates(CatalogItemType.STEP, workspace_paths_factory(tmp_path, None), force=True)

        assert raised.value.error_code == ErrorCode.CATALOG_SEED_FAILED
        assert raised.value.message == "\n".join(
            [_HEADER, *(f"{display_path(target)} exists as a directory, not a file." for target in blocked)]
        )
        assert raised.value.remediations == ()
        assert all(target.is_file() for target in _step_targets(tmp_path) if target not in blocked)

    def test_write_failure_chains_the_os_error(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """List '<path>: <error>' for each file that cannot be written, chained from the first OSError."""
        errors: list[OSError] = []

        def _raise(*_args: object, **_kwargs: object) -> None:
            errors.append(PermissionError(13, "Permission denied"))
            raise errors[-1]

        monkeypatch.setattr(Filesystem, "atomic_write_text", staticmethod(_raise))

        with pytest.raises(CatalogError) as raised:
            seed_catalog_templates(CatalogItemType.STEP, workspace_paths_factory(tmp_path, None))

        targets = _step_targets(tmp_path)
        assert raised.value.error_code == ErrorCode.CATALOG_SEED_FAILED
        assert raised.value.message == "\n".join(
            [_HEADER, *(f"{display_path(target)}: {error}" for target, error in zip(targets, errors, strict=True))]
        )
        assert raised.value.__cause__ is errors[0]


class SeedAllCatalogTemplatesTests:
    def test_aggregates_created_files_across_blueprint_and_step_types(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        summary = seed_all_catalog_templates(workspace_paths_factory(tmp_path, None))

        blueprint_dir = tmp_path / ".dovo" / "catalog" / "blueprints" / "dovo"
        step_dir = tmp_path / ".dovo" / "catalog" / "steps" / "dovo"
        assert blueprint_dir.is_dir()
        assert step_dir.is_dir()
        assert any(p.is_relative_to(blueprint_dir) for p in summary.created_files)
        assert any(p.is_relative_to(step_dir) for p in summary.created_files)

    def test_second_call_without_force_skips_everything_already_seeded(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        paths = workspace_paths_factory(tmp_path, None)
        first = seed_all_catalog_templates(paths)
        second = seed_all_catalog_templates(paths, force=False)

        assert second.created_files == ()
        assert second.skipped_existing_files == first.created_files

    def test_blueprint_failure_still_seeds_steps_then_raises(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """Seed every step file after a blueprint target fails, then raise one CATALOG_SEED_FAILED naming it."""
        blocked = tmp_path / ".dovo" / "catalog" / "blueprints" / "dovo" / "fix-tests.yml"
        blocked.mkdir(parents=True)

        with pytest.raises(CatalogError) as raised:
            seed_all_catalog_templates(workspace_paths_factory(tmp_path, None))

        assert raised.value.error_code == ErrorCode.CATALOG_SEED_FAILED
        assert raised.value.message == f"{_HEADER}\n{display_path(blocked)} exists as a directory, not a file."
        assert all(target.is_file() for target in _step_targets(tmp_path))
