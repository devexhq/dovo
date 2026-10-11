"""Unit tests for dovo.core.diagnostics.checks.artifacts_integrity."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.common.filesystem import WorkspacePaths
from dovo.core.artifacts.models import ArtifactProvenance
from dovo.core.artifacts.services.upload import publish_artifact
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from dovo.core.diagnostics.checks.artifacts_integrity import ArtifactsIntegrityCheck
from dovo.core.diagnostics.models import CheckCategory, CheckStatus, DiagnosticsContext
from tests.harness import WorkspaceBuilder

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]


@pytest.fixture
def artifacts_integrity_paths(tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory) -> WorkspacePaths:
    """WorkspacePaths for a workspace with an initialized database and no artifacts."""
    workspace = WorkspaceBuilder(tmp_path / "artifacts_integrity_ws").with_database().build()
    return workspace_paths_factory(workspace, None)


@pytest.fixture
def artifacts_repository(artifacts_integrity_paths: WorkspacePaths) -> ArtifactsRepository:
    """ArtifactsRepository bound to the artifacts_integrity_paths workspace."""
    return ArtifactsRepository(
        db_path=artifacts_integrity_paths.database_file, project_id=artifacts_integrity_paths.project_id
    )


def _publish_plan(paths: WorkspacePaths, repository: ArtifactsRepository, tmp_path: Path, content: bytes) -> Path:
    """Publish PLAN.md with content as the next revision of 'plan' and return its revision directory."""
    worktree_path = tmp_path / f"worktree-{content.decode()}"
    worktree_path.mkdir()
    (worktree_path / "PLAN.md").write_bytes(content)
    result = publish_artifact(
        worktree_path,
        paths.artifacts_dir,
        repository,
        session_id="wf_1",
        name="plan",
        path_glob="PLAN.md",
        retention_days=None,
        provenance=ArtifactProvenance(),
    )
    assert result.ok
    return paths.artifacts_dir / "wf_1" / "plan" / str(result.revision)


class ArtifactsIntegrityCheckTests:
    """Unit tests for ArtifactsIntegrityCheck diagnostic outcomes."""

    def test_execute_clean_workspace_returns_ok_with_verified_count(
        self, artifacts_integrity_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository, tmp_path: Path
    ) -> None:
        """[tier-1/integration] ArtifactsIntegrityCheck.execute: two published revisions with matching rows -> OK, verified_count 2, no error_code."""
        _publish_plan(artifacts_integrity_paths, artifacts_repository, tmp_path, b"one")
        _publish_plan(artifacts_integrity_paths, artifacts_repository, tmp_path, b"two")

        result = ArtifactsIntegrityCheck().execute(
            DiagnosticsContext(cwd=artifacts_integrity_paths.root_dir, paths=artifacts_integrity_paths)
        )

        assert (result.check_id, result.category) == ("artifacts.integrity", CheckCategory.ARTIFACTS)
        assert result.status == CheckStatus.OK
        assert result.error_code is None
        assert result.details == {"verified_count": 2}

    def test_execute_directory_without_row_returns_orphan_warning(
        self, artifacts_integrity_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository, tmp_path: Path
    ) -> None:
        """[tier-1/integration] ArtifactsIntegrityCheck.execute: a <name>/3 directory with manifest.json and no row -> WARNING DOCTOR_ARTIFACT_ORPHAN_DIR naming that directory."""
        revision_dir = _publish_plan(artifacts_integrity_paths, artifacts_repository, tmp_path, b"one")
        orphan_dir = revision_dir.parent / "3"
        shutil.copytree(revision_dir, orphan_dir)

        result = ArtifactsIntegrityCheck().execute(
            DiagnosticsContext(cwd=artifacts_integrity_paths.root_dir, paths=artifacts_integrity_paths)
        )

        assert result.status == CheckStatus.WARNING
        assert result.error_code == "DOCTOR_ARTIFACT_ORPHAN_DIR"
        assert result.details == {"orphan_directories": [str(orphan_dir.resolve())]}

    def test_execute_row_without_directory_returns_missing_warning(
        self, artifacts_integrity_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository, tmp_path: Path
    ) -> None:
        """[tier-1/integration] ArtifactsIntegrityCheck.execute: a row whose directory was deleted -> WARNING DOCTOR_ARTIFACT_MISSING_DIR naming '<session>/<name>@<revision>'; the row still exists afterwards."""
        revision_dir = _publish_plan(artifacts_integrity_paths, artifacts_repository, tmp_path, b"one")
        shutil.rmtree(revision_dir)

        result = ArtifactsIntegrityCheck().execute(
            DiagnosticsContext(cwd=artifacts_integrity_paths.root_dir, paths=artifacts_integrity_paths)
        )

        assert result.status == CheckStatus.WARNING
        assert result.error_code == "DOCTOR_ARTIFACT_MISSING_DIR"
        assert result.details == {"missing_directories": ["wf_1/plan@1"]}
        assert artifacts_repository.get("wf_1", "plan") is not None

    def test_execute_both_drifts_prioritizes_missing_directory(
        self, artifacts_integrity_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository, tmp_path: Path
    ) -> None:
        """[tier-1/integration] ArtifactsIntegrityCheck.execute: orphan directory and missing directory together -> error_code DOCTOR_ARTIFACT_MISSING_DIR."""
        first_dir = _publish_plan(artifacts_integrity_paths, artifacts_repository, tmp_path, b"one")
        second_dir = _publish_plan(artifacts_integrity_paths, artifacts_repository, tmp_path, b"two")
        shutil.rmtree(first_dir)
        shutil.copytree(second_dir, second_dir.parent / "3")

        result = ArtifactsIntegrityCheck().execute(
            DiagnosticsContext(cwd=artifacts_integrity_paths.root_dir, paths=artifacts_integrity_paths)
        )

        assert result.error_code == "DOCTOR_ARTIFACT_MISSING_DIR"

    def test_execute_missing_database_returns_ok_with_zero_verified_count(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] ArtifactsIntegrityCheck.execute: no database file -> OK, verified_count 0, nothing created on disk."""
        paths = workspace_paths_factory(tmp_path, None)

        result = ArtifactsIntegrityCheck().execute(DiagnosticsContext(cwd=tmp_path, paths=paths))

        assert result.status == CheckStatus.OK
        assert result.details == {"verified_count": 0}
        assert not paths.database_file.exists()

    def test_execute_ignores_staging_directories(
        self, artifacts_integrity_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository, tmp_path: Path
    ) -> None:
        """[tier-1/integration] ArtifactsIntegrityCheck.execute: a .tmp-plan-<hex> directory containing manifest.json is not reported."""
        revision_dir = _publish_plan(artifacts_integrity_paths, artifacts_repository, tmp_path, b"one")
        shutil.copytree(revision_dir, revision_dir.parent.parent / ".tmp-plan-abc123")

        result = ArtifactsIntegrityCheck().execute(
            DiagnosticsContext(cwd=artifacts_integrity_paths.root_dir, paths=artifacts_integrity_paths)
        )

        assert result.status == CheckStatus.OK

    def test_execute_bundle_containing_nested_manifest_json_is_not_orphan(
        self, artifacts_integrity_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository, tmp_path: Path
    ) -> None:
        """[tier-1/integration] ArtifactsIntegrityCheck.execute: a published bundle holding build/manifest.json -> OK, only the revision directory is verified."""
        worktree_path = tmp_path / "worktree-nested"
        (worktree_path / "build").mkdir(parents=True)
        (worktree_path / "build" / "manifest.json").write_text("{}", encoding="utf-8")
        result = publish_artifact(
            worktree_path,
            artifacts_integrity_paths.artifacts_dir,
            artifacts_repository,
            session_id="wf_1",
            name="site",
            path_glob="build/*",
            retention_days=None,
            provenance=ArtifactProvenance(),
        )
        assert result.ok

        check_result = ArtifactsIntegrityCheck().execute(
            DiagnosticsContext(cwd=artifacts_integrity_paths.root_dir, paths=artifacts_integrity_paths)
        )

        assert check_result.status == CheckStatus.OK
        assert check_result.details == {"verified_count": 1}
