"""Single-tier CLI integration tests for dovo artifacts download."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.artifacts import Artifacts
from dovo.core.db import DovoDb
from tests.harness.workspace_paths import initialized_workspace_paths


def _publish_artifact(workspace: Path, *, session_id: str, name: str) -> Path:
    """Publish a real artifact bundle under session_id/name and return its worktree source path."""
    worktree_path = workspace / "worktree-scratch" / session_id
    (worktree_path / "dist").mkdir(parents=True, exist_ok=True)
    (worktree_path / "dist" / "pkg.whl").write_bytes(b"package-bytes")
    paths = initialized_workspace_paths(workspace)
    db = DovoDb(database_file=paths.database_file, project_id=paths.project_id)
    result = Artifacts(paths, db=db.artifacts).upload(session_id, name, "dist/*.whl", worktree_path=worktree_path)
    assert result.ok
    return worktree_path


class ArtifactsDownloadCliIntegrationTests:
    """Typer runner integration tests for dovo artifacts download."""

    def test_artifacts_download_cli_happy_path_extracts_files_and_exits_zero(
        self, cli_runner: CliRunner, artifacts_workspace: Path, tmp_path: Path
    ) -> None:
        """dovo artifacts download <id> <name> --dest <path>: exit 0 and the file is extracted to --dest."""
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
        """dovo artifacts download <id> missing --dest ./out: exit 1, "Artifact 'missing' not found for session '<id>'" in stdout."""
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
        """dovo artifacts download: a corrupted on-disk file vs. manifest.json exits 1 with status CHECKSUM_MISMATCH and leaves --dest empty."""
        _publish_artifact(artifacts_workspace, session_id="wf_one", name="dist-packages")
        artifacts_dir = initialized_workspace_paths(artifacts_workspace).artifacts_dir
        artifact_dir = artifacts_dir / "wf_one" / "dist-packages"
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
        """dovo artifacts download --format json: stdout equals the literal ArtifactDownloadResult envelope."""
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
                "file_count": 1,
                "errors": [],
                "warnings": [],
                "error_code": None,
                "fixes": [],
            },
        }
