"""Single-tier CLI integration tests for dovo artifacts list."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.artifacts import Artifacts
from dovo.core.artifacts.models import ArtifactProvenance
from dovo.core.db import DovoDb
from tests.harness.workspace_paths import initialized_workspace_paths


def _publish_artifact(
    workspace: Path, *, session_id: str, name: str, provenance: ArtifactProvenance | None = None
) -> None:
    """Publish a real artifact bundle under session_id/name for CLI list/download fixtures."""
    worktree_path = workspace / "worktree-scratch" / session_id
    (worktree_path / "dist").mkdir(parents=True, exist_ok=True)
    (worktree_path / "dist" / "pkg.whl").write_bytes(b"package-bytes")
    paths = initialized_workspace_paths(workspace)
    db = DovoDb(database_file=paths.database_file, project_id=paths.project_id)
    Artifacts(paths, db=db.artifacts).publish(
        session_id,
        name,
        "dist/*.whl",
        worktree_path=worktree_path,
        provenance=provenance if provenance is not None else ArtifactProvenance(),
    )


class ArtifactsListCliIntegrationTests:
    """Typer runner integration tests for dovo artifacts list."""

    def test_artifacts_list_cli_empty_workspace_prints_no_artifacts_found(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "artifacts", "list"])

        assert result.exit_code == 0
        assert "No artifacts found." in result.stdout

    def test_artifacts_list_cli_session_flag_filters_rows(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        _publish_artifact(cli_workspace, session_id="wf_one", name="dist-one")
        _publish_artifact(cli_workspace, session_id="wf_two", name="dist-two")

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "artifacts", "list", "--session", "wf_one"])

        assert result.exit_code == 0
        assert "dist-one" in result.stdout
        assert "dist-two" not in result.stdout

    def test_artifacts_list_cli_format_json_emits_wire_schema(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "artifacts", "list", "--format", "json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {
            "event_type": "ArtifactsListResult",
            "payload": {"status": "ok", "artifacts": [], "errors": [], "warnings": [], "remediations": []},
        }

    def test_artifacts_list_cli_format_json_emits_one_row_per_revision_with_provenance(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        """Emit one JSON row per revision, newest first, with a 64-char digest, step_id, attempt and loop_iteration."""
        for iteration in (1, 2):
            _publish_artifact(
                cli_workspace,
                session_id="wf_one",
                name="plan",
                provenance=ArtifactProvenance(step_id="plan", attempt=1, loop_iteration=iteration),
            )

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "artifacts", "list", "--format", "json"])

        rows = json.loads(result.stdout)["payload"]["artifacts"]
        assert result.exit_code == 0
        assert [(row["revision"], row["step_id"], row["attempt"], row["loop_iteration"]) for row in rows] == [
            (2, "plan", 1, 2),
            (1, "plan", 1, 1),
        ]
        assert [len(row["digest"]) for row in rows] == [64, 64]
