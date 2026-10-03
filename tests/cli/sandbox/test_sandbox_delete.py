"""Single-tier CLI integration tests for dovo sandbox delete."""

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
from dovo.core.sandbox.facade import Sandbox
from dovo.core.sandbox.models import SandboxDeleteStatus, SandboxSession


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(None))


def _create_sandbox(sandbox_workspace: Path) -> SandboxSession:
    """Create a fresh sandbox via the facade for one delete test."""
    create_result = Sandbox(paths=_paths_for(sandbox_workspace)).create(name="delete-me")
    assert create_result.session is not None
    return create_result.session


class SandboxDeleteCliIntegrationTests:
    """Typer runner integration tests for dovo sandbox delete."""

    def test_sandbox_delete_cli_declined_confirmation_exits_one(
        self, cli_runner: CliRunner, sandbox_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """dovo sandbox delete <id> with input='n\\n' exits 1, leaves the sandbox dir on disk, and dispatches the exact ABORTED DTO."""
        session = _create_sandbox(sandbox_workspace)

        result = cli_runner.invoke(
            app, ["-p", str(sandbox_workspace), "sandbox", "delete", session.session_id], input="n\n"
        )

        assert result.exit_code == 1
        assert "Aborted." in result.stdout
        assert session.sandbox_path.is_dir()
        assert len(dispatch_spy) == 1

        payload = dispatch_spy[0]
        assert payload.status == SandboxDeleteStatus.ABORTED
        assert payload.sandbox_id == session.session_id
        assert not payload.deleted
        assert payload.errors == ["Aborted."]
        assert payload.sandbox is not None
        assert payload.sandbox.id == session.session_id

    @pytest.mark.parametrize(
        ("extra_args", "invoke_input"),
        [
            pytest.param([], "y\n", id="confirmed-via-stdin"),
            pytest.param(["--force"], None, id="force-flag"),
        ],
    )
    def test_sandbox_delete_cli_confirmed_or_forced_deletes_exits_zero(
        self,
        cli_runner: CliRunner,
        sandbox_workspace: Path,
        dispatch_spy: list[Any],
        extra_args: list[str],
        invoke_input: str | None,
    ) -> None:
        """dovo sandbox delete <id>, confirmed via stdin or --force, exits 0, removes the sandbox dir, and dispatches the exact DELETED DTO."""
        session = _create_sandbox(sandbox_workspace)

        result = cli_runner.invoke(
            app,
            ["-p", str(sandbox_workspace), "sandbox", "delete", session.session_id, *extra_args],
            input=invoke_input,
        )

        assert result.exit_code == 0
        assert "Sandbox deleted:" in result.stdout
        assert not session.sandbox_path.exists()
        assert len(dispatch_spy) == 1

        payload = dispatch_spy[0]
        assert payload.status == SandboxDeleteStatus.DELETED
        assert payload.sandbox_id == session.session_id
        assert payload.deleted
        assert len(payload.errors) == 0
        assert payload.sandbox is not None
        assert payload.sandbox.id == session.session_id

    def test_sandbox_delete_cli_missing_sandbox_exits_one(
        self, cli_runner: CliRunner, sandbox_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """dovo sandbox delete missing-id --force exits 1, reports not found, and dispatches the exact NOT_FOUND DTO."""
        result = cli_runner.invoke(app, ["-p", str(sandbox_workspace), "sandbox", "delete", "missing-id", "--force"])

        assert result.exit_code == 1
        assert "not found" in result.stdout
        assert len(dispatch_spy) == 1

        payload = dispatch_spy[0]
        assert payload.status == SandboxDeleteStatus.NOT_FOUND
        assert payload.sandbox_id == "missing-id"
        assert not payload.deleted
        assert payload.sandbox is None
        assert payload.errors == ["Sandbox 'missing-id' not found."]

    def test_sandbox_delete_cli_renders_json(self, cli_runner: CliRunner, sandbox_workspace: Path) -> None:
        """dovo sandbox delete <id> --force --format json emits the 'deleted' SandboxDeleteResult envelope."""
        session = _create_sandbox(sandbox_workspace)

        result = cli_runner.invoke(
            app,
            [
                "-p",
                str(sandbox_workspace),
                "sandbox",
                "delete",
                session.session_id,
                "--force",
                "--format",
                "json",
            ],
        )

        assert result.exit_code == 0
        actual_json = json.loads(result.stdout)
        sandbox = actual_json["payload"]["sandbox"]
        for field in ("created_at", "updated_at", "project_id"):
            assert sandbox[field]
            sandbox[field] = "placeholder"

        assert actual_json == {
            "event_type": "SandboxDeleteResult",
            "payload": {
                "status": "deleted",
                "sandbox_id": session.session_id,
                "sandbox": {
                    "id": session.session_id,
                    "project_id": "placeholder",
                    "name": "delete-me",
                    "branch_name": session.target_branch,
                    "base_commit": session.base_commit,
                    "sandbox_path": str(session.sandbox_path),
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

    def test_sandbox_delete_cli_declined_confirmation_renders_json(
        self, cli_runner: CliRunner, sandbox_workspace: Path
    ) -> None:
        """dovo sandbox delete <id> --format json with input='n\\n' emits the 'aborted' SandboxDeleteResult envelope."""
        session = _create_sandbox(sandbox_workspace)

        result = cli_runner.invoke(
            app,
            ["-p", str(sandbox_workspace), "sandbox", "delete", session.session_id, "--format", "json"],
            input="n\n",
        )

        assert result.exit_code == 1
        json_line = result.stdout.strip().splitlines()[-1]
        actual_json = json.loads(json_line)
        sandbox = actual_json["payload"]["sandbox"]
        for field in ("created_at", "updated_at", "project_id"):
            assert sandbox[field]
            sandbox[field] = "placeholder"

        assert actual_json == {
            "event_type": "SandboxDeleteResult",
            "payload": {
                "status": "aborted",
                "sandbox_id": session.session_id,
                "sandbox": {
                    "id": session.session_id,
                    "project_id": "placeholder",
                    "name": "delete-me",
                    "branch_name": session.target_branch,
                    "base_commit": session.base_commit,
                    "sandbox_path": str(session.sandbox_path),
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

    def test_sandbox_delete_cli_missing_sandbox_renders_json(
        self, cli_runner: CliRunner, sandbox_workspace: Path
    ) -> None:
        """dovo sandbox delete missing-id --force --format json emits the 'not_found' SandboxDeleteResult envelope."""
        result = cli_runner.invoke(
            app,
            ["-p", str(sandbox_workspace), "sandbox", "delete", "missing-id", "--force", "--format", "json"],
        )

        assert result.exit_code == 1
        assert json.loads(result.stdout) == {
            "event_type": "SandboxDeleteResult",
            "payload": {
                "status": "not_found",
                "sandbox_id": "missing-id",
                "sandbox": None,
                "deleted": False,
                "error_code": None,
                "errors": ["Sandbox 'missing-id' not found."],
                "warnings": [],
                "fixes": ["Run `dovo sandbox list` to see known sandboxes"],
            },
        }
