"""Tier 1 tests for the process-wide SIGINT/SIGTERM handlers, run in child processes that receive real signals."""

from __future__ import annotations

import concurrent.futures
import os
import signal
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal delivery required")

SIGNALLED_CHILD = """
import signal
from dovo.common.interrupt import install_interrupt_handlers, reraise_interrupt_signal

install_interrupt_handlers()
try:
    print("READY", flush=True)
    signal.pause()
except KeyboardInterrupt:
    print("INTERRUPTED", flush=True)
    reraise_interrupt_signal()
"""

UNSIGNALLED_CHILD = """
from dovo.common.interrupt import install_interrupt_handlers, reraise_interrupt_signal

install_interrupt_handlers()
reraise_interrupt_signal()
print("RETURNED", flush=True)
"""

DEFERRED_CHILD = """
import os
import signal
from dovo.common.interrupt import deferred_interrupts, install_interrupt_handlers, reraise_interrupt_signal

install_interrupt_handlers()
try:
    with deferred_interrupts():
        os.kill(os.getpid(), signal.SIGTERM)
        print("INSIDE", flush=True)
except KeyboardInterrupt:
    print("AFTER", flush=True)
    reraise_interrupt_signal()
"""

IGNORED_CHILD = """
import os
import signal
from dovo.common.interrupt import install_interrupt_handlers, reraise_interrupt_signal

signal.signal(signal.SIGTERM, signal.SIG_IGN)
install_interrupt_handlers()
os.kill(os.getpid(), signal.SIGTERM)
reraise_interrupt_signal()
print("IGNORED", flush=True)
"""


def _start(script: str) -> subprocess.Popen[str]:
    """Start script under the current interpreter with captured text stdout and stderr."""
    return subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _read_line(process: subprocess.Popen[str]) -> str:
    """Return the next stdout line of process, failing the test when none arrives within 10 s."""
    assert process.stdout is not None
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        return executor.submit(process.stdout.readline).result(timeout=10.0).strip()
    finally:
        # Waiting would block on the readline; the caller kills the child, which ends it
        executor.shutdown(wait=False, cancel_futures=True)


class InterruptHandlerTests:
    @pytest.mark.parametrize(
        "signum",
        [pytest.param(signal.SIGINT, id="sigint"), pytest.param(signal.SIGTERM, id="sigterm")],
    )
    def test_signal_raises_keyboard_interrupt_and_reraise_terminates_by_it(self, signum: int) -> None:
        """In a child that installed the handlers, printed READY and blocks in signal.pause(), print INTERRUPTED from its except KeyboardInterrupt, then call reraise_interrupt_signal(); the child's returncode is -signum."""
        process = _start(SIGNALLED_CHILD)
        try:
            assert _read_line(process) == "READY"

            os.kill(process.pid, signum)
            stdout, _ = process.communicate(timeout=10.0)

            assert stdout.splitlines() == ["INTERRUPTED"]
            assert process.returncode == -signum
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_reraise_returns_without_a_signal(self) -> None:
        """In a fresh child that installed the handlers and received no signal, reraise_interrupt_signal() returns and the child exits 0."""
        completed = subprocess.run(
            [sys.executable, "-c", UNSIGNALLED_CHILD], capture_output=True, text=True, timeout=30.0, check=False
        )

        assert (completed.returncode, completed.stdout.splitlines()) == (0, ["RETURNED"])

    def test_signal_ignored_at_install_stays_ignored(self) -> None:
        """A child that ignores SIGTERM before installing the handlers and then sends itself SIGTERM keeps running: reraise_interrupt_signal() returns and the child prints IGNORED and exits 0."""
        completed = subprocess.run(
            [sys.executable, "-c", IGNORED_CHILD], capture_output=True, text=True, timeout=30.0, check=False
        )

        assert (completed.returncode, completed.stdout.splitlines()) == (0, ["IGNORED"])

    def test_signal_inside_deferred_interrupts_raises_after_the_block(self) -> None:
        """A child that installed the handlers sends itself SIGTERM inside deferred_interrupts() and prints INSIDE after the kill; its except KeyboardInterrupt prints AFTER and calls reraise_interrupt_signal(); stdout is INSIDE then AFTER and the returncode is -SIGTERM."""
        completed = subprocess.run(
            [sys.executable, "-c", DEFERRED_CHILD], capture_output=True, text=True, timeout=30.0, check=False
        )

        assert (completed.returncode, completed.stdout.splitlines()) == (-signal.SIGTERM, ["INSIDE", "AFTER"])
