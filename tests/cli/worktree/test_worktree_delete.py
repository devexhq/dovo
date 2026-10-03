"""Single-tier CLI integration tests for dovo worktree delete."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.filesystem.models import RepositoryPaths, WorkspacePaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.project.services.storage import resolve_workspace_paths
from dovo.core.worktree.facade import Worktree
from dovo.core.worktree.models import WorktreeDeleteStatus, WorktreeSession


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(None))


def _create_worktree(worktree_workspace: Path) -> WorktreeSession:
    """Create a fresh worktree via the facade for one delete test."""
    create_result = Worktree(paths=_paths_for(worktree_workspace)).create(name="delete-me")
    assert create_result.session is not None
    return create_result.session


class WorktreeDeleteCliIntegrationTests:
    """Typer runner integration tests for dovo worktree delete."""

    def test_worktree_delete_cli_declined_confirmation_exits_one(
        self, cli_runner: CliRunner, worktree_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """dovo worktree delete <id> with input='n\\n' exits 1, leaves the worktree dir on disk, and dispatches the exact ABORTED DTO."""
        session = _create_worktree(worktree_workspace)

        result = cli_runner.invoke(
            app, ["-p", str(worktree_workspace), "worktree", "delete", session.session_id], input="n\n"
        )

        assert result.exit_code == 1
        assert "Aborted." in result.stdout
        assert session.worktree_path.is_dir()
        assert len(dispatch_spy) == 1

        payload = dispatch_spy[0]
        assert payload.status == WorktreeDeleteStatus.ABORTED
        assert payload.worktree_id == session.session_id
        assert not payload.deleted
        assert payload.errors == ["Aborted."]
        assert payload.worktree is not None
        assert payload.worktree.id == session.session_id

    @pytest.mark.parametrize(
        ("extra_args", "invoke_input"),
        [
            pytest.param([], "y\n", id="confirmed-via-stdin"),
            pytest.param(["--force"], None, id="force-flag"),
        ],
    )
    def test_worktree_delete_cli_confirmed_or_forced_deletes_exits_zero(
        self,
        cli_runner: CliRunner,
        worktree_workspace: Path,
        dispatch_spy: list[Any],
        extra_args: list[str],
        invoke_input: str | None,
    ) -> None:
        """dovo worktree delete <id>, confirmed via stdin or --force, exits 0, removes the worktree dir, and dispatches the exact DELETED DTO."""
        session = _create_worktree(worktree_workspace)

        result = cli_runner.invoke(
            app,
            ["-p", str(worktree_workspace), "worktree", "delete", session.session_id, *extra_args],
            input=invoke_input,
        )

        assert result.exit_code == 0
        assert "Worktree deleted:" in result.stdout
        assert not session.worktree_path.exists()
        assert len(dispatch_spy) == 1

        payload = dispatch_spy[0]
        assert payload.status == WorktreeDeleteStatus.DELETED
        assert payload.worktree_id == session.session_id
        assert payload.deleted
        assert len(payload.errors) == 0
        assert payload.worktree is not None
        assert payload.worktree.id == session.session_id

    def test_worktree_delete_cli_missing_worktree_exits_one(
        self, cli_runner: CliRunner, worktree_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """dovo worktree delete missing-id --force exits 1, reports not found, and dispatches the exact NOT_FOUND DTO."""
        result = cli_runner.invoke(app, ["-p", str(worktree_workspace), "worktree", "delete", "missing-id", "--force"])

        assert result.exit_code == 1
        assert "not found" in result.stdout
        assert len(dispatch_spy) == 1

        payload = dispatch_spy[0]
        assert payload.status == WorktreeDeleteStatus.NOT_FOUND
        assert payload.worktree_id == "missing-id"
        assert not payload.deleted
        assert payload.worktree is None
        assert payload.errors == ["Worktree 'missing-id' not found."]

    def test_worktree_delete_cli_renders_json(self, cli_runner: CliRunner, worktree_workspace: Path) -> None:
        """dovo worktree delete <id> --force --format json emits the 'deleted' WorktreeDeleteResult envelope."""
        session = _create_worktree(worktree_workspace)

        result = cli_runner.invoke(
            app,
            [
                "-p",
                str(worktree_workspace),
                "worktree",
                "delete",
                session.session_id,
                "--force",
                "--format",
                "json",
            ],
        )

        assert result.exit_code == 0
        actual_json = json.loads(result.stdout)
        worktree = actual_json["payload"]["worktree"]
        for field in ("created_at", "updated_at", "project_id"):
            assert worktree[field]
            worktree[field] = "placeholder"

        assert actual_json == {
            "event_type": "WorktreeDeleteResult",
            "payload": {
                "status": "deleted",
                "worktree_id": session.session_id,
                "worktree": {
                    "id": session.session_id,
                    "project_id": "placeholder",
                    "name": "delete-me",
                    "branch_name": session.target_branch,
                    "base_commit": session.base_commit,
                    "worktree_path": str(session.worktree_path),
                    "status": "active",
                    "created_at": "placeholder",
                    "updated_at": "placeholder",
                },
                "deleted": True,
                "error_code": None,
                "errors": [],
                "warnings": [],
                "fixes": [],
            },
        }

    def test_worktree_delete_cli_declined_confirmation_renders_json(
        self, cli_runner: CliRunner, worktree_workspace: Path
    ) -> None:
        """dovo worktree delete <id> --format json with input='n\\n' emits the 'aborted' WorktreeDeleteResult envelope."""
        session = _create_worktree(worktree_workspace)

        result = cli_runner.invoke(
            app,
            ["-p", str(worktree_workspace), "worktree", "delete", session.session_id, "--format", "json"],
            input="n\n",
        )

        assert result.exit_code == 1
        json_line = result.stdout.strip().splitlines()[-1]
        actual_json = json.loads(json_line)
        worktree = actual_json["payload"]["worktree"]
        for field in ("created_at", "updated_at", "project_id"):
            assert worktree[field]
            worktree[field] = "placeholder"

        assert actual_json == {
            "event_type": "WorktreeDeleteResult",
            "payload": {
                "status": "aborted",
                "worktree_id": session.session_id,
                "worktree": {
                    "id": session.session_id,
                    "project_id": "placeholder",
                    "name": "delete-me",
                    "branch_name": session.target_branch,
                    "base_commit": session.base_commit,
                    "worktree_path": str(session.worktree_path),
                    "status": "active",
                    "created_at": "placeholder",
                    "updated_at": "placeholder",
                },
                "deleted": False,
                "error_code": None,
                "errors": ["Aborted."],
                "warnings": [],
                "fixes": [],
            },
        }

    def test_worktree_delete_cli_missing_worktree_renders_json(
        self, cli_runner: CliRunner, worktree_workspace: Path
    ) -> None:
        """dovo worktree delete missing-id --force --format json emits the 'not_found' WorktreeDeleteResult envelope."""
        result = cli_runner.invoke(
            app,
            ["-p", str(worktree_workspace), "worktree", "delete", "missing-id", "--force", "--format", "json"],
        )

        assert result.exit_code == 1
        assert json.loads(result.stdout) == {
            "event_type": "WorktreeDeleteResult",
            "payload": {
                "status": "not_found",
                "worktree_id": "missing-id",
                "worktree": None,
                "deleted": False,
                "error_code": None,
                "errors": ["Worktree 'missing-id' not found."],
                "warnings": [],
                "fixes": ["Run `dovo worktree list` to see known worktrees"],
            },
        }
