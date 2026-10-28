"""Process-wide SIGINT/SIGTERM handling: raise KeyboardInterrupt, remember the signal, and end the process by it once the command has unwound."""

from __future__ import annotations

import os
import signal
import sys
from collections.abc import Generator
from contextlib import contextmanager
from types import FrameType

_INTERRUPT_SIGNALS = (signal.SIGINT, signal.SIGTERM)
_received_signal: int | None = None


def install_interrupt_handlers() -> None:
    """Route SIGINT and SIGTERM to KeyboardInterrupt, recording the first signal number received."""
    for sig in _INTERRUPT_SIGNALS:
        try:
            signal.signal(sig, _raise_interrupt)
        except (ValueError, OSError):
            # Signals cannot be intercepted outside the main thread
            pass


def reraise_interrupt_signal() -> None:
    """When an interrupt signal was recorded, flush stdout and stderr, restore its default disposition and send it to this process; return when none was recorded."""
    signum = _received_signal
    if signum is None:
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError):
            pass
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)
    # Reached only where os.kill does not end the process by the signal (Windows)
    sys.exit(128 + signum)


@contextmanager
def deferred_interrupts() -> Generator[None]:
    """Block SIGINT and SIGTERM for the block so one received inside it raises KeyboardInterrupt just after the block; block nothing on Windows."""
    if sys.platform == "win32":
        yield
        return
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, _INTERRUPT_SIGNALS)
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def _raise_interrupt(signum: int, frame: FrameType | None) -> None:
    """Record signum when none is recorded yet, then raise KeyboardInterrupt."""
    global _received_signal
    if _received_signal is None:
        _received_signal = signum
    raise KeyboardInterrupt
