"""Shared seed fixture for the session list, show and logs masking tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.common.filesystem import Filesystem
from dovo.core.db import SessionStatus
from tests.harness.workspace_paths import initialized_workspace_db, initialized_workspace_paths


@pytest.fixture
def seed_pin_session(cli_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[[list[str]], None]:
    """Seed FAILED session 'session-pin' whose error message and 'pin' step log hold DB_PIN's value '1234'.

    The returned callable takes the names to list under environment.sensitive_variables in config.json.
    """

    def _seed(sensitive_variables: list[str]) -> None:
        monkeypatch.setenv("DB_PIN", "1234")
        paths = initialized_workspace_paths(cli_workspace)
        sessions = initialized_workspace_db(cli_workspace).sessions
        sessions.create(session_id="session-pin", blueprint_name="task-a", blueprint_key="task-a")
        sessions.update_status("session-pin", SessionStatus.FAILED, error_message="1234")
        session_log_dir = paths.logs_dir / "session-pin"
        session_log_dir.mkdir(parents=True)
        (session_log_dir / "session.log").write_text("", encoding="utf-8")
        (session_log_dir / "02_pin_attempt_1.stdout.log").write_text("pin 1234\n", encoding="utf-8")
        config = json.loads(paths.config_file.read_text(encoding="utf-8"))
        config["environment"] = {"sensitive_variables": sensitive_variables}
        Filesystem.atomic_write_json(paths.config_file, config)

    return _seed
