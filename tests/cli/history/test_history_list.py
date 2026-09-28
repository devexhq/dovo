"""Single-tier CLI integration tests for wt history / wt history list."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from worktree.cli import app
from worktree.common.filesystem.models import RepositoryPaths, WorkspacePaths
from worktree.common.filesystem.services.global_root import resolve_global_paths
from worktree.core.db import RunStatus, WorktreeDb
from worktree.core.project.services.storage import resolve_workspace_paths


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(None))


def _db_for(workspace: Path) -> WorktreeDb:
    """Construct a WorktreeDb bound to workspace's resolved database file and project id."""
    paths = _paths_for(workspace)
    return WorktreeDb(database_file=paths.database_file, project_id=paths.project_id)


class HistoryListCliIntegrationTests:
    """Typer runner integration tests for wt history / wt history list."""

    def test_history_cli_bare_lists_recent_runs_exits_zero(
        self, cli_runner: CliRunner, history_workspace: Path
    ) -> None:
        """wt history: bare invocation lists seeded COMPLETED and FAILED runs, exit 0, both session IDs in stdout."""
        db = _db_for(history_workspace)
        db.runs.create(
            session_id="session-completed", blueprint_name="task-a", blueprint_key="task-a", status=RunStatus.COMPLETED
        )
        db.runs.create(
            session_id="session-failed", blueprint_name="task-b", blueprint_key="task-b", status=RunStatus.FAILED
        )

        result = cli_runner.invoke(app, ["-p", str(history_workspace), "history"])

        assert result.exit_code == 0
        assert "session-completed" in result.stdout
        assert "session-failed" in result.stdout

    def test_history_list_cli_json_emits_literal_wire_payload(
        self, cli_runner: CliRunner, history_workspace: Path
    ) -> None:
        """wt history list --format json: stdout equals the literal HistoryListResult envelope for the two seeded runs."""
        db = _db_for(history_workspace)
        db.runs.create(
            session_id="session-completed", blueprint_name="task-a", blueprint_key="task-a", status=RunStatus.COMPLETED
        )
        db.runs.create(
            session_id="session-failed", blueprint_name="task-b", blueprint_key="task-b", status=RunStatus.FAILED
        )

        result = cli_runner.invoke(app, ["-p", str(history_workspace), "history", "list", "--format", "json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["event_type"] == "HistoryListResult"
        payload = data["payload"]
        runs = payload["runs"]
        assert len(runs) == 2
        for r in runs:
            assert isinstance(r["started_at"], str)
            r["started_at"] = "<timestamp>"
        assert payload == {
            "status": "ok",
            "runs": [
                {
                    "session_id": "session-failed",
                    "blueprint_name": "task-b",
                    "status": "failed",
                    "branch_name": None,
                    "started_at": "<timestamp>",
                    "completed_at": None,
                    "duration_seconds": None,
                    "error_message": None,
                },
                {
                    "session_id": "session-completed",
                    "blueprint_name": "task-a",
                    "status": "completed",
                    "branch_name": None,
                    "started_at": "<timestamp>",
                    "completed_at": None,
                    "duration_seconds": None,
                    "error_message": None,
                },
            ],
            "total_runs": 2,
            "errors": [],
            "warnings": [],
            "fixes": [],
        }
