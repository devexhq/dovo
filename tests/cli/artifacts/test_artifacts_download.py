"""Single-tier CLI integration tests for dovo artifacts download."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.artifacts import Artifacts
from dovo.core.artifacts.models import ArtifactProvenance
from dovo.core.db import DovoDb
from tests.harness.workspace_paths import initialized_workspace_paths


def _publish_artifact(workspace: Path, *, session_id: str, name: str, content: bytes = b"package-bytes") -> Path:
    """Publish a real artifact bundle under session_id/name and return its worktree source path."""
    worktree_path = workspace / "worktree-scratch" / session_id
    (worktree_path / "dist").mkdir(parents=True, exist_ok=True)
    (worktree_path / "dist" / "pkg.whl").write_bytes(content)
    paths = initialized_workspace_paths(workspace)
    db = DovoDb(database_file=paths.database_file, project_id=paths.project_id)
    result = Artifacts(paths, db=db.artifacts).publish(
        session_id, name, "dist/*.whl", worktree_path=worktree_path, provenance=ArtifactProvenance()
    )
    assert result.ok
    return worktree_path


class ArtifactsDownloadCliIntegrationTests:
    """Typer runner integration tests for dovo artifacts download."""

    def test_artifacts_download_cli_happy_path_extracts_files_and_exits_zero(
        self, cli_runner: CliRunner, artifacts_workspace: Path, tmp_path: Path
    ) -> None:
        _publish_artifact(artifacts_workspace, session_id="wf_one", name="dist-packages")
        dest = tmp_path / "out"

        result = cli_runner.invoke(
            app,
            ["-p", str(artifacts_workspace), "artifacts", "download", "wf_one", "dist-packages", "--dest", str(dest)],
        )

        assert result.exit_code == 0
        assert (dest / "dist" / "pkg.whl").read_bytes() == b"package-bytes"

    def test_artifacts_download_cli_not_found_exits_one(
        self, cli_runner: CliRunner, artifacts_workspace: Path, tmp_path: Path
    ) -> None:
        dest = tmp_path / "out"

        result = cli_runner.invoke(
            app,
            ["-p", str(artifacts_workspace), "artifacts", "download", "wf_one", "missing", "--dest", str(dest)],
        )

        assert result.exit_code == 1
        assert "Artifact 'missing' not found for session 'wf_one'" in result.stdout

    def test_artifacts_download_cli_checksum_mismatch_exits_one_without_partial_files(
        self, cli_runner: CliRunner, artifacts_workspace: Path, tmp_path: Path
    ) -> None:
        _publish_artifact(artifacts_workspace, session_id="wf_one", name="dist-packages")
        artifacts_dir = initialized_workspace_paths(artifacts_workspace).artifacts_dir
        artifact_dir = artifacts_dir / "wf_one" / "dist-packages" / "1"
        (artifact_dir / "dist" / "pkg.whl").write_bytes(b"corrupted-bytes")
        dest = tmp_path / "out"

        result = cli_runner.invoke(
            app,
            ["-p", str(artifacts_workspace), "artifacts", "download", "wf_one", "dist-packages", "--dest", str(dest)],
        )

        assert result.exit_code == 1
        assert not dest.exists()

    def test_artifacts_download_cli_format_json_emits_wire_schema(
        self, cli_runner: CliRunner, artifacts_workspace: Path, tmp_path: Path
    ) -> None:
        _publish_artifact(artifacts_workspace, session_id="wf_one", name="dist-packages")
        dest = tmp_path / "out"

        result = cli_runner.invoke(
            app,
            [
                "-p",
                str(artifacts_workspace),
                "artifacts",
                "download",
                "wf_one",
                "dist-packages",
                "--dest",
                str(dest),
                "--format",
                "json",
            ],
        )

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {
            "event_type": "ArtifactDownloadResult",
            "payload": {
                "status": "ok",
                "session_id": "wf_one",
                "name": "dist-packages",
                "dest": str(dest),
                "revision": 1,
                "file_count": 1,
                "errors": [],
                "warnings": [],
                "error_code": None,
                "fixes": [],
            },
        }

    def test_artifacts_download_cli_revision_option_binds_to_domain_and_extracts_that_revision(
        self, cli_runner: CliRunner, artifacts_workspace: Path, tmp_path: Path
    ) -> None:
        _publish_artifact(artifacts_workspace, session_id="wf_one", name="plan", content=b"v1")
        _publish_artifact(artifacts_workspace, session_id="wf_one", name="plan", content=b"v2")
        dest = tmp_path / "out"

        result = cli_runner.invoke(
            app,
            [
                "-p",
                str(artifacts_workspace),
                "artifacts",
                "download",
                "wf_one",
                "plan",
                "--dest",
                str(dest),
                "--revision",
                "1",
            ],
        )

        assert result.exit_code == 0
        assert (dest / "dist" / "pkg.whl").read_bytes() == b"v1"

    def test_artifacts_download_cli_unknown_revision_exits_one(
        self, cli_runner: CliRunner, artifacts_workspace: Path, tmp_path: Path
    ) -> None:
        _publish_artifact(artifacts_workspace, session_id="wf_one", name="plan")

        result = cli_runner.invoke(
            app,
            [
                "-p",
                str(artifacts_workspace),
                "artifacts",
                "download",
                "wf_one",
                "plan",
                "--dest",
                str(tmp_path / "out"),
                "--revision",
                "9",
            ],
        )

        assert result.exit_code == 1
        assert "Artifact 'plan' revision 9 not found for session 'wf_one'" in result.stdout

    def test_artifacts_download_cli_revision_zero_is_rejected_by_option_validation(
        self, cli_runner: CliRunner, artifacts_workspace: Path, tmp_path: Path
    ) -> None:
        _publish_artifact(artifacts_workspace, session_id="wf_one", name="plan")
        dest = tmp_path / "out"

        result = cli_runner.invoke(
            app,
            [
                "-p",
                str(artifacts_workspace),
                "artifacts",
                "download",
                "wf_one",
                "plan",
                "--dest",
                str(dest),
                "--revision",
                "0",
            ],
        )

        assert result.exit_code == 2
        assert not dest.exists()
