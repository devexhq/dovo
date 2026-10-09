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
        """Scenario: An external process holding workspace lock causes concurrent invocation to emit lock wait feedback and exit 1 upon timeout.

        Given an initialized workspace with an external process holding .dovo/workspace.lock
        When a concurrent mutating dovo command is invoked
        Then lock waiting feedback is emitted and the command exits with code 1 upon timeout
        """
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
