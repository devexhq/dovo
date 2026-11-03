"""Contract tests for the Artifacts entrypoint."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.artifacts import Artifacts
from dovo.core.artifacts.models import ArtifactProvenance
from dovo.core.config.models import DovoConfig, ProjectConfig, PruneConfig
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from tests.harness.diagnostic_log import diagnostic_record


def _worktree(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create a worktree holding the given files."""
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir(exist_ok=True)
    for relative_path, content in files.items():
        (worktree_path / relative_path).write_text(content, encoding="utf-8")
    return worktree_path


def _publish_plan(artifacts: Artifacts, worktree_path: Path) -> None:
    """Publish PLAN.md as 'plan' for session wf_1 and assert it succeeded."""
    result = artifacts.publish("wf_1", "plan", "PLAN.md", worktree_path=worktree_path, provenance=ArtifactProvenance())
    assert result.ok


class ArtifactsTests:
    """Contract tests for Artifacts publishing, reference resolution and pruning."""

    def test_publish_stores_bundle_under_given_session(
        self, tmp_path: Path, artifacts_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        artifacts = Artifacts(artifacts_paths, artifacts_repository)

        _publish_plan(artifacts, _worktree(tmp_path, {"PLAN.md": "plan"}))

        assert [record.name for record in artifacts.list(session_id="wf_1").artifacts] == ["plan"]

    @pytest.mark.parametrize(
        ("remove_expired", "expected"),
        [
            pytest.param(None, True, id="no-config"),
            pytest.param(True, False, id="config-enabled"),
        ],
    )
    def test_prune_resolves_remove_expired_from_config(
        self,
        remove_expired: bool | None,
        expected: bool,
        artifacts_paths: WorkspacePaths,
        artifacts_repository: ArtifactsRepository,
    ) -> None:
        config = (
            None
            if remove_expired is None
            else DovoConfig(
                version=1,
                project=ProjectConfig(name="test-project"),
                prune=PruneConfig(remove_expired_artifacts=remove_expired),
            )
        )

        result = Artifacts(artifacts_paths, artifacts_repository).prune(config=config)

        assert (result.disabled, result.error_code) == (expected, None)


class ArtifactsDownloadFailureTests:
    """Contract tests for the failures Artifacts.download copies into its result."""

    def test_download_checksum_mismatch_reports_code_and_leaves_dest_untouched(
        self, tmp_path: Path, artifacts_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        artifacts = Artifacts(artifacts_paths, artifacts_repository)
        _publish_plan(artifacts, _worktree(tmp_path, {"PLAN.md": "plan"}))
        (artifacts_paths.artifacts_dir / "wf_1" / "plan" / "1" / "PLAN.md").write_text("tampered", encoding="utf-8")
        dest = tmp_path / "out"

        result = artifacts.download("wf_1", "plan", dest=dest)

        assert (result.ok, result.error_code, result.errors, result.diagnostics) == (
            False,
            ErrorCode.ARTIFACT_CHECKSUM_INVALID,
            ["Checksum mismatch for: PLAN.md"],
            [],
        )
        assert not dest.exists()

    def test_download_unreadable_manifest_lists_its_diagnostic_record(
        self,
        tmp_path: Path,
        artifacts_paths: WorkspacePaths,
        artifacts_repository: ArtifactsRepository,
        frozen_diagnostic_clock: Path,
    ) -> None:
        artifacts = Artifacts(artifacts_paths, artifacts_repository)
        _publish_plan(artifacts, _worktree(tmp_path, {"PLAN.md": "plan"}))
        manifest_path = artifacts_paths.artifacts_dir / "wf_1" / "plan" / "1" / "manifest.json"
        manifest_path.unlink()

        result = artifacts.download("wf_1", "plan", dest=tmp_path / "out")

        assert (result.error_code, result.errors, result.session_id, result.name) == (
            ErrorCode.ARTIFACT_DOWNLOAD_FAILED,
            [f"[Errno 2] No such file or directory: '{manifest_path}'"],
            "wf_1",
            "plan",
        )
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
