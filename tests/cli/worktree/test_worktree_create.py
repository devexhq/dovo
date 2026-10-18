"""Single-tier CLI integration tests for dovo worktree create."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.models import WorktreeCreateResult, WorktreeCreateStatus


class WorktreeCreateCliIntegrationTests:
    """Typer runner integration tests for dovo worktree create."""

    def test_worktree_create_cli_creates_worktree_and_branch_exits_zero(
        self, cli_runner: CliRunner, cli_workspace: Path, dispatch_spy: list[WorktreeCreateResult]
    ) -> None:
        result = cli_runner.invoke(
            app,
            ["-p", str(cli_workspace), "worktree", "create", "--name", "demo"],
        )

        assert result.exit_code == 0
        assert "Worktree created:" in result.stdout

        worktree_dirs = list((cli_workspace / ".dovo" / "worktrees").iterdir())
        assert len(worktree_dirs) == 1
        session_id = worktree_dirs[0].name
        assert worktree_dirs[0].is_dir()

        branches = GitRunner.list_branches(cli_workspace)
        assert f"dovo/{session_id}" in branches

        assert len(dispatch_spy) == 1
        payload = dispatch_spy[0]
        assert payload.status == WorktreeCreateStatus.OK
        assert payload.session is not None
        assert payload.session.session_id == session_id
        assert payload.session.name == "demo"

    def test_worktree_create_cli_capacity_exceeded_exits_one(
        self, cli_runner: CliRunner, cli_workspace: Path, dispatch_spy: list[WorktreeCreateResult]
    ) -> None:
        for _ in range(3):
            create_result = cli_runner.invoke(app, ["-p", str(cli_workspace), "worktree", "create"])
            assert create_result.exit_code == 0

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "worktree", "create"])

        assert result.exit_code == 1
        assert "Maximum active worktrees reached (3/3)." in result.stdout
        assert len(dispatch_spy) == 4
        payload = dispatch_spy[-1]
        assert payload.status == WorktreeCreateStatus.CAPACITY_EXCEEDED
        assert payload.errors == ["Maximum active worktrees reached (3/3)."]

    def test_worktree_create_cli_renders_json(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        result = cli_runner.invoke(
            app,
            ["-p", str(cli_workspace), "worktree", "create", "--name", "demo", "--format", "json"],
        )

        assert result.exit_code == 0
        event = json.loads(result.stdout)
        assert event["event_type"] == "WorktreeCreateResult"
        assert event["payload"]["status"] == "ok"
        assert event["payload"]["session"]["name"] == "demo"

    def test_worktree_create_cli_colliding_id_renders_already_exists_json_exits_one(
        self, cli_runner: CliRunner, cli_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("dovo.core.worktree.services.lifecycle.new_session_id", lambda _prefix: "dovo_collide1")
        first = cli_runner.invoke(app, ["-p", str(cli_workspace), "worktree", "create"])
        assert first.exit_code == 0

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "worktree", "create", "--format", "json"])

        assert result.exit_code == 1
        payload = json.loads(result.stdout)["payload"]
        assert payload["status"] == "already_exists"
        assert payload["error_code"] == "WORKTREE_PATH_CONFLICT"
        assert payload["errors"] == ["Worktree 'dovo_collide1' already exists (WORKTREE_PATH_CONFLICT)."]
        assert (cli_workspace / ".dovo" / "worktrees" / "dovo_collide1").is_dir()
        assert "dovo/dovo_collide1" in GitRunner.list_branches(cli_workspace)

    def test_worktree_create_cli_with_wip_flag_overlays_and_exits_zero(
        self, cli_runner: CliRunner, cli_workspace: Path, dispatch_spy: list[WorktreeCreateResult]
    ) -> None:
        (cli_workspace / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")

        result = cli_runner.invoke(
            app,
            ["-p", str(cli_workspace), "worktree", "create", "--name", "wip-demo", "--wip"],
        )

        assert result.exit_code == 0
        assert len(dispatch_spy) == 1
        payload = dispatch_spy[0]
        assert payload.status == WorktreeCreateStatus.OK
        assert payload.session is not None
        assert payload.session.wip_paths == ["dirty.txt"]

    def test_worktree_create_cli_with_base_ref_option_branches_from_specified_target(
        self, cli_runner: CliRunner, cli_workspace: Path, dispatch_spy: list[WorktreeCreateResult]
    ) -> None:
        (cli_workspace / "first.txt").write_text("first commit\n", encoding="utf-8")
        GitRunner.add_all(cli_workspace)
        GitRunner.commit(cli_workspace, "Add first.txt")
        first_commit = GitRunner.rev_parse(cli_workspace, rev="HEAD")

        (cli_workspace / "second.txt").write_text("second commit\n", encoding="utf-8")
        GitRunner.add_all(cli_workspace)
        GitRunner.commit(cli_workspace, "Add second.txt")
        current_head = GitRunner.rev_parse(cli_workspace, rev="HEAD")
        assert first_commit != current_head

        result = cli_runner.invoke(
            app,
            ["-p", str(cli_workspace), "worktree", "create", "--name", "ref-demo", "--base-ref", first_commit],
        )

        assert result.exit_code == 0
        assert len(dispatch_spy) == 1
        payload = dispatch_spy[0]
        assert payload.status == WorktreeCreateStatus.OK
        assert payload.session is not None
        assert payload.session.base_commit == first_commit

        session = payload.session
        assert (session.worktree_path / "first.txt").read_text(encoding="utf-8") == "first commit\n"
        assert not (session.worktree_path / "second.txt").exists()
