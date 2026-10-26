"""End-to-end tests for advisory file locking, concurrency controls, and lock timeouts."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path

import pytest

from e2e.conftest import DovoRunner


@pytest.mark.e2e
@pytest.mark.fixture
class AdvisoryLockingCliTests:
    """E2E tests for cross-process advisory locking and contention feedback."""

    def test_external_lock_causes_lock_wait_feedback_and_timeout_exit_1(
        self,
        run_dovo: DovoRunner,
        initialized_project: Path,
    ) -> None:
        """A mutating command blocked by an externally held workspace.lock reports the wait, then times out."""
        lock_file = initialized_project / ".dovo" / "workspace.lock"
        file_descriptor = os.open(lock_file, os.O_CREAT | os.O_RDWR)
        fcntl.flock(file_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

        try:
            result = run_dovo(
                ["worktree", "prune"],
                cwd=initialized_project,
                env={"DOVO_LOCK_TIMEOUT_SECONDS": "0.5"},
            )

            assert result.exit_code == 1
            assert "Lock Held" in result.stdout
            assert "Workspace lock is currently held by another process" in result.stdout
            assert "workspace.lock" in result.stdout
            assert "Timed out waiting for workspace lock" in result.stdout
        finally:
            fcntl.flock(file_descriptor, fcntl.LOCK_UN)
            os.close(file_descriptor)

    @pytest.mark.parametrize(
        ("argv", "expected_stdout"),
        [
            pytest.param(
                ["worktree", "create"],
                ["Workspace Lock Timeout", "Timed out waiting for workspace lock", "Lock Held"],
                id="worktree-create",
            ),
            pytest.param(
                ["blueprint", "create", "--name", "locked-bp"],
                ["Workspace Lock Timeout", "Timed out waiting for workspace lock"],
                id="blueprint-create",
            ),
            pytest.param(
                ["step", "create", "--name", "locked-step"],
                ["Workspace Lock Timeout", "Timed out waiting for workspace lock"],
                id="step-create",
            ),
        ],
    )
    def test_escaping_lock_timeout_renders_panel_without_traceback_and_exits_1(
        self, run_dovo: DovoRunner, initialized_project: Path, argv: list[str], expected_stdout: list[str]
    ) -> None:
        """Print the Workspace Lock Timeout panel on stdout, no Traceback on stderr, and exit 1."""
        lock_file = initialized_project / ".dovo" / "workspace.lock"
        file_descriptor = os.open(lock_file, os.O_CREAT | os.O_RDWR)
        fcntl.flock(file_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

        try:
            result = run_dovo(argv, cwd=initialized_project, env={"DOVO_LOCK_TIMEOUT_SECONDS": "0.5"})

            assert result.exit_code == 1
            assert [fragment for fragment in expected_stdout if fragment not in result.stdout] == []
            assert "Traceback" not in result.stderr
        finally:
            fcntl.flock(file_descriptor, fcntl.LOCK_UN)
            os.close(file_descriptor)
