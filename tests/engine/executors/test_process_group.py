from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from dovo.common.process import terminate_process_tree
from dovo.engine.executors.models import StepExecutionContext
from dovo.engine.executors.step_executor import StepExecution
from tests.harness.builders import StepBuilder
from tests.harness.poll import wait_until

pytestmark = pytest.mark.slow


def _pid_dead(pid: int) -> bool:
    """Return True when no process with the given PID is alive."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def _sync_and_interrupt(pid_dir: Path, timeout: float = 10.0) -> None:
    """Wait for child and grandchild PID files to appear, then raise KeyboardInterrupt."""
    child_pid_file = pid_dir / "child.pid"
    grandchild_pid_file = pid_dir / "grandchild.pid"
    assert wait_until(lambda: child_pid_file.exists() and grandchild_pid_file.exists(), timeout=timeout), (
        "child and grandchild PID files did not appear before the timeout"
    )
    raise KeyboardInterrupt


@pytest.mark.slow
class ProcessGroupEscalationTests:
    """Integration tests verifying process group isolation and escalation to SIGKILL."""

    def test_timeout_escalates_from_sigterm_to_sigkill(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
        execution = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path, diagnostics=[]))
        monkeypatch.setattr(
            "dovo.engine.executors.step_executor.terminate_process_tree",
            lambda process, *, grace_seconds=2.0, pgid=None: terminate_process_tree(
                process, grace_seconds=0.2, pgid=pgid
            ),
        )
        result = execution.run()
        elapsed = time.monotonic() - start_time

        assert (result.status, result.exit_code) == ("failed", 124)
        assert result.error_message == "Command step execution timed out after 1 seconds."
        assert result.duration_seconds >= 1.0
        assert elapsed >= 1.0

        child_pid_file = tmp_path / "child.pid"
        assert child_pid_file.exists()
        child_pid = int(child_pid_file.read_text().strip())

        assert wait_until(lambda: _pid_dead(child_pid)) is True

    def test_keyboard_interrupt_terminates_child_process_tree(self, tmp_path: Path) -> None:
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
        execution = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path, diagnostics=[]))

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

        assert wait_until(lambda: _pid_dead(child_pid)) is True
        assert wait_until(lambda: _pid_dead(grandchild_pid)) is True
