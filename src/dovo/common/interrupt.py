"""Process-wide SIGINT, SIGTERM and SIGHUP handling: raise KeyboardInterrupt, remember the signal, and end the process by it once the command has unwound."""

from __future__ import annotations

import os
import signal
import sys
from collections.abc import Generator
from contextlib import contextmanager
from types import FrameType

_HANGUP_SIGNAL: signal.Signals | None = getattr(signal, "SIGHUP", None)
_INTERRUPT_SIGNALS: tuple[signal.Signals, ...] = (
    (signal.SIGINT, signal.SIGTERM) if _HANGUP_SIGNAL is None else (signal.SIGINT, signal.SIGTERM, _HANGUP_SIGNAL)
)
_received_signal: int | None = None


def install_interrupt_handlers() -> None:
    """Route SIGINT, SIGTERM and SIGHUP (where the platform has it) to KeyboardInterrupt, recording the first signal number received; leave a signal the process inherited as ignored ignored."""
    for sig in _INTERRUPT_SIGNALS:
        if signal.getsignal(sig) is signal.SIG_IGN:
            continue
        try:
            signal.signal(sig, _raise_interrupt)
        except (ValueError, OSError):
            # Signals cannot be intercepted outside the main thread
            pass


def reraise_interrupt_signal() -> None:
    """When an interrupt signal was recorded, flush stdout and stderr, restore its default disposition and send it to this process, or exit 128 + signum on Windows; return when none was recorded."""
    signum = _received_signal
    if signum is None:
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError):
            pass
    if sys.platform == "win32":
        # os.kill on Windows terminates the process with exit code signum, which collides with a usage error
        sys.exit(128 + signum)
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


@contextmanager
def deferred_interrupts() -> Generator[None]:
    """Block SIGINT, SIGTERM and SIGHUP for the block so one received inside it raises KeyboardInterrupt just after the block; block nothing on Windows."""
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
