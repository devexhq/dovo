"""Wiring test: command results reach `--format json` output with the six-field header first."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dovo.cli import app

HEADER_KEYS = ["ok", "error_code", "errors", "warnings", "remediations", "diagnostics"]


class ResultHeaderWiringTests:
    """Each command's JSON payload starts with the header, in the bound order, through the real CLI."""

    @pytest.mark.parametrize(
        "command",
        [
            pytest.param(["worktree", "list"], id="worktree_list"),
            pytest.param(["blueprint", "list"], id="blueprint_list"),
            pytest.param(["session", "list"], id="session_list"),
            pytest.param(["artifacts", "list"], id="artifacts_list"),
            pytest.param(["config", "show"], id="config_show"),
        ],
    )
    def test_command_json_payload_starts_with_header(
        self, cli_runner: CliRunner, cli_workspace: Path, command: list[str]
    ) -> None:
        """Emit ok true, then error_code, errors, warnings, remediations and diagnostics before the command keys."""
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), *command, "--format", "json"])

        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout.strip().splitlines()[-1])["payload"]
        assert list(payload)[: len(HEADER_KEYS)] == HEADER_KEYS
        assert payload["ok"] is True
        assert payload["error_code"] is None
