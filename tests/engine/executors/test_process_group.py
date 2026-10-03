from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from dovo.engine.executors.models import StepExecutionContext
from dovo.engine.executors.step_executor import StepExecution
from tests.harness.builders import StepBuilder

pytestmark = pytest.mark.slow


def _wait_pid_dead(pid: int, timeout: float = 3.0) -> bool:
    """Poll until the process with the given PID is no longer alive."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def _sync_and_interrupt(pid_dir: Path, timeout: float = 10.0) -> None:
    """Wait for child and grandchild PID files to appear, then raise KeyboardInterrupt."""
    child_pid_file = pid_dir / "child.pid"
    grandchild_pid_file = pid_dir / "grandchild.pid"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if child_pid_file.exists() and grandchild_pid_file.exists():
            break
        time.sleep(0.05)
    raise KeyboardInterrupt


@pytest.mark.slow
class ProcessGroupEscalationTests:
    """Integration tests verifying process group isolation and escalation to SIGKILL."""

    def test_timeout_escalates_from_sigterm_to_sigkill(self, tmp_path: Path) -> None:
        """Verify child process ignoring SIGTERM is escalated to SIGKILL after grace period."""
        if sys.platform == "win32":
            pytest.skip("POSIX process groups and signal trapping required")

        script = (
            "import os, signal, time, pathlib; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "pathlib.Path('child.pid').write_text(str(os.getpid())); "
            "time.sleep(10)"
        )
        cmd = f'{sys.executable} -c "{script}"'
        step = StepBuilder.command(cmd).with_id("timeout-escalate").with_timeout(1).build()

        start_time = time.monotonic()
        execution = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path))
        result = execution.run()
        elapsed = time.monotonic() - start_time

        assert result.step_id == "timeout-escalate"
        assert result.status == "failed"
        assert result.exit_code == 124
        assert result.stdout == ""
        assert result.stderr == ""
        assert result.attempts == 1
        assert result.error_message == "Command step execution timed out after 1 seconds."
        assert result.errors == []
        assert result.warnings == []
        assert result.fixes == []
        assert result.duration_seconds >= 2.0
        assert elapsed >= 2.0

        child_pid_file = tmp_path / "child.pid"
        assert child_pid_file.exists()
        child_pid = int(child_pid_file.read_text().strip())

        assert _wait_pid_dead(child_pid) is True

    def test_keyboard_interrupt_terminates_child_process_tree(self, tmp_path: Path) -> None:
        """Verify KeyboardInterrupt cleans up child and grandchild processes in the process group."""
        if sys.platform == "win32":
            pytest.skip("POSIX process groups and signal trapping required")

        script_content = """import os
import subprocess
import sys
import time
from pathlib import Path

Path("child.pid").write_text(str(os.getpid()))
subprocess.Popen([
    sys.executable,
    "-c",
    "import os, time, pathlib; pathlib.Path('grandchild.pid').write_text(str(os.getpid())); time.sleep(30)",
])
time.sleep(30)
"""
        (tmp_path / "tree_script.py").write_text(script_content)
        cmd = f"{sys.executable} tree_script.py"
        step = StepBuilder.command(cmd).with_id("interrupt-tree").with_timeout(30).build()
        execution = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path))

        def mock_wait(self_proc: subprocess.Popen[str], *args: object, **kwargs: object) -> int:
            _sync_and_interrupt(tmp_path)
            return 0

        with patch.object(subprocess.Popen, "wait", mock_wait):
            with pytest.raises(KeyboardInterrupt):
                execution.run()

        child_pid_file = tmp_path / "child.pid"
        grandchild_pid_file = tmp_path / "grandchild.pid"
        assert child_pid_file.exists()
        assert grandchild_pid_file.exists()
        child_pid = int(child_pid_file.read_text().strip())
        grandchild_pid = int(grandchild_pid_file.read_text().strip())

        assert _wait_pid_dead(child_pid) is True
        assert _wait_pid_dead(grandchild_pid) is True
