"""Single-tier CLI integration tests for dovo worktree create."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.models import WorktreeCreateResult, WorktreeCreateStatus
from tests.harness.fakes import SequentialIds, use_fixed_clock_and_ids


class WorktreeCreateCliIntegrationTests:
    """Typer runner integration tests for dovo worktree create."""

    def test_worktree_create_cli_creates_worktree_and_branch_exits_zero(
        self,
        cli_runner: CliRunner,
        cli_workspace: Path,
        dispatch_spy: list[WorktreeCreateResult],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        use_fixed_clock_and_ids(monkeypatch)

        result = cli_runner.invoke(
            app,
            ["-p", str(cli_workspace), "worktree", "create", "--name", "demo"],
        )

        assert result.exit_code == 0
        assert "Worktree created:" in result.stdout

        worktree_dir = cli_workspace / ".dovo" / "worktrees" / "dovo_0000abcd"
        assert list((cli_workspace / ".dovo" / "worktrees").iterdir()) == [worktree_dir]
        assert worktree_dir.is_dir()
        assert "dovo/dovo_0000abcd" in GitRunner().list_branches(cli_workspace)

        assert len(dispatch_spy) == 1
        payload = dispatch_spy[0]
        assert payload.status == WorktreeCreateStatus.OK
        assert payload.session is not None
        assert payload.session.session_id == "dovo_0000abcd"
        assert payload.session.worktree_path == worktree_dir.resolve()
        assert payload.session.target_branch == "dovo/dovo_0000abcd"
        assert payload.session.name == "demo"

    def test_worktree_create_cli_capacity_exceeded_exits_one(
        self,
        cli_runner: CliRunner,
        cli_workspace: Path,
        dispatch_spy: list[WorktreeCreateResult],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        use_fixed_clock_and_ids(monkeypatch, new_id=SequentialIds("0000000a", "0000000b", "0000000c"))
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

    def test_worktree_create_cli_renders_json(
        self, cli_runner: CliRunner, cli_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        use_fixed_clock_and_ids(monkeypatch)

        result = cli_runner.invoke(
            app,
            ["-p", str(cli_workspace), "worktree", "create", "--name", "demo", "--format", "json"],
        )

        assert result.exit_code == 0
        event = json.loads(result.stdout)
        assert event["event_type"] == "WorktreeCreateResult"
        assert event["payload"]["status"] == "ok"
        assert event["payload"]["session"]["name"] == "demo"
        assert event["payload"]["session"]["session_id"] == "dovo_0000abcd"
        assert event["payload"]["session"]["created_at"] == "2026-01-02T03:04:05+00:00"
        assert (cli_workspace / ".dovo" / "worktrees" / "dovo_0000abcd").is_dir()
        assert "dovo/dovo_0000abcd" in GitRunner().list_branches(cli_workspace)

    def test_worktree_create_cli_colliding_id_renders_already_exists_json_exits_one(
        self, cli_runner: CliRunner, cli_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        use_fixed_clock_and_ids(monkeypatch)
        first = cli_runner.invoke(app, ["-p", str(cli_workspace), "worktree", "create"])
        assert first.exit_code == 0

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "worktree", "create", "--format", "json"])

        assert result.exit_code == 1
        payload = json.loads(result.stdout)["payload"]
        assert payload["status"] == "already_exists"
        assert payload["error_code"] == "WORKTREE_PATH_CONFLICT"
        assert payload["errors"] == ["Worktree 'dovo_0000abcd' already exists (WORKTREE_PATH_CONFLICT)."]
        assert (cli_workspace / ".dovo" / "worktrees" / "dovo_0000abcd").is_dir()
        assert "dovo/dovo_0000abcd" in GitRunner().list_branches(cli_workspace)

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
        GitRunner().run(["add", "-A"], path=cli_workspace)
        GitRunner().run(["commit", "-m", "Add first.txt"], path=cli_workspace)
        first_commit = GitRunner().rev_parse(cli_workspace, rev="HEAD")

        (cli_workspace / "second.txt").write_text("second commit\n", encoding="utf-8")
        GitRunner().run(["add", "-A"], path=cli_workspace)
        GitRunner().run(["commit", "-m", "Add second.txt"], path=cli_workspace)
        current_head = GitRunner().rev_parse(cli_workspace, rev="HEAD")
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
