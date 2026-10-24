"""Single-tier CLI integration tests for dovo worktree show."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.models import WorktreeShowResult, WorktreeShowStatus
from dovo.core.worktree.worktree import Worktree
from tests.harness.fakes import fixed_clock, fixed_new_id
from tests.harness.workspace_paths import initialized_workspace_paths


class WorktreeShowCliIntegrationTests:
    """Typer runner integration tests for dovo worktree show."""

    def test_worktree_show_cli_existing_worktree_exits_zero(
        self, cli_runner: CliRunner, cli_workspace: Path, dispatch_spy: list[object]
    ) -> None:
        create_result = Worktree(
            paths=initialized_workspace_paths(cli_workspace), git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id
        ).create(name="show-me")
        assert create_result.session is not None
        session = create_result.session

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "worktree", "show", session.session_id])

        assert result.exit_code == 0
        assert session.session_id in result.stdout
        assert len(dispatch_spy) == 1
        result_dto = dispatch_spy[0]
        assert isinstance(result_dto, WorktreeShowResult)
        assert result_dto.status == WorktreeShowStatus.OK
        assert result_dto.worktree is not None
        assert result_dto.worktree.id == session.session_id
        assert result_dto.worktree.name == "show-me"

    def test_worktree_show_cli_missing_worktree_exits_one(
        self, cli_runner: CliRunner, cli_workspace: Path, dispatch_spy: list[object]
    ) -> None:
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "worktree", "show", "missing-id"])

        assert result.exit_code == 1
        assert "not found" in result.stdout
        assert len(dispatch_spy) == 1
        result_dto = dispatch_spy[0]
        assert isinstance(result_dto, WorktreeShowResult)
        assert result_dto.status == WorktreeShowStatus.NOT_FOUND
        assert result_dto.errors == ["Worktree 'missing-id' not found."]

    def test_worktree_show_cli_renders_json(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        create_result = Worktree(
            paths=initialized_workspace_paths(cli_workspace), git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id
        ).create(name="show-me")
        assert create_result.session is not None
        session = create_result.session

        result = cli_runner.invoke(
            app,
            ["-p", str(cli_workspace), "worktree", "show", session.session_id, "--format", "json"],
        )

        assert result.exit_code == 0
        event = json.loads(result.stdout)
        assert event["event_type"] == "WorktreeShowResult"
        assert event["payload"]["status"] == "ok"
        assert event["payload"]["worktree"]["id"] == session.session_id
