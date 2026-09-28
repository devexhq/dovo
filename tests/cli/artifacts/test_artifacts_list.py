"""Single-tier CLI integration tests for wt artifacts list."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from worktree.cli import app
from worktree.common.filesystem.models import RepositoryPaths, WorkspacePaths
from worktree.common.filesystem.services.global_root import resolve_global_paths
from worktree.core.artifacts import Artifacts
from worktree.core.db import WorktreeDb
from worktree.core.project.services.storage import resolve_workspace_paths


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(None))


def _publish_artifact(workspace: Path, *, session_id: str, name: str) -> None:
    """Publish a real artifact bundle under session_id/name for CLI list/download fixtures."""
    sandbox_path = workspace / "sandbox-scratch" / session_id
    (sandbox_path / "dist").mkdir(parents=True, exist_ok=True)
    (sandbox_path / "dist" / "pkg.whl").write_bytes(b"package-bytes")
    paths = _paths_for(workspace)
    db = WorktreeDb(database_file=paths.database_file, project_id=paths.project_id)
    Artifacts(paths, db=db.artifacts).upload(session_id, name, "dist/*.whl", sandbox_path=sandbox_path)


class ArtifactsListCliIntegrationTests:
    """Typer runner integration tests for wt artifacts list."""

    def test_artifacts_list_cli_empty_workspace_prints_no_artifacts_found(
        self, cli_runner: CliRunner, artifacts_workspace: Path
    ) -> None:
        """wt artifacts list: no artifacts published yet -> exit 0, 'No artifacts found.' in stdout."""
        result = cli_runner.invoke(app, ["-p", str(artifacts_workspace), "artifacts", "list"])

        assert result.exit_code == 0
        assert "No artifacts found." in result.stdout

    def test_artifacts_list_cli_session_flag_filters_rows(
        self, cli_runner: CliRunner, artifacts_workspace: Path
    ) -> None:
        """wt artifacts list --session <id>: only rows matching <id> appear in the rendered table."""
        _publish_artifact(artifacts_workspace, session_id="wf_one", name="dist-one")
        _publish_artifact(artifacts_workspace, session_id="wf_two", name="dist-two")

        result = cli_runner.invoke(app, ["-p", str(artifacts_workspace), "artifacts", "list", "--session", "wf_one"])

        assert result.exit_code == 0
        assert "dist-one" in result.stdout
        assert "dist-two" not in result.stdout

    def test_artifacts_list_cli_format_json_emits_wire_schema(
        self, cli_runner: CliRunner, artifacts_workspace: Path
    ) -> None:
        """wt artifacts list --format json: no artifacts published -> stdout equals the literal empty-list wire payload."""
        result = cli_runner.invoke(app, ["-p", str(artifacts_workspace), "artifacts", "list", "--format", "json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {
            "event_type": "ArtifactsListResult",
            "payload": {"status": "ok", "artifacts": [], "errors": [], "warnings": [], "fixes": []},
        }
