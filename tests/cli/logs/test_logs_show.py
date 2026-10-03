"""Single-tier CLI integration tests for dovo logs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.filesystem.models import RepositoryPaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.db import DovoDb, RunStatus
from dovo.core.logs import LogsShowResult, LogsShowStatus, RunLogEvent, RunLogEventType
from dovo.core.project.services.storage import resolve_workspace_paths

_EVENTS = [
    RunLogEvent(ts="2026-09-26T10:00:00+00:00", event=RunLogEventType.RUN_STARTED, session_id="sess-logs"),
    RunLogEvent(ts="2026-09-26T10:00:01+00:00", event=RunLogEventType.RUN_COMPLETED, status="completed"),
]


def _seed_session(workspace: Path) -> Path:
    """Persist a run record plus a run.log and build step captures for session 'sess-logs'."""
    paths = resolve_workspace_paths(RepositoryPaths.from_root(workspace), resolve_global_paths(None))
    DovoDb(database_file=paths.database_file, project_id=paths.project_id).runs.create(
        session_id="sess-logs", blueprint_name="bp", blueprint_key="bp", status=RunStatus.COMPLETED
    )
    session_log_dir = paths.logs_dir / "sess-logs"
    session_log_dir.mkdir(parents=True)
    (session_log_dir / "run.log").write_text("".join(e.model_dump_json() + "\n" for e in _EVENTS), encoding="utf-8")
    (session_log_dir / "01_build_attempt_1.stderr.log").write_text("first-attempt\n", encoding="utf-8")
    (session_log_dir / "01_build_attempt_2.stdout.log").write_text("stdout-line\n", encoding="utf-8")
    (session_log_dir / "01_build_attempt_2.stderr.log").write_text(
        "".join(f"err-{i}\n" for i in range(1, 8)), encoding="utf-8"
    )
    return session_log_dir


class LogsCliIntegrationTests:
    """Typer runner integration tests for dovo logs."""

    def test_logs_command_exit_0_prints_run_log_events_for_valid_session(
        self, cli_runner: CliRunner, logs_workspace: Path
    ) -> None:
        """dovo logs <session_id>: exit 0 and stdout renders every run.log event."""
        _seed_session(logs_workspace)

        result = cli_runner.invoke(app, ["-p", str(logs_workspace), "logs", "sess-logs"])

        assert result.exit_code == 0
        for event in _EVENTS:
            assert event.ts in result.stdout
            assert event.event.value in result.stdout

    def test_logs_command_step_attempt_stream_tail_options_bind_to_service_call(
        self, cli_runner: CliRunner, logs_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """dovo logs <session_id> --step build --attempt 2 --stream stderr --tail 5: the result holds attempt 2's last five stderr lines."""
        _seed_session(logs_workspace)

        result = cli_runner.invoke(
            app,
            [
                "-p",
                str(logs_workspace),
                "logs",
                "sess-logs",
                "--step",
                "build",
                "--attempt",
                "2",
                "--stream",
                "stderr",
                "--tail",
                "5",
            ],
        )

        assert result.exit_code == 0
        assert dispatch_spy == [
            LogsShowResult(
                status=LogsShowStatus.OK, session_id="sess-logs", lines=["err-3", "err-4", "err-5", "err-6", "err-7"]
            )
        ]

    def test_logs_command_unknown_session_exits_1_with_not_found_message(
        self, cli_runner: CliRunner, logs_workspace: Path
    ) -> None:
        """dovo logs ghost-session: exit 1 with the not-found message."""
        result = cli_runner.invoke(app, ["-p", str(logs_workspace), "logs", "ghost-session"])

        assert result.exit_code == 1
        assert "No logs found for session 'ghost-session'" in result.stdout

    def test_logs_command_format_json_matches_wire_schema(self, cli_runner: CliRunner, logs_workspace: Path) -> None:
        """dovo logs <session_id> --format json: stdout equals the literal LogsShowResult envelope."""
        _seed_session(logs_workspace)

        result = cli_runner.invoke(app, ["-p", str(logs_workspace), "logs", "sess-logs", "--format", "json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {
            "event_type": "LogsShowResult",
            "payload": {
                "status": "ok",
                "session_id": "sess-logs",
                "lines": [],
                "events": [
                    {
                        "ts": "2026-09-26T10:00:00+00:00",
                        "event": "run_started",
                        "session_id": "sess-logs",
                        "blueprint_key": None,
                        "step_index": None,
                        "step_id": None,
                        "step_name": None,
                        "attempt": None,
                        "status": None,
                        "exit_code": None,
                        "duration_seconds": None,
                        "loop_id": None,
                        "iteration": None,
                        "max_iterations": None,
                        "all_passed": None,
                        "next_iteration": None,
                        "conditions": None,
                    },
                    {
                        "ts": "2026-09-26T10:00:01+00:00",
                        "event": "run_completed",
                        "session_id": None,
                        "blueprint_key": None,
                        "step_index": None,
                        "step_id": None,
                        "step_name": None,
                        "attempt": None,
                        "status": "completed",
                        "exit_code": None,
                        "duration_seconds": None,
                        "loop_id": None,
                        "iteration": None,
                        "max_iterations": None,
                        "all_passed": None,
                        "next_iteration": None,
                        "conditions": None,
                    },
                ],
                "available_steps": [],
                "available_attempts": [],
                "errors": [],
                "warnings": [],
                "fixes": [],
            },
        }
