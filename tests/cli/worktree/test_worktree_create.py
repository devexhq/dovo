"""Single-tier CLI integration tests for dovo worktree create."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.models import WorktreeCreateStatus


class WorktreeCreateCliIntegrationTests:
    """Typer runner integration tests for dovo worktree create."""

    def test_worktree_create_cli_creates_worktree_and_branch_exits_zero(
        self, cli_runner: CliRunner, worktree_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """dovo worktree create --name demo creates the worktree dir and branch, exits 0, and dispatches the exact WorktreeCreateResult DTO."""
        result = cli_runner.invoke(
            app,
            ["-p", str(worktree_workspace), "worktree", "create", "--name", "demo"],
        )

        assert result.exit_code == 0
        assert "Worktree created:" in result.stdout

        worktree_dirs = list((worktree_workspace / ".dovo" / "worktrees").iterdir())
        assert len(worktree_dirs) == 1
        session_id = worktree_dirs[0].name
        assert worktree_dirs[0].is_dir()

        branches = GitRunner.list_branches(worktree_workspace)
        assert f"dovo/{session_id}" in branches

        assert len(dispatch_spy) == 1
        payload = dispatch_spy[0]
        assert payload.status == WorktreeCreateStatus.OK
        assert payload.session is not None
        assert payload.session.session_id == session_id
        assert payload.session.target_branch == f"dovo/{session_id}"
        assert payload.session.name == "demo"
        assert payload.session.command_passed is None
        assert not payload.session.wip_applied
        assert len(payload.session.wip_paths) == 0
        assert len(payload.errors) == 0

    def test_worktree_create_cli_capacity_exceeded_exits_one(
        self, cli_runner: CliRunner, worktree_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """A 4th dovo worktree create beyond the default limit of 3 exits 1 and dispatches the exact CAPACITY_EXCEEDED DTO."""
        for _ in range(3):
            create_result = cli_runner.invoke(app, ["-p", str(worktree_workspace), "worktree", "create"])
            assert create_result.exit_code == 0

        result = cli_runner.invoke(app, ["-p", str(worktree_workspace), "worktree", "create"])

        assert result.exit_code == 1
        assert "Maximum active worktrees reached (3/3)." in result.stdout
        assert len(dispatch_spy) == 4
        payload = dispatch_spy[-1]
        assert payload.status == WorktreeCreateStatus.CAPACITY_EXCEEDED
        assert payload.session is None
        assert payload.errors == ["Maximum active worktrees reached (3/3)."]
        assert len(payload.fixes) > 0

    def test_worktree_create_cli_renders_json(self, cli_runner: CliRunner, worktree_workspace: Path) -> None:
        """dovo worktree create --name demo --format json emits a WorktreeCreateResult envelope."""
        result = cli_runner.invoke(
            app,
            ["-p", str(worktree_workspace), "worktree", "create", "--name", "demo", "--format", "json"],
        )

        assert result.exit_code == 0
        actual_json = json.loads(result.stdout)
        session = actual_json["payload"]["session"]
        for field in ("session_id", "target_branch", "worktree_path", "base_commit", "created_at"):
            assert session[field]
            session[field] = "placeholder"

        assert actual_json == {
            "event_type": "WorktreeCreateResult",
            "payload": {
                "status": "ok",
                "session": {
                    "session_id": "placeholder",
                    "target_branch": "placeholder",
                    "worktree_path": "placeholder",
                    "base_commit": "placeholder",
                    "name": "demo",
                    "created_at": "placeholder",
                    "command_passed": None,
                    "wip_applied": False,
                    "wip_paths": [],
                },
                "error_code": None,
                "errors": [],
                "warnings": [],
                "fixes": [],
            },
        }

    def test_worktree_create_cli_with_wip_flag_overlays_and_exits_zero(
        self, cli_runner: CliRunner, worktree_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """dovo worktree create --wip binds the flag and dispatches WorktreeCreateResult with wip_applied=True."""
        (worktree_workspace / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")

        result = cli_runner.invoke(
            app,
            ["-p", str(worktree_workspace), "worktree", "create", "--name", "wip-demo", "--wip"],
        )

        assert result.exit_code == 0
        assert len(dispatch_spy) == 1
        payload = dispatch_spy[0]
        assert payload.status == WorktreeCreateStatus.OK
        assert payload.session is not None
        assert payload.session.name == "wip-demo"
        assert payload.session.wip_applied is True
        assert payload.session.wip_paths == ["dirty.txt"]
        assert len(payload.errors) == 0

    def test_worktree_create_cli_with_base_ref_option_branches_from_specified_target(
        self, cli_runner: CliRunner, worktree_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """dovo worktree create --base-ref binds the option and creates a worktree branching from the specified ref."""
        (worktree_workspace / "first.txt").write_text("first commit\n", encoding="utf-8")
        GitRunner.add_all(worktree_workspace)
        GitRunner.commit(worktree_workspace, "Add first.txt")
        first_commit = GitRunner.rev_parse(worktree_workspace, rev="HEAD")

        (worktree_workspace / "second.txt").write_text("second commit\n", encoding="utf-8")
        GitRunner.add_all(worktree_workspace)
        GitRunner.commit(worktree_workspace, "Add second.txt")
        current_head = GitRunner.rev_parse(worktree_workspace, rev="HEAD")
        assert first_commit != current_head

        result = cli_runner.invoke(
            app,
            ["-p", str(worktree_workspace), "worktree", "create", "--name", "ref-demo", "--base-ref", first_commit],
        )

        assert result.exit_code == 0
        assert len(dispatch_spy) == 1
        payload = dispatch_spy[0]
        assert payload.status == WorktreeCreateStatus.OK
        assert payload.session is not None
        assert payload.session.base_commit == first_commit
        assert payload.session.name == "ref-demo"
        assert not payload.session.wip_applied
        assert len(payload.errors) == 0

        session = payload.session
        assert (session.worktree_path / "first.txt").read_text(encoding="utf-8") == "first commit\n"
        assert not (session.worktree_path / "second.txt").exists()
