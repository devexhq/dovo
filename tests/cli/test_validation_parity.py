"""Parity: `dovo run` and `dovo resume` report a blueprint's validation exactly as `dovo blueprint validate` does."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.db import DovoDb
from dovo.engine.writer import snapshot_blueprint_path
from tests.harness.run_result import run_result_payload
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
    pytest.param(
        [
            {
                "id": "fix",
                "type": "loop",
                "until": ["steps.check.exit_code =="],
                "do": [{"id": "check", "run": "true"}],
            }
        ],
        id="until_syntax_error",
    ),
    pytest.param(
        [{"id": "a", "run": "true"}, {"id": "b", "run": "true", "when": ["steps.a.status == inputs.mode"]}],
        id="when_inputs_root",
    ),
    pytest.param([{"id": "a", "run": "echo {{ a b }} ${{ c == }}"}], id="placeholder_syntax_error"),
    pytest.param(
        [
            {"id": "a", "run": "echo {{ lenght(inputs.x) }}"},
            {"id": "b", "run": "true", "when": ["contains(steps.a.stdout)"]},
        ],
        id="function_errors",
    ),
    pytest.param(
        [
            {"id": "a", "run": "true", "artifacts": [{"name": "plan", "path": "plan.json"}]},
            {"id": "b", "run": "true", "when": ["artifacts.verdict.revision >= 1"]},
        ],
        id="unpublished_artifact",
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


# The codes a failed validation reports; any other failure happens after validation passed.
_VALIDATION_CODES = frozenset({"CATALOG_FILE_UNREADABLE", "CATALOG_SYNTAX_INVALID", "CATALOG_SCHEMA_INVALID"})


def _command_report(cli_runner: CliRunner, workspace: Path, args: list[str]) -> dict[str, object]:
    """Return the validation a run or resume reports: its run result's warnings and, when validation failed, its failure."""
    result = cli_runner.invoke(app, ["-p", str(workspace), *args, "--no-tty", "--format", "json"])
    payload = run_result_payload(result.stdout)
    if payload["error_code"] not in _VALIDATION_CODES:
        return {"error_code": None, "errors": [], "warnings": payload["warnings"], "remediations": []}
    return {
        "error_code": payload["error_code"],
        "errors": payload["errors"],
        "warnings": payload["warnings"],
        "remediations": payload["remediations"],
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
