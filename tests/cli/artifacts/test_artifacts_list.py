"""Single-tier CLI integration tests for dovo artifacts list."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.artifacts import Artifacts
from dovo.core.db import DovoDb
from tests.harness.workspace_paths import initialized_workspace_paths


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return initialized_workspace_paths(root)


def _publish_artifact(workspace: Path, *, session_id: str, name: str) -> None:
    """Publish a real artifact bundle under session_id/name for CLI list/download fixtures."""
    worktree_path = workspace / "worktree-scratch" / session_id
    (worktree_path / "dist").mkdir(parents=True, exist_ok=True)
    (worktree_path / "dist" / "pkg.whl").write_bytes(b"package-bytes")
    paths = _paths_for(workspace)
    db = DovoDb(database_file=paths.database_file, project_id=paths.project_id)
    Artifacts(paths, db=db.artifacts).upload(session_id, name, "dist/*.whl", worktree_path=worktree_path)


class ArtifactsListCliIntegrationTests:
    """Typer runner integration tests for dovo artifacts list."""

    def test_artifacts_list_cli_empty_workspace_prints_no_artifacts_found(
        self, cli_runner: CliRunner, artifacts_workspace: Path
    ) -> None:
        """dovo artifacts list: no artifacts published yet -> exit 0, 'No artifacts found.' in stdout."""
        result = cli_runner.invoke(app, ["-p", str(artifacts_workspace), "artifacts", "list"])

        assert result.exit_code == 0
        assert "No artifacts found." in result.stdout

    def test_artifacts_list_cli_session_flag_filters_rows(
        self, cli_runner: CliRunner, artifacts_workspace: Path
    ) -> None:
        """dovo artifacts list --session <id>: only rows matching <id> appear in the rendered table."""
        _publish_artifact(artifacts_workspace, session_id="wf_one", name="dist-one")
        _publish_artifact(artifacts_workspace, session_id="wf_two", name="dist-two")

        result = cli_runner.invoke(app, ["-p", str(artifacts_workspace), "artifacts", "list", "--session", "wf_one"])

        assert result.exit_code == 0
        assert "dist-one" in result.stdout
        assert "dist-two" not in result.stdout

    def test_artifacts_list_cli_format_json_emits_wire_schema(
        self, cli_runner: CliRunner, artifacts_workspace: Path
    ) -> None:
        """dovo artifacts list --format json: no artifacts published -> stdout equals the literal empty-list wire payload."""
        result = cli_runner.invoke(app, ["-p", str(artifacts_workspace), "artifacts", "list", "--format", "json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {
            "event_type": "ArtifactsListResult",
            "payload": {"status": "ok", "artifacts": [], "errors": [], "warnings": [], "fixes": []},
        }
