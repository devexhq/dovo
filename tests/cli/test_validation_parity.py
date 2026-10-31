"""Parity: `dovo run` and `dovo resume` report a blueprint's validation exactly as `dovo blueprint validate` does."""

from __future__ import annotations

import json
from itertools import takewhile
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.db import DovoDb
from dovo.engine.writer import snapshot_blueprint_path
from tests.harness.sessions import seed_paused_session
from tests.harness.workspace_paths import initialized_workspace_paths

_KEY = "deploy"

FIXTURES = [
    pytest.param([{"id": "a", "run": "echo one"}, {"id": "a", "run": "echo two"}], id="duplicate_step_ids"),
    pytest.param([{"id": "build", "type": "script", "script_path": "../build.sh"}], id="unsafe_script_path"),
    pytest.param(
        [
            {
                "id": "phases",
                "type": "loop",
                "until": ["steps.check.exit_code == 0"],
                "for_each": {"artifact": "plan"},
                "do": [{"id": "check", "run": "true"}],
            }
        ],
        id="unknown_loop_key",
    ),
    pytest.param([{"id": "a", "run": "true"}, {"id": "b", "type": "agent"}], id="agent_without_prompt"),
    pytest.param(
        [
            {"id": "build", "type": "script", "script_path": "scripts/build.sh"},
            {"id": "build", "run": "true", "env": {"GHOST": "${{ inputs.ghost }}"}},
        ],
        id="errors_with_warnings",
    ),
    pytest.param(
        [{"id": "build", "type": "script", "script_path": "scripts/build.sh"}],
        id="missing_script_warning",
    ),
]


def _seed(workspace: Path, steps: list[dict[str, object]]) -> None:
    """Pause a run of catalog blueprint `deploy`, then give the blueprint and the run's snapshot the same steps."""
    paths = initialized_workspace_paths(workspace)
    sessions = DovoDb(database_file=paths.database_file, project_id=paths.project_id).sessions
    seed_paused_session(
        paths,
        sessions,
        session_id=_KEY,
        steps=[{"id": "s1", "run": "true"}, {"id": "s2", "run": "true", "on_failure": "continue"}],
        paused_step_id="s2",
    )
    content = yaml.safe_dump({"name": _KEY, "steps": steps}, sort_keys=False)
    (workspace / ".dovo" / "catalog" / "blueprints" / f"{_KEY}.yml").write_text(content, encoding="utf-8")
    snapshot_blueprint_path(paths.session_dir(_KEY), _KEY).write_text(content, encoding="utf-8")


def _validate_report(cli_runner: CliRunner, workspace: Path) -> dict[str, object]:
    """Return the error code, errors, warnings and remediations `dovo blueprint validate deploy` reports."""
    result = cli_runner.invoke(app, ["-p", str(workspace), "blueprint", "validate", _KEY, "--format", "json"])
    payload = json.loads(result.stdout)["payload"]
    return {key: payload[key] for key in ("error_code", "errors", "warnings", "remediations")}


def _command_report(cli_runner: CliRunner, workspace: Path, args: list[str]) -> dict[str, object]:
    """Return the validation a run or resume reports: the warnings before any step, and its Blueprint Invalid event."""
    result = cli_runner.invoke(app, ["-p", str(workspace), *args, "--no-tty", "--format", "json"])
    events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    before_steps = list(takewhile(lambda event: event["event_type"] != "StepStartEvent", events))
    warnings = [event["payload"]["message"] for event in before_steps if event["event_type"] == "WarningEvent"]
    invalid = [event["payload"] for event in before_steps if event["event_type"] == "BlueprintInvalidEvent"]
    report = invalid[0] if invalid else {"error_code": None, "errors": [], "remediations": []}
    return {
        "error_code": report["error_code"],
        "errors": report["errors"],
        "remediations": report["remediations"],
        "warnings": warnings,
    }


class ValidationParityTests:
    """`dovo blueprint validate`, `dovo run` and `dovo resume` report the same validation for the same blueprint."""

    @pytest.mark.parametrize("steps", FIXTURES)
    def test_validate_run_and_resume_report_identical_findings(
        self, cli_runner: CliRunner, cli_workspace: Path, steps: list[dict[str, object]]
    ) -> None:
        _seed(cli_workspace, steps)

        validated = _validate_report(cli_runner, cli_workspace)
        resumed = _command_report(cli_runner, cli_workspace, ["session", "resume", _KEY])
        run = _command_report(cli_runner, cli_workspace, ["run", _KEY, "--no-worktree"])

        assert validated["errors"] or validated["warnings"]
        assert run == validated
        assert resumed == validated
