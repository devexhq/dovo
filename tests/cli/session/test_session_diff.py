"""Single-tier CLI integration tests for dovo session diff."""

from __future__ import annotations

import json
from pathlib import Path

import click
import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.redact import SecretRedactor
from dovo.core.db import DovoDb, SessionStatus
from dovo.engine.writer import get_session_dir, write_session_diff
from tests.harness.workspace_paths import initialized_workspace_paths

_PATCH_TEXT = (
    "diff --git a/file.txt b/file.txt\n"
    "index 111..222 100644\n"
    "--- a/file.txt\n"
    "+++ b/file.txt\n"
    "@@ -1 +1 @@\n"
    "-old line\n"
    "+new line\n"
)


_LONG_LINE_PATCH_TEXT = (
    f"diff --git a/wide.txt b/wide.txt\n--- a/wide.txt\n+++ b/wide.txt\n@@ -1 +1 @@\n-short\n+{'x' * 200}\n"
)


def _write_session_diff(cli_workspace: Path, session_id: str, patch_text: str = _PATCH_TEXT) -> Path:
    """Persist a session record and a real unified-diff patch for session_id in the workspace's session storage."""
    paths = initialized_workspace_paths(cli_workspace)
    DovoDb(database_file=paths.database_file, project_id=paths.project_id).sessions.create(
        session_id=session_id, blueprint_name="bp", blueprint_key="bp", status=SessionStatus.COMPLETED
    )
    return write_session_diff(get_session_dir(paths, session_id), patch_text, SecretRedactor([]))


class SessionDiffCliIntegrationTests:
    """Typer runner integration tests for dovo session diff."""

    def test_diff_cli_known_session_renders_patch_exits_zero(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        _write_session_diff(cli_workspace, "sess-diff-1")

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "diff", "sess-diff-1"])

        assert result.exit_code == 0
        assert "diff --git a/file.txt b/file.txt" in result.stdout
        assert "-old line" in result.stdout
        assert "+new line" in result.stdout

    def test_diff_cli_unknown_session_exits_one(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "diff", "unknown-session"])

        assert result.exit_code == 1
        assert "not found." in result.stdout
        assert "Run `dovo session list`" in result.stdout

    def test_diff_cli_json_emits_literal_wire_payload(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        patch_path = _write_session_diff(cli_workspace, "sess-diff-1")

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "session", "diff", "sess-diff-1", "--format", "json"]
        )

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {
            "event_type": "DiffResult",
            "payload": {
                "status": "ok",
                "session_id": "sess-diff-1",
                "artifact_path": str(patch_path),
                "relative_path": str(patch_path),
                "diff_text": _PATCH_TEXT,
                "full": False,
                "max_lines": 500,
                "total_lines": 7,
                "truncated": False,
                "truncated_lines": 0,
                "errors": [],
                "warnings": [],
                "fixes": [],
            },
        }

    def test_diff_cli_full_flag_binds_into_json_payload(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        _write_session_diff(cli_workspace, "sess-diff-1")

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "session", "diff", "sess-diff-1", "--full", "--format", "json"]
        )

        payload = json.loads(result.stdout)["payload"]
        assert result.exit_code == 0
        assert (payload["full"], payload["max_lines"]) == (True, 500)
        assert "raw" not in payload

    def test_diff_cli_unknown_session_json_payload_keeps_default_display_options(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "session", "diff", "unknown", "--full", "--format", "json"]
        )

        payload = json.loads(result.stdout)["payload"]
        assert result.exit_code == 1
        assert payload["status"] == "session_not_found"
        assert (payload["full"], payload["max_lines"]) == (False, None)


class SessionDiffLatestCliIntegrationTests:
    """Typer runner integration tests for dovo session diff with the session id omitted."""

    def test_diff_without_id_header_reads_latest(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        patch_path = _write_session_diff(cli_workspace, "sess-diff-1")

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "diff"])

        assert result.exit_code == 0
        assert result.stdout.lstrip().startswith("Session: sess-diff-1 (latest)")
        assert str(patch_path) not in result.stdout

    def test_diff_without_id_patch_and_json_omit_latest_line(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        _write_session_diff(cli_workspace, "sess-diff-1")

        patch = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "diff", "--patch"])
        wire = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "diff", "--format", "json"])

        assert patch.exit_code == 0
        assert patch.stdout == _PATCH_TEXT
        assert wire.exit_code == 0
        assert "latest" not in json.loads(wire.stdout)["payload"]
        assert "(latest)" not in patch.stdout + wire.stdout


class SessionDiffPatchCliIntegrationTests:
    """Typer runner integration tests for dovo session diff --patch."""

    @pytest.mark.parametrize(
        "command", [pytest.param(["session", "diff"], id="session"), pytest.param(["diff"], id="shortcut")]
    )
    def test_patch_writes_exact_patch_text(
        self, cli_runner: CliRunner, cli_workspace: Path, command: list[str]
    ) -> None:
        _write_session_diff(cli_workspace, "sess-diff-1", _LONG_LINE_PATCH_TEXT)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), *command, "sess-diff-1", "--patch"])

        assert result.exit_code == 0
        assert result.stdout == _LONG_LINE_PATCH_TEXT

    def test_patch_with_empty_patch_writes_nothing(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        _write_session_diff(cli_workspace, "sess-diff-1", "")

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "diff", "sess-diff-1", "--patch"])

        assert result.exit_code == 0
        assert result.stdout == ""

    def test_patch_with_unknown_session_reports_not_found(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        plain = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "diff", "unknown-session"])
        patch = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "diff", "unknown-session", "--patch"])

        assert (plain.exit_code, patch.exit_code) == (1, 1)
        assert patch.stdout == plain.stdout
        assert "Session Not Found" in patch.stdout

    def test_patch_with_format_json_is_a_usage_error(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        _write_session_diff(cli_workspace, "sess-diff-1")

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "diff", "sess-diff-1", "--patch", "--format", "json"]
        )

        assert result.exit_code == 2
        assert result.stdout == ""
        assert "--patch cannot be combined with --format json." in click.unstyle(result.stderr)
        assert "Invalid value" not in click.unstyle(result.stderr)

    def test_raw_flag_is_no_such_option(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "diff", "--raw"])

        assert result.exit_code == 2
        assert result.stdout == ""
        assert "No such option: --raw" in click.unstyle(result.stderr)
