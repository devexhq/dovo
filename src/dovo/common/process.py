"""Cross-platform subprocess process group isolation and cascading tree termination."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

DEFAULT_GRACE_PERIOD_SECONDS: float = 2.0


def get_isolated_process_kwargs() -> dict[str, Any]:
    """Return platform-specific kwargs for subprocess process group isolation."""
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        return {"creationflags": flags}
    return {"start_new_session": True}


def is_process_group_alive(pgid: int) -> bool:
    """Return True if at least one process in the POSIX process group is alive."""
    if not hasattr(os, "killpg"):
        return False
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True


def _poll_posix_group_exit(pgid: int, grace_seconds: float) -> bool:
    """Poll process group until all processes exit or grace period expires."""
    deadline = time.monotonic() + max(0.0, grace_seconds)
    while time.monotonic() < deadline:
        if not is_process_group_alive(pgid):
            return True
        time.sleep(0.05)
    return not is_process_group_alive(pgid)


def _terminate_posix_group(
    pid: int,
    pgid: int,
    proc: subprocess.Popen[Any] | None,
    grace_seconds: float,
) -> None:
    """Cascading SIGTERM -> grace period -> SIGKILL on POSIX process group."""
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass

    exited = _poll_posix_group_exit(pgid, grace_seconds)
    if not exited:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    if proc is not None:
        try:
            proc.kill()
            proc.wait(timeout=1.0)
        except Exception:
            pass


def _terminate_windows_tree(
    pid: int,
    proc: subprocess.Popen[Any] | None,
    grace_seconds: float,
) -> None:
    """Graceful termination with taskkill /T /F fallback on Windows."""
    if proc is not None:
        try:
            proc.terminate()
            proc.wait(timeout=max(0.1, grace_seconds))
            return
        except Exception:
            pass

    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            timeout=5.0,
            check=False,
        )
    except Exception:
        pass

    if proc is not None:
        try:
            proc.kill()
            proc.wait(timeout=1.0)
        except Exception:
            pass


def terminate_process_tree(
    proc: subprocess.Popen[Any] | int,
    *,
    grace_seconds: float = DEFAULT_GRACE_PERIOD_SECONDS,
    pgid: int | None = None,
) -> None:
    """Terminate or kill a process and its entire child process tree."""
    pid = proc.pid if isinstance(proc, subprocess.Popen) else proc
    popened = proc if isinstance(proc, subprocess.Popen) else None
    resolved_pgid = pgid if pgid is not None else pid

    if sys.platform != "win32" and hasattr(os, "killpg"):
        _terminate_posix_group(pid, resolved_pgid, popened, grace_seconds)
    else:
        _terminate_windows_tree(pid, popened, grace_seconds)


class ProcessRegistry:
    """Thread-safe registry tracking active subprocess.Popen instances."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._processes: dict[subprocess.Popen[Any], int] = {}

    def register(self, proc: subprocess.Popen[Any], *, pgid: int | None = None) -> None:
        """Register an active subprocess with optional explicit PGID."""
        resolved_pgid = pgid if pgid is not None else proc.pid
        with self._lock:
            self._processes[proc] = resolved_pgid

    def unregister(self, proc: subprocess.Popen[Any]) -> None:
        """Unregister a completed subprocess."""
        with self._lock:
            self._processes.pop(proc, None)

    def terminate_all(self, *, grace_seconds: float = 0.5) -> None:
        """Terminate all registered subprocesses and their process trees."""
        with self._lock:
            active = list(self._processes.items())
            self._processes.clear()

        for proc, pgid in active:
            try:
                terminate_process_tree(proc, grace_seconds=grace_seconds, pgid=pgid)
            except Exception:
                pass

    def __len__(self) -> int:
        with self._lock:
            return len(self._processes)


process_registry = ProcessRegistry()


def _cleanup_process_streams(proc: subprocess.Popen[Any]) -> None:
    """Close any unclosed stdout/stderr/stdin streams on the process."""
    for stream in (proc.stdout, proc.stderr, proc.stdin):
        if stream is not None and not stream.closed:
            try:
                stream.close()
            except Exception:
                pass


def run_isolated_process(
    cmd: Sequence[str] | str,
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    input_data: bytes | str | None = None,
    timeout_seconds: float | None = None,
    shell: bool = False,
    text: bool = False,
    grace_seconds: float = DEFAULT_GRACE_PERIOD_SECONDS,
) -> subprocess.CompletedProcess[Any]:
    """Execute a subprocess inside an isolated process group with cascading termination.

    Spawns the subprocess in a dedicated session/process group (via `start_new_session=True`
    on POSIX or `CREATE_NEW_PROCESS_GROUP` on Windows) and registers it in `ProcessRegistry`.
    If execution exceeds `timeout_seconds`, cascading termination is sent to the entire
    process tree (`SIGTERM` followed by `grace_seconds` before escalating to `SIGKILL`).
    Upon any unexpected exception or interrupt, active child processes are reaped before
    re-raising.

    Args:
        cmd: Command string or list of argument strings to execute.
        cwd: Optional working directory for the subprocess.
        env: Optional environment variables dictionary.
        input_data: Optional bytes or string input to pass to standard input.
        timeout_seconds: Subprocess execution timeout in seconds.
        shell: When True, executes the command through the system shell.
        text: When True, stdout and stderr streams are decoded as strings.
        grace_seconds: Grace period duration between SIGTERM and SIGKILL on timeout.

    Returns:
        A CompletedProcess instance containing stdout, stderr, and returncode.

    Raises:
        subprocess.TimeoutExpired: When execution exceeds `timeout_seconds`.
        FileNotFoundError: When the executable command cannot be found.
        OSError: When process spawning fails.
    """
    isolation_kwargs = get_isolated_process_kwargs()
    effective_cwd = str(cwd) if cwd is not None else None

    proc: subprocess.Popen[Any] = subprocess.Popen(
        cmd,
        cwd=effective_cwd,
        env=env,
        stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=shell,
        text=text,
        **isolation_kwargs,
    )
    pgid = proc.pid
    process_registry.register(proc, pgid=pgid)

    try:
        stdout_data, stderr_data = proc.communicate(input=input_data, timeout=timeout_seconds)
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=proc.returncode,
            stdout=stdout_data,
            stderr=stderr_data,
        )
    except subprocess.TimeoutExpired as exc:
        terminate_process_tree(proc, grace_seconds=grace_seconds, pgid=pgid)
        raise exc
    except BaseException:
        terminate_process_tree(proc, grace_seconds=0.5, pgid=pgid)
        raise
    finally:
        process_registry.unregister(proc)
        if proc.poll() is None:
            terminate_process_tree(proc, grace_seconds=0.5, pgid=pgid)
        _cleanup_process_streams(proc)
