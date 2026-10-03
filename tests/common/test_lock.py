"""Tier 1 and subsystem integration tests for WorkspaceLock."""

from __future__ import annotations

import concurrent.futures
import os
import subprocess
import sys
from pathlib import Path

import pytest

from dovo.common.lock import (
    LockTimeoutError,
    WorkspaceLock,
    resolve_lock_file_path,
    try_acquire_file_descriptor_lock,
    unlock_file_descriptor,
)


class WorkspaceLockTests:
    """Integration tests verifying cross-process advisory lock contracts."""

    def test_lock_supports_nested_acquisition_in_same_process(self, tmp_path: Path) -> None:
        """Verify WorkspaceLock supports reentrant nested acquisition within the same process."""
        lock = WorkspaceLock(tmp_path / ".lock")
        assert not lock.is_locked

        with lock:
            assert lock.is_locked
            with lock:
                assert lock.is_locked
            assert lock.is_locked

        assert not lock.is_locked

        lock_a = WorkspaceLock(tmp_path / ".lock")
        lock_b = WorkspaceLock(tmp_path / ".lock")
        assert not lock_a.is_locked
        assert not lock_b.is_locked

        with lock_a:
            assert lock_a.is_locked
            assert not lock_b.is_locked
            with lock_b:
                assert lock_a.is_locked
                assert lock_b.is_locked
            assert lock_a.is_locked
            assert not lock_b.is_locked

        assert not lock_a.is_locked
        assert not lock_b.is_locked

    def test_release_when_not_locked_leaves_state_unchanged(self, tmp_path: Path) -> None:
        """Verify calling release on an unacquired or already released lock does not corrupt registry state."""
        lock_a = WorkspaceLock(resolve_lock_file_path(tmp_path))
        lock_a.release()
        assert not lock_a.is_locked

        with lock_a:
            assert lock_a.is_locked
            lock_b = WorkspaceLock(resolve_lock_file_path(tmp_path))
            lock_b.release()
            assert not lock_b.is_locked
            assert lock_a.is_locked

        lock_a.release()
        assert not lock_a.is_locked

    def test_lock_dispatches_on_wait_callback_on_contention(self, tmp_path: Path) -> None:
        """Verify on_wait callback is dispatched after 200ms when lock is contested and timeout raises LockTimeoutError."""
        lock_file = (tmp_path / ".dovo" / ".lock").resolve()
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor = os.open(str(lock_file), os.O_RDWR | os.O_CREAT, 0o644)
        acquired = try_acquire_file_descriptor_lock(file_descriptor)
        assert acquired

        try:
            os.lseek(file_descriptor, 0, os.SEEK_SET)
            os.ftruncate(file_descriptor, 0)
            os.write(file_descriptor, b"99999\n")
            os.fsync(file_descriptor)

            dispatched: list[tuple[Path, str | None, float]] = []

            def on_wait_callback(path: Path, pid: str | None, timeout: float) -> None:
                dispatched.append((path, pid, timeout))

            lock = WorkspaceLock(resolve_lock_file_path(tmp_path), timeout_seconds=0.3, on_wait=on_wait_callback)
            assert not lock.is_locked

            with pytest.raises(LockTimeoutError) as exc_info:
                with lock:
                    pass

            assert not lock.is_locked
            assert len(dispatched) == 1
            assert dispatched[0] == (lock_file, "99999", 0.3)
            error_message = str(exc_info.value)
            assert str(lock_file) in error_message
            assert "99999" in error_message
        finally:
            unlock_file_descriptor(file_descriptor)
            os.close(file_descriptor)

    def test_lock_queueing_acquires_cleanly_when_holder_exits(self, tmp_path: Path) -> None:
        """Verify a waiting WorkspaceLock queues during contention and acquires cleanly when the holding process exits."""
        child_code = f"""
import time
from pathlib import Path
from dovo.common.lock import WorkspaceLock, resolve_lock_file_path

lock = WorkspaceLock(resolve_lock_file_path(Path({str(tmp_path)!r})))
with lock:
    print("LOCKED", flush=True)
    time.sleep(0.3)
"""
        proc = subprocess.Popen(
            [sys.executable, "-c", child_code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            assert proc.stdout is not None
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(proc.stdout.readline)
                line = future.result(timeout=5.0)
            assert line.strip() == "LOCKED"

            parent_lock = WorkspaceLock(resolve_lock_file_path(tmp_path), timeout_seconds=3.0)
            assert not parent_lock.is_locked

            with parent_lock:
                assert parent_lock.is_locked

            assert not parent_lock.is_locked

            proc.communicate(timeout=2.0)
            assert proc.returncode == 0
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=1.0)
