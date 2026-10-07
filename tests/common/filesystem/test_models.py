"""Contract tests for common filesystem models."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from dovo.common.filesystem.models import GlobalPaths, RepositoryPaths, WorkspacePaths
from dovo.common.filesystem.services.paths import get_catalog_templates_dir
from dovo.core.catalog.models import CatalogTier
from dovo.core.project.models import ProjectIdentity
from dovo.core.project.services.identity import save_project_identity
from dovo.core.project.services.storage import resolve_workspace_paths


class GlobalPathsTests:
    """Contract tests for canonical global filesystem paths."""

    def test_from_root_resolves_root_and_derives_global_hierarchy(self, tmp_path: Path) -> None:
        source_root = tmp_path / "nested" / "global-root"

        paths = GlobalPaths.from_root(source_root)

        expected_root = source_root.resolve()
        assert paths.root == expected_root
        assert paths.global_dir == expected_root / "global"
        assert paths.global_catalog_dir == expected_root / "global" / "catalog"
        assert paths.user_dir == expected_root / "user"
        assert paths.user_catalog_dir == expected_root / "user" / "catalog"
        assert paths.data_dir == expected_root / "data"
        assert paths.storage_dir == expected_root / "storage"


class RepositoryPathsTests:
    """Contract tests for repository-local path discovery."""

    def test_from_root_derives_repository_local_paths(self, tmp_path: Path) -> None:
        """[tier-1/unit] RepositoryPaths.from_root: derives every repo-local child path under .dovo/, and the real .lock filename."""
        repo_root = tmp_path / "repository"

        paths = RepositoryPaths.from_root(repo_root)

        dovo_dir = repo_root / ".dovo"
        assert paths.root_dir == repo_root
        assert paths.dovo_dir == dovo_dir
        assert paths.config_file == dovo_dir / "config.json"
        assert paths.catalog_dir == dovo_dir / "catalog"
        assert paths.catalog_steps_dir == dovo_dir / "catalog" / "steps"
        assert paths.catalog_blueprints_dir == dovo_dir / "catalog" / "blueprints"
        assert paths.worktrees_dir == dovo_dir / "worktrees"
        assert paths.lock_file == dovo_dir / ".lock"
        assert paths.gitignore_file == repo_root / ".gitignore"

    def test_from_root_given_dovo_dir_resolves_parent_as_root(self, tmp_path: Path) -> None:
        """[tier-1/unit] RepositoryPaths.from_root: passing the .dovo directory itself resolves root_dir to its parent."""
        repo_root = tmp_path / "repository"
        dovo_dir = repo_root / ".dovo"

        paths = RepositoryPaths.from_root(dovo_dir)

        assert paths.root_dir == repo_root
        assert paths.dovo_dir == dovo_dir


def _build_workspace_paths(root: Path, global_root: Path, *, project_id: str = "test-project") -> WorkspacePaths:
    repository_paths = RepositoryPaths.from_root(root)
    global_paths = GlobalPaths.from_root(global_root)
    runtime_root = global_paths.storage_dir / "projects" / project_id
    return WorkspacePaths(
        root_dir=repository_paths.root_dir,
        dovo_dir=repository_paths.dovo_dir,
        config_file=repository_paths.config_file,
        catalog_dir=repository_paths.catalog_dir,
        catalog_steps_dir=repository_paths.catalog_steps_dir,
        catalog_blueprints_dir=repository_paths.catalog_blueprints_dir,
        worktrees_dir=repository_paths.worktrees_dir,
        lock_file=repository_paths.lock_file,
        gitignore_file=repository_paths.gitignore_file,
        catalog_templates_dir=get_catalog_templates_dir(),
        global_paths=global_paths,
        database_file=global_paths.data_dir / "dovo.db",
        project_id=project_id,
        runtime_root=runtime_root,
        logs_dir=runtime_root / "logs",
        sessions_dir=runtime_root / "sessions",
        artifacts_dir=runtime_root / "artifacts",
        tmp_dir=runtime_root / "tmp",
    )


@pytest.fixture
def sample_workspace_paths(tmp_path: Path) -> WorkspacePaths:
    return _build_workspace_paths(tmp_path / "repository", tmp_path / "global")


@pytest.fixture
def fixture_repo_with_identity(tmp_path: Path) -> Path:
    """Create a repository root with a persisted identity for global runtime storage."""
    repository = tmp_path / "fixture-repository-with-identity"
    dovo_dir = repository / ".dovo"
    dovo_dir.mkdir(parents=True)
    identity = ProjectIdentity(id="project-626", created_at=datetime(2026, 1, 1, tzinfo=UTC))
    save_project_identity(dovo_dir / "project.json", identity)
    return repository


def _resolve_fixture_workspace_paths(repository: Path) -> WorkspacePaths:
    global_paths = GlobalPaths.from_root(repository.parent / "legacy-global-root")
    return resolve_workspace_paths(RepositoryPaths.from_root(repository), global_paths)


class WorkspacePathsContractTests:
    def test_catalog_dir_for_packaged_raises_value_error(self, sample_workspace_paths: WorkspacePaths) -> None:
        """[tier-1/unit] WorkspacePaths.catalog_dir_for(CatalogTier.PACKAGED): raises ValueError with message "Tier 'packaged' is not disk-backed and has no tier root."."""
        with pytest.raises(ValueError, match="not disk-backed"):
            sample_workspace_paths.catalog_dir_for(CatalogTier.PACKAGED)

    @pytest.mark.parametrize(
        ("tier", "expected_attr"),
        [
            pytest.param(CatalogTier.REPO, "catalog_dir", id="repo"),
            pytest.param(CatalogTier.USER, "user_catalog_dir", id="user"),
            pytest.param(CatalogTier.GLOBAL, "global_catalog_dir", id="global"),
        ],
    )
    def test_catalog_dir_for_disk_backed_tiers_resolves_expected_root(
        self, sample_workspace_paths: WorkspacePaths, tier: CatalogTier, expected_attr: str
    ) -> None:
        """[tier-1/unit] WorkspacePaths.catalog_dir_for: REPO/USER/GLOBAL each resolve to their real disk-backed tier root."""
        if tier is CatalogTier.REPO:
            expected = sample_workspace_paths.catalog_dir
        else:
            expected = getattr(sample_workspace_paths.global_paths, expected_attr)

        assert sample_workspace_paths.catalog_dir_for(tier) == expected

    def test_session_dir_and_worktree_dir_do_not_create_directories(
        self, sample_workspace_paths: WorkspacePaths, tmp_path: Path
    ) -> None:
        """[tier-1/unit] WorkspacePaths.session_dir/worktree_dir: returned paths do not exist on disk after the call (no mkdir side effect)."""
        session_dir = sample_workspace_paths.session_dir("sess_1")
        worktree_dir = sample_workspace_paths.worktree_dir("dovo_1")

        assert session_dir == sample_workspace_paths.sessions_dir / "sess_1"
        assert worktree_dir == sample_workspace_paths.worktrees_dir / "dovo_1"
        assert not session_dir.exists()
        assert not worktree_dir.exists()


class WorkspacePathsParityTests:
    def test_workspace_paths_matches_legacy_resolution_with_project_identity(
        self, fixture_repo_with_identity: Path
    ) -> None:
        """[tier-2/unit] WorkspacePaths keeps the legacy project-aware runtime layout field-for-field."""
        paths = _resolve_fixture_workspace_paths(fixture_repo_with_identity)
        legacy_dovo_dir = fixture_repo_with_identity / ".dovo"
        legacy_runtime_root = paths.global_paths.storage_dir / "projects" / "project-626"
        legacy_layout = {
            "root_dir": fixture_repo_with_identity,
            "dovo_dir": legacy_dovo_dir,
            "config_file": legacy_dovo_dir / "config.json",
            "catalog_dir": legacy_dovo_dir / "catalog",
            "catalog_steps_dir": legacy_dovo_dir / "catalog" / "steps",
            "catalog_blueprints_dir": legacy_dovo_dir / "catalog" / "blueprints",
            "worktrees_dir": legacy_dovo_dir / "worktrees",
            "gitignore_file": fixture_repo_with_identity / ".gitignore",
            "runtime_root": legacy_runtime_root,
            "logs_dir": legacy_runtime_root / "logs",
            "sessions_dir": legacy_runtime_root / "sessions",
            "artifacts_dir": legacy_runtime_root / "artifacts",
            "tmp_dir": legacy_runtime_root / "tmp",
        }

        for field, expected_path in legacy_layout.items():
            assert getattr(paths, field) == expected_path
        assert paths.lock_file == legacy_dovo_dir / ".lock"
        assert paths.lock_file != legacy_dovo_dir / "dovo.lock"


class WorkspacePathsModelTests:
    def test_project_id_none_raises_validation_error(self, sample_workspace_paths: WorkspacePaths) -> None:
        """[tier-1/unit] WorkspacePaths: constructing it with project_id None raises ValidationError."""
        fields = dict(sample_workspace_paths)
        fields["project_id"] = None

        with pytest.raises(ValidationError):
            WorkspacePaths(**fields)

    def test_project_id_omitted_raises_validation_error(self, sample_workspace_paths: WorkspacePaths) -> None:
        """[tier-1/unit] WorkspacePaths: constructing it without project_id raises ValidationError."""
        fields = dict(sample_workspace_paths)
        del fields["project_id"]

        with pytest.raises(ValidationError):
            WorkspacePaths(**fields)
