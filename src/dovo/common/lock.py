"""Cross-process advisory file lock for mutual exclusion on .dovo mutating operations."""

from __future__ import annotations

import atexit
import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import FrameType, TracebackType

LockWaitNotifier = Callable[[Path, str | None, float], None]

# Optional imports based on platform
if sys.platform != "win32":
    import fcntl

    msvcrt = None  # pyright: ignore[reportConstantRedefinition]
else:
    import msvcrt  # pyright: ignore[reportMissingImports]

    fcntl = None  # pyright: ignore[reportConstantRedefinition]

POLL_INTERVAL_SECONDS = 0.2
DEFAULT_LOCK_TIMEOUT_SECONDS = 30.0

_LOCK_REGISTRY: dict[Path, int] = {}
_FD_REGISTRY: dict[Path, int] = {}
_REGISTRY_LOCK = threading.Lock()
_signal_handlers_installed = False


class LockTimeoutError(RuntimeError):
    """Raised when acquiring the workspace lock exceeds the timeout."""


def _cleanup_registered_locks() -> None:
    """Close and unlock all active file descriptors registered in this process."""
    with _REGISTRY_LOCK:
        for file_descriptor in list(_FD_REGISTRY.values()):
            try:
                unlock_file_descriptor(file_descriptor)
                os.close(file_descriptor)
            except Exception:
                pass
        _LOCK_REGISTRY.clear()
        _FD_REGISTRY.clear()


def _signal_handler(signum: int, frame: FrameType | None) -> None:
    """Emergency signal handler releasing held file locks before exit."""
    _cleanup_registered_locks()
    sys.exit(128 + signum)


def _ensure_signal_handlers() -> None:
    """Register exit and signal handlers once per process."""
    global _signal_handlers_installed
    with _REGISTRY_LOCK:
        if _signal_handlers_installed:
            return
        atexit.register(_cleanup_registered_locks)
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _signal_handler)
            except (ValueError, OSError, AttributeError):
                # Signals might not be interceptable in non-main threads or some environments
                pass
        _signal_handlers_installed = True


def _try_flock_posix(file_descriptor: int) -> bool:
    """Attempt non-blocking flock acquisition on POSIX systems."""
    if fcntl is None:
        return False
    try:
        fcntl.flock(file_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (BlockingIOError, OSError, PermissionError):
        return False


def _try_lock_windows(file_descriptor: int) -> bool:
    """Attempt non-blocking byte lock on Windows systems."""
    if msvcrt is None:
        return False
    try:
        os.lseek(file_descriptor, 0, os.SEEK_SET)
        msvcrt.locking(file_descriptor, msvcrt.LK_NBLCK, 1)
        return True
    except (BlockingIOError, OSError, PermissionError):
        return False


def try_acquire_file_descriptor_lock(file_descriptor: int) -> bool:
    """Attempt non-blocking platform-specific file lock."""
    if sys.platform != "win32":
        return _try_flock_posix(file_descriptor)
    return _try_lock_windows(file_descriptor)


def unlock_file_descriptor(file_descriptor: int) -> None:
    """Release platform-specific file lock on file_descriptor."""
    if sys.platform != "win32":
        if fcntl is not None:
            try:
                fcntl.flock(file_descriptor, fcntl.LOCK_UN)
            except Exception:
                pass
    elif msvcrt is not None:
        try:
            os.lseek(file_descriptor, 0, os.SEEK_SET)
            msvcrt.locking(file_descriptor, msvcrt.LK_UNLCK, 1)
        except Exception:
            pass


def _read_holder_pid(lock_path: Path) -> str | None:
    """Read holder process ID from the lock file if present."""
    if not lock_path.exists():
        return None
    try:
        content = lock_path.read_text(encoding="utf-8").strip()
        return content if content.isdigit() else None
    except Exception:
        return None


def _write_holder_pid(file_descriptor: int) -> None:
    """Write current process PID into the lock file and flush."""
    try:
        os.lseek(file_descriptor, 0, os.SEEK_SET)
        os.ftruncate(file_descriptor, 0)
        pid_payload = f"{os.getpid()}\n".encode()
        os.write(file_descriptor, pid_payload)
        os.fsync(file_descriptor)
    except Exception:
        pass


def _check_lock_timeout(
    start_time: float,
    timeout_seconds: float,
    lock_path: Path,
    holder_pid: str | None,
) -> None:
    """Raise LockTimeoutError if the elapsed polling time has exceeded timeout_seconds."""
    elapsed = time.monotonic() - start_time
    if elapsed + POLL_INTERVAL_SECONDS >= timeout_seconds:
        pid_desc = f"process {holder_pid}" if holder_pid else "unknown process"
        raise LockTimeoutError(
            f"Timed out waiting for workspace lock on '{lock_path}' after "
            f"{timeout_seconds:.1f}s (held by {pid_desc}).\n"
            f"Fix:\n- check if another `dovo` command is running, or remove '{lock_path}' if the process terminated."
        )


def resolve_lock_file_path(root_dir: Path) -> Path:
    """Determine the canonical .dovo/.lock path for root_dir."""
    canonical_root = root_dir.expanduser().resolve()
    if canonical_root.name == ".lock":
        return canonical_root
    if canonical_root.name == ".dovo":
        return canonical_root / ".lock"
    return canonical_root / ".dovo" / ".lock"


_default_on_wait: LockWaitNotifier | None = None


class WorkspaceLock:
    """Cross-process advisory file lock context manager for .dovo/."""

    @classmethod
    def set_default_on_wait(cls, callback: LockWaitNotifier | None) -> None:
        """Configure process-level default wait callback for lock contention."""
        global _default_on_wait
        _default_on_wait = callback

    @classmethod
    def reset_default_on_wait(cls) -> None:
        """Reset process-level default wait callback to None."""
        global _default_on_wait
        _default_on_wait = None

    @classmethod
    def get_default_on_wait(cls) -> LockWaitNotifier | None:
        """Return currently configured process-level default wait callback."""
        return _default_on_wait

    def __init__(
        self,
        lock_path: Path,
        timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
        on_wait: Callable[[Path, str | None, float], None] | None = None,
    ) -> None:
        """Initialize workspace lock bound to a resolved lock file path.

        Args:
            lock_path: The resolved .dovo/.lock (or global-tier) file path.
            timeout_seconds: Maximum seconds to wait for lock acquisition.
            on_wait: Optional callback invoked if lock is currently held.
        """
        self.lock_path = lock_path
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.on_wait = on_wait if on_wait is not None else _default_on_wait
        self._file_descriptor: int | None = None
        self._is_nested: bool = False
        self._local_depth: int = 0

    @property
    def is_locked(self) -> bool:
        """Return True if this lock instance is currently acquired."""
        return self._file_descriptor is not None and self._local_depth > 0

    def _open_lock_file(self) -> int:
        """Ensure parent directory exists and open lock file descriptor."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        return os.open(str(self.lock_path), os.O_RDWR | os.O_CREAT, 0o644)

    def _poll_lock(self, file_descriptor: int) -> bool:
        """Poll for lock acquisition up to timeout_seconds."""
        start_time = time.monotonic()
        announced = False

        while True:
            if try_acquire_file_descriptor_lock(file_descriptor):
                _write_holder_pid(file_descriptor)
                return True

            holder_pid = _read_holder_pid(self.lock_path)
            if not announced:
                if self.on_wait is not None:
                    self.on_wait(self.lock_path, holder_pid, self.timeout_seconds)
                announced = True

            _check_lock_timeout(start_time, self.timeout_seconds, self.lock_path, holder_pid)
            time.sleep(POLL_INTERVAL_SECONDS)

    def acquire(self) -> WorkspaceLock:
        """Acquire the file lock, supporting in-process re-entrancy."""
        _ensure_signal_handlers()

        with _REGISTRY_LOCK:
            depth = _LOCK_REGISTRY.get(self.lock_path, 0)
            if depth > 0:
                _LOCK_REGISTRY[self.lock_path] = depth + 1
                self._file_descriptor = _FD_REGISTRY.get(self.lock_path)
                self._is_nested = True
                self._local_depth += 1
                return self

        file_descriptor = self._open_lock_file()
        try:
            self._poll_lock(file_descriptor)
        except Exception:
            try:
                os.close(file_descriptor)
            except Exception:
                pass
            raise

        with _REGISTRY_LOCK:
            _LOCK_REGISTRY[self.lock_path] = 1
            _FD_REGISTRY[self.lock_path] = file_descriptor
            self._file_descriptor = file_descriptor
            self._is_nested = False
            self._local_depth = 1

        return self

    def release(self) -> None:
        """Release the file lock, decrementing re-entrancy depth."""
        if not self.is_locked:
            return

        file_descriptor: int | None = None
        with _REGISTRY_LOCK:
            if not self.is_locked:
                return
            self._local_depth -= 1
            depth = _LOCK_REGISTRY.get(self.lock_path, 0)
            if depth > 1:
                _LOCK_REGISTRY[self.lock_path] = depth - 1
                if self._local_depth == 0:
                    self._file_descriptor = None
                return

            _LOCK_REGISTRY.pop(self.lock_path, None)
            file_descriptor = _FD_REGISTRY.pop(self.lock_path, None)
            self._file_descriptor = None

        if file_descriptor is not None:
            try:
                unlock_file_descriptor(file_descriptor)
            finally:
                try:
                    os.close(file_descriptor)
                except Exception:
                    pass

    def __enter__(self) -> WorkspaceLock:
        """Context manager entry point acquiring workspace lock."""
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Context manager exit point releasing workspace lock."""
        self.release()
