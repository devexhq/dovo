"""Single-tier CLI integration tests for dovo artifacts download."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.artifacts import Artifacts
from dovo.core.artifacts.models import ArtifactProvenance
from dovo.core.db import DovoDb
from tests.harness.exit_codes import OUTPUT_FORMATS, assert_exit_follows_result
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

    @pytest.mark.parametrize("output_format", OUTPUT_FORMATS)
    @pytest.mark.parametrize(
        ("name", "exit_code", "error_code"),
        [
            pytest.param("dist-packages", 0, None, id="published_artifact"),
            pytest.param("missing", 1, "ARTIFACT_NOT_FOUND", id="unknown_artifact"),
        ],
    )
    def test_exit_code_follows_result(
        self,
        cli_runner: CliRunner,
        cli_workspace: Path,
        tmp_path: Path,
        output_format: str,
        name: str,
        exit_code: int,
        error_code: str | None,
    ) -> None:
        _publish_artifact(cli_workspace, session_id="wf_one", name="dist-packages")

        result = cli_runner.invoke(
            app,
            [
                "-p",
                str(cli_workspace),
                "artifacts",
                "download",
                "wf_one",
                name,
                "--dest",
                str(tmp_path / "out"),
                "--format",
                output_format,
            ],
        )

        assert_exit_follows_result(result, output_format, exit_code, error_code)

    def test_artifacts_download_cli_happy_path_extracts_files_and_exits_zero(
        self, cli_runner: CliRunner, cli_workspace: Path, tmp_path: Path
    ) -> None:
        _publish_artifact(cli_workspace, session_id="wf_one", name="dist-packages")
        dest = tmp_path / "out"

        result = cli_runner.invoke(
            app,
            ["-p", str(cli_workspace), "artifacts", "download", "wf_one", "dist-packages", "--dest", str(dest)],
        )

        assert result.exit_code == 0
        assert (dest / "dist" / "pkg.whl").read_bytes() == b"package-bytes"

    def test_artifacts_download_cli_not_found_exits_one(
        self, cli_runner: CliRunner, cli_workspace: Path, tmp_path: Path
    ) -> None:
        dest = tmp_path / "out"

        result = cli_runner.invoke(
            app,
            ["-p", str(cli_workspace), "artifacts", "download", "wf_one", "missing", "--dest", str(dest)],
        )

        assert result.exit_code == 1
        assert "Artifact 'missing' not found for session 'wf_one'" in result.stdout

    def test_artifacts_download_cli_format_json_emits_wire_schema(
        self, cli_runner: CliRunner, cli_workspace: Path, tmp_path: Path
    ) -> None:
        _publish_artifact(cli_workspace, session_id="wf_one", name="dist-packages")
        dest = tmp_path / "out"

        result = cli_runner.invoke(
            app,
            [
                "-p",
                str(cli_workspace),
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
                "session_id": "wf_one",
                "name": "dist-packages",
                "dest": str(dest),
                "revision": 1,
                "file_count": 1,
                "errors": [],
                "warnings": [],
                "error_code": None,
                "remediations": [],
                "diagnostics": [],
            },
        }

    def test_artifacts_download_cli_format_json_not_found_emits_error_code(
        self, cli_runner: CliRunner, cli_workspace: Path, tmp_path: Path
    ) -> None:
        dest = tmp_path / "out"

        result = cli_runner.invoke(
            app,
            [
                "-p",
                str(cli_workspace),
                "artifacts",
                "download",
                "wf_one",
                "missing",
                "--dest",
                str(dest),
                "--format",
                "json",
            ],
        )

        assert result.exit_code == 1
        assert json.loads(result.stdout) == {
            "event_type": "ArtifactDownloadResult",
            "payload": {
                "session_id": "wf_one",
                "name": "missing",
                "dest": "",
                "revision": 0,
                "file_count": 0,
                "errors": ["Artifact 'missing' not found for session 'wf_one'"],
                "warnings": [],
                "error_code": "ARTIFACT_NOT_FOUND",
                "remediations": [],
                "diagnostics": [],
            },
        }

    def test_artifacts_download_cli_revision_option_binds_to_domain_and_extracts_that_revision(
        self, cli_runner: CliRunner, cli_workspace: Path, tmp_path: Path
    ) -> None:
        _publish_artifact(cli_workspace, session_id="wf_one", name="plan", content=b"v1")
        _publish_artifact(cli_workspace, session_id="wf_one", name="plan", content=b"v2")
        dest = tmp_path / "out"

        result = cli_runner.invoke(
            app,
            [
                "-p",
                str(cli_workspace),
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

    def test_artifacts_download_cli_revision_zero_is_rejected_by_option_validation(
        self, cli_runner: CliRunner, cli_workspace: Path, tmp_path: Path
    ) -> None:
        _publish_artifact(cli_workspace, session_id="wf_one", name="plan")
        dest = tmp_path / "out"

        result = cli_runner.invoke(
            app,
            [
                "-p",
                str(cli_workspace),
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
