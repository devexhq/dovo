"""Tier 2 presentation contract tests for WorktreeCreateFormatter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dovo.cli.ui.formatters.worktree.worktree_create import WorktreeCreateFormatter
from dovo.core.worktree.models import (
    WorktreeCreateResult,
    WorktreeCreateStatus,
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

CREATED_OK = FormatterCase(
    data=WorktreeCreateResult(status=WorktreeCreateStatus.OK, session=_SESSION),
    view=WorktreeCreateResult(status=WorktreeCreateStatus.OK, session=_SESSION),
    render_expectations=[_SESSION.session_id, _SESSION.target_branch],
)

FAILED_GIT = FormatterCase(
    data=WorktreeCreateResult(
        status=WorktreeCreateStatus.GIT_FAILED,
        errors=["Git checkout failed."],
        fixes=["Check git status and branch name"],
    ),
    view=WorktreeCreateResult(
        status=WorktreeCreateStatus.GIT_FAILED,
        errors=["Git checkout failed."],
        fixes=["Check git status and branch name"],
    ),
    render_expectations=["Git checkout failed.", "Check git status and branch name"],
)

WORKTREE_CREATE_CASES = [
    pytest.param(CREATED_OK, id="created_ok"),
    pytest.param(FAILED_GIT, id="failed_git"),
]

WORKTREE_CREATE_PAYLOAD_CASES = [
    pytest.param(
        CREATED_OK,
        {
            "status": "ok",
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
            "fixes": [],
        },
        id="created_ok",
    ),
    pytest.param(
        FAILED_GIT,
        {
            "status": "git_failed",
            "session": None,
            "warnings": [],
            "errors": ["Git checkout failed."],
            "error_code": None,
            "fixes": ["Check git status and branch name"],
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
        """Verify to_json_serializable matches the published wire format dict."""
        assert_json_payload_matches_published_shape(WorktreeCreateFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", WORKTREE_CREATE_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[WorktreeCreateResult, WorktreeCreateResult]
    ) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(WorktreeCreateFormatter, case.data, case.render_expectations)
