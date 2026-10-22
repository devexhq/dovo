"""Tests for recording unexpected worktree failures in the diagnostic log."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dovo.common.filesystem.services import resolve_global_paths
from dovo.core.git.exceptions import GitCommandError, GitNotFoundError, GitPlumbingTimeoutError
from dovo.core.worktree.exceptions import WorktreeError
from dovo.core.worktree.services.failure_log import logged_failure, record_fallback
from tests.harness.diagnostic_log import FROZEN_EVENT_ID

_CONTEXT = {"worktree_id": "dovo_abc", "branch": "dovo/dovo_abc", "path": "/tmp/dovo_abc"}


class WorktreeFailureLogTests:
    @pytest.mark.parametrize(
        "exc",
        [
            pytest.param(GitCommandError(["git", "worktree", "add"], 128, "", "fatal"), id="git_command"),
            pytest.param(GitNotFoundError("git not found"), id="git_missing"),
            pytest.param(GitPlumbingTimeoutError("timed out"), id="git_timeout"),
            pytest.param(WorktreeError("worktree path does not exist"), id="worktree_error"),
        ],
    )
    def test_logged_failure_returns_message_unchanged_for_expected_errors(
        self, frozen_diagnostic_clock: Path, exc: Exception
    ) -> None:
        """Return "Git worktree operation failed: x" unchanged and leave <DOVO_HOME>/logs absent for each expected error."""
        message = logged_failure("Git worktree operation failed: x", exc, operation="worktree.create", context=_CONTEXT)

        assert message == "Git worktree operation failed: x"
        assert not resolve_global_paths(None).logs_dir.exists()

    def test_logged_failure_records_error_and_appends_hint_for_residual(self, frozen_diagnostic_clock: Path) -> None:
        """Return f"msg\nDetails logged: {frozen_diagnostic_clock} (event exc_3f9a1c07b2de)" for RuntimeError("boom") and write an error record with operation worktree.create and the given context."""
        message = logged_failure("msg", RuntimeError("boom"), operation="worktree.create", context=_CONTEXT)

        assert message == f"msg\nDetails logged: {frozen_diagnostic_clock} (event {FROZEN_EVENT_ID})"
        event = json.loads(frozen_diagnostic_clock.read_text(encoding="utf-8"))
        assert event["level"] == "error"
        assert event["operation"] == "worktree.create"
        assert event["context"] == _CONTEXT
        assert event["exception_type"] == "RuntimeError"
        assert event["exception_message"] == "boom"

    def test_logged_failure_returns_message_unchanged_when_recorder_cannot_write(
        self, frozen_diagnostic_clock: Path
    ) -> None:
        """Return the message unchanged for RuntimeError("boom") when <DOVO_HOME>/logs is a regular file."""
        logs_dir = resolve_global_paths(None).logs_dir
        logs_dir.parent.mkdir(parents=True, exist_ok=True)
        logs_dir.write_text("not a directory", encoding="utf-8")

        message = logged_failure("msg", RuntimeError("boom"), operation="worktree.create", context=_CONTEXT)

        assert message == "msg"
        assert logs_dir.read_text(encoding="utf-8") == "not a directory"

    def test_record_fallback_writes_debug_record(self, frozen_diagnostic_clock: Path) -> None:
        """Write one debug record with operation worktree.cleanup and exception_type GitCommandError for a GitCommandError."""
        exc = GitCommandError(["git", "worktree", "remove"], 128, "", "fatal: not a working tree")

        record_fallback(exc, operation="worktree.cleanup", context={"path": "/tmp/dovo_abc"})

        assert list(frozen_diagnostic_clock.parent.iterdir()) == [frozen_diagnostic_clock]
        event = json.loads(frozen_diagnostic_clock.read_text(encoding="utf-8"))
        assert event["level"] == "debug"
        assert event["operation"] == "worktree.cleanup"
        assert event["context"] == {"path": "/tmp/dovo_abc"}
        assert event["exception_type"] == "GitCommandError"

    def test_record_fallback_skips_missing_git(self, frozen_diagnostic_clock: Path) -> None:
        """Leave <DOVO_HOME>/logs absent for GitNotFoundError."""
        record_fallback(GitNotFoundError("git not found"), operation="worktree.cleanup", context={})

        assert not resolve_global_paths(None).logs_dir.exists()
