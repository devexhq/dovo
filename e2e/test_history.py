"""End-to-end tests for session history, diffs, log streaming, and secret redaction."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from e2e.conftest import DovoRunner


def _write_blueprint(project: Path, name: str, content: str, *, runner: DovoRunner | None = None) -> Path:
    """Write a blueprint YAML definition into the project repository catalog and commit it."""
    blueprint_file = project / ".dovo" / "catalog" / "blueprints" / f"{name}.yml"
    blueprint_file.parent.mkdir(parents=True, exist_ok=True)
    blueprint_file.write_text(content, encoding="utf-8")
    if runner is not None:
        runner(["blueprint", "list"], cwd=project)
    subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", f"chore: add blueprint {name}"], cwd=project, check=True, capture_output=True
    )
    return blueprint_file


def _parse_json(stdout: str) -> dict[str, object]:
    """Extract and parse first JSON payload from command output."""
    for line in stdout.splitlines():
        trimmed = line.strip()
        if trimmed.startswith("{") and trimmed.endswith("}"):
            try:
                data = json.loads(trimmed)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
    return json.loads(stdout)


@pytest.mark.e2e
@pytest.mark.fixture
class SessionsAndHistoryCliTests:
    """E2E tests for session history queries, diff inspection, log streaming, and secret redaction."""

    def test_history_list_displays_completed_sessions_with_filters(
        self,
        run_dovo: DovoRunner,
        initialized_project: Path,
    ) -> None:
        """Scenario: dovo history list displays recorded sessions and supports filtering by status and limit.

        Given an initialized workspace with multiple executed sessions across different statuses
        When dovo history list is executed with --status completed and --limit 1
        Then only completed sessions are returned up to the specified limit
        """
        _write_blueprint(
            initialized_project,
            "success-bp",
            """version: "1.0"
name: success-bp
id: success-bp
steps:
  - id: step-ok
    run: echo "ok"
""",
            runner=run_dovo,
        )
        _write_blueprint(
            initialized_project,
            "failure-bp",
            """version: "1.0"
name: failure-bp
id: failure-bp
steps:
  - id: step-fail
    run: exit 1
""",
            runner=run_dovo,
        )

        run_dovo(["run", "success-bp", "--session-id", "sess-succ-1"], cwd=initialized_project)
        run_dovo(["run", "failure-bp", "--session-id", "sess-fail-1"], cwd=initialized_project)
        run_dovo(["run", "success-bp", "--session-id", "sess-succ-2"], cwd=initialized_project)

        result = run_dovo(
            ["history", "list", "--status", "completed", "--limit", "1", "--format", "json"],
            cwd=initialized_project,
        )
        assert result.exit_code == 0
        data = _parse_json(result.stdout)
        payload = data.get("payload", {})
        assert isinstance(payload, dict)
        sessions = payload.get("sessions", [])
        assert isinstance(sessions, list)
        assert len(sessions) == 1
        assert sessions[0].get("status") == "completed"
        assert sessions[0].get("session_id") == "sess-succ-2"

        terminal_result = run_dovo(["history", "list"], cwd=initialized_project)
        assert terminal_result.exit_code == 0
        assert "sess-succ-2" in terminal_result.stdout
        assert "sess-fail-1" in terminal_result.stdout

    def test_history_show_presents_execution_metadata_and_steps(
        self,
        run_dovo: DovoRunner,
        initialized_project: Path,
    ) -> None:
        """Scenario: dovo history show presents session execution metadata and step results.

        Given an executed session with recorded steps
        When dovo history show is executed for the session identifier
        Then session metadata including status, duration, and step execution details are presented
        """
        _write_blueprint(
            initialized_project,
            "metadata-bp",
            """version: "1.0"
name: metadata-bp
id: metadata-bp
steps:
  - id: step-first
    run: echo "first"
  - id: step-second
    run: echo "second"
""",
            runner=run_dovo,
        )

        session_id = "sess-show-meta"
        run_res = run_dovo(["run", "metadata-bp", "--session-id", session_id], cwd=initialized_project)
        assert run_res.exit_code == 0

        show_json = run_dovo(["history", "show", session_id, "--format", "json"], cwd=initialized_project)
        assert show_json.exit_code == 0
        data = _parse_json(show_json.stdout)
        payload = data.get("payload", {})
        assert isinstance(payload, dict)
        session = payload.get("session", {})
        assert isinstance(session, dict)
        assert session.get("session_id") == session_id
        assert session.get("blueprint_name") == "metadata-bp"
        assert session.get("status") == "completed"
        assert session.get("completed_at") is not None

        show_term = run_dovo(["history", "show", session_id], cwd=initialized_project)
        assert show_term.exit_code == 0
        assert f"Session Metadata: {session_id}" in show_term.stdout
        assert "completed" in show_term.stdout

    def test_diff_presents_latest_changeset_and_raw_format(
        self,
        run_dovo: DovoRunner,
        initialized_project: Path,
    ) -> None:
        """Scenario: dovo diff presents the latest session changeset, and --raw outputs plain unformatted diff.

        Given an executed session that modified files in the workspace
        When dovo diff and dovo diff <session_id> --raw are executed
        Then dovo diff presents the unified diff and --raw outputs the unformatted patch directly
        """
        _write_blueprint(
            initialized_project,
            "diff-sample-bp",
            """version: "1.0"
name: diff-sample-bp
id: diff-sample-bp
steps:
  - id: create-file-step
    run: echo "diff payload line" > sample_output.txt
""",
            runner=run_dovo,
        )

        session_id = "sess-diff-verify"
        run_res = run_dovo(["run", "diff-sample-bp", "--session-id", session_id], cwd=initialized_project)
        assert run_res.exit_code == 0

        diff_latest = run_dovo(["diff"], cwd=initialized_project)
        assert diff_latest.exit_code == 0
        assert "sample_output.txt" in diff_latest.stdout

        diff_raw = run_dovo(["diff", session_id, "--raw"], cwd=initialized_project)
        assert diff_raw.exit_code == 0
        assert "diff --git a/sample_output.txt b/sample_output.txt" in diff_raw.stdout
        assert "+diff payload line" in diff_raw.stdout

    def test_logs_displays_structured_event_timeline(
        self,
        run_dovo: DovoRunner,
        initialized_project: Path,
    ) -> None:
        """Scenario: dovo logs displays the structured session event timeline.

        Given an executed session with lifecycle and step events
        When dovo logs <session_id> is executed
        Then the structured timeline of session and step events is presented
        """
        _write_blueprint(
            initialized_project,
            "timeline-bp",
            """version: "1.0"
name: timeline-bp
id: timeline-bp
steps:
  - id: step-a
    run: echo "step-a output"
  - id: step-b
    run: echo "step-b output"
""",
            runner=run_dovo,
        )

        session_id = "sess-timeline-test"
        run_res = run_dovo(["run", "timeline-bp", "--session-id", session_id], cwd=initialized_project)
        assert run_res.exit_code == 0

        logs_res = run_dovo(["logs", session_id], cwd=initialized_project)
        assert logs_res.exit_code == 0
        assert "Time" in logs_res.stdout
        assert "Event" in logs_res.stdout
        assert "Details" in logs_res.stdout
        assert "session_started" in logs_res.stdout
        assert "step_start" in logs_res.stdout
        assert "step_done" in logs_res.stdout
        assert "session_completed" in logs_res.stdout

    def test_logs_stream_displays_step_output_with_tail(
        self,
        run_dovo: DovoRunner,
        initialized_project: Path,
    ) -> None:
        """Scenario: dovo logs with --step and --stream displays step attempt output with optional --tail filtering.

        Given a session whose step emitted multi-line stdout output
        When dovo logs is executed with --step, --stream stdout, and --tail
        Then the trailing lines of the specified stream are returned
        """
        _write_blueprint(
            initialized_project,
            "multiline-bp",
            """version: "1.0"
name: multiline-bp
id: multiline-bp
steps:
  - id: multi-step
    run: echo "line 1" && echo "line 2" && echo "line 3" && echo "line 4" && echo "line 5"
""",
            runner=run_dovo,
        )

        session_id = "sess-multiline-logs"
        run_res = run_dovo(["run", "multiline-bp", "--session-id", session_id], cwd=initialized_project)
        assert run_res.exit_code == 0

        logs_tail = run_dovo(
            ["logs", session_id, "--step", "multi-step", "--stream", "stdout", "--tail", "2"],
            cwd=initialized_project,
        )
        assert logs_tail.exit_code == 0
        assert "line 4" in logs_tail.stdout
        assert "line 5" in logs_tail.stdout
        assert "line 1" not in logs_tail.stdout
        assert "line 2" not in logs_tail.stdout

    def test_sensitive_variables_redacted_across_tools(
        self,
        run_dovo: DovoRunner,
        initialized_project: Path,
    ) -> None:
        """Scenario: Secrets and configured sensitive variables are masked as [REDACTED:<name>] across history, logs, and diff.

        Given a workspace configured with environment.sensitive_variables and a session operating on secret values
        When dovo logs, dovo diff, and dovo history show are inspected
        Then secret values are masked with [REDACTED:<name>] and never appear in plain text
        """
        secret_name = "MY_SPECIAL_API_TOKEN"
        secret_value = "super_secret_val_88319"
        env_with_secret = {"MY_SPECIAL_API_TOKEN": secret_value}

        cfg_res = run_dovo(
            ["config", "set", "environment.sensitive_variables", f'["{secret_name}"]'],
            cwd=initialized_project,
        )
        assert cfg_res.exit_code == 0

        _write_blueprint(
            initialized_project,
            "redact-check-bp",
            f"""version: "1.0"
name: redact-check-bp
id: redact-check-bp
steps:
  - id: secret-step
    run: echo "secret is ${secret_name}" > secret.txt && echo "log output contains ${secret_name}"
""",
            runner=run_dovo,
        )

        session_id = "sess-redact-inspect"
        run_res = run_dovo(
            ["run", "redact-check-bp", "--session-id", session_id],
            cwd=initialized_project,
            env=env_with_secret,
        )
        assert run_res.exit_code == 0

        expected_mask = f"[REDACTED:{secret_name}]"

        logs_res = run_dovo(
            ["logs", session_id, "--step", "secret-step", "--stream", "stdout"],
            cwd=initialized_project,
            env=env_with_secret,
        )
        assert logs_res.exit_code == 0
        assert expected_mask in logs_res.stdout
        assert secret_value not in logs_res.stdout

        diff_res = run_dovo(
            ["diff", session_id, "--raw"],
            cwd=initialized_project,
            env=env_with_secret,
        )
        assert diff_res.exit_code == 0
        assert expected_mask in diff_res.stdout
        assert secret_value not in diff_res.stdout

        show_res = run_dovo(
            ["history", "show", session_id, "--logs"],
            cwd=initialized_project,
            env=env_with_secret,
        )
        assert show_res.exit_code == 0
        assert secret_value not in show_res.stdout
