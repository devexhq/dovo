"""Tier 2 presentation contract tests for WorktreeCreateFormatter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dovo.cli.ui.formatters.worktree.worktree_create import WorktreeCreateFormatter
from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord
from dovo.common.models import Remediation
from dovo.core.worktree.models import (
    WorktreeCreateResult,
    WorktreeSession,
)
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

_SESSION = WorktreeSession(
    session_id="dovo_create123",
    target_branch="dovo/create123",
    worktree_path=Path("/tmp/dovo_create123"),
    base_commit="def5678",
    created_at="2026-08-31T20:00:00Z",
)

CREATED_OK = FormatterCase.identity(
    data=WorktreeCreateResult(session=_SESSION),
    render_expectations=[_SESSION.session_id, _SESSION.target_branch],
)

FAILED_GIT = FormatterCase.identity(
    data=WorktreeCreateResult(
        error_code="GIT_FAILED",
        errors=["Git checkout failed."],
        remediations=[Remediation(message="Check git status and branch name")],
    ),
    render_expectations=["Git checkout failed.", "Fix:", "- Check git status and branch name"],
)

CREATED_WITH_DIAGNOSTICS = FormatterCase.identity(
    data=WorktreeCreateResult(
        session=_SESSION,
        warnings=["Failed to persist worktree metadata to the local database: boom"],
        diagnostics=[
            DiagnosticRecord(
                event_id="exc_3f9a1c07b2de",
                log_path=Path("/home/me/.dovo/logs/2026-10-23/exc_3f9a1c07b2de.json"),
                level=DiagnosticLevel.ERROR,
            )
        ],
    ),
    render_expectations=[
        _SESSION.session_id,
        "Failed to persist worktree metadata to the local database: boom",
        "Details logged: /home/me/.dovo/logs/2026-10-23/exc_3f9a1c07b2de.json (event exc_3f9a1c07b2de)",
    ],
)

WORKTREE_CREATE_CASES = [
    pytest.param(CREATED_OK, id="created_ok"),
    pytest.param(FAILED_GIT, id="failed_git"),
    pytest.param(CREATED_WITH_DIAGNOSTICS, id="created_with_diagnostics"),
]

WORKTREE_CREATE_PAYLOAD_CASES = [
    pytest.param(
        CREATED_OK,
        {
            "session": {
                "session_id": "dovo_create123",
                "target_branch": "dovo/create123",
                "worktree_path": "/tmp/dovo_create123",
                "base_commit": "def5678",
                "name": None,
                "created_at": "2026-08-31T20:00:00Z",
                "command_passed": None,
                "wip_applied": False,
                "wip_paths": [],
            },
            "warnings": [],
            "errors": [],
            "error_code": None,
            "remediations": [],
            "diagnostics": [],
        },
        id="created_ok",
    ),
    pytest.param(
        FAILED_GIT,
        {
            "session": None,
            "warnings": [],
            "errors": ["Git checkout failed."],
            "error_code": "GIT_FAILED",
            "remediations": [{"message": "Check git status and branch name", "command": None}],
            "diagnostics": [],
        },
        id="failed_git",
    ),
]


class WorktreeCreateFormatterTests:
    """Tier 2 presentation contract tests for WorktreeCreateFormatter."""

    @pytest.mark.parametrize(("case", "expected_payload"), WORKTREE_CREATE_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[WorktreeCreateResult, WorktreeCreateResult],
        expected_payload: dict[str, Any],
    ) -> None:
        assert_json_payload_matches_published_shape(WorktreeCreateFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", WORKTREE_CREATE_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[WorktreeCreateResult, WorktreeCreateResult]
    ) -> None:
        assert_rich_render_shows_every_view_value(WorktreeCreateFormatter, case.data, case.render_expectations)
