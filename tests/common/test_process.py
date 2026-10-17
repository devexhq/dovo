"""Tier 1 unit tests for process isolation and platform primitives."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.common.process import get_isolated_process_kwargs, process_registry, stream_isolated_process

WINDOWS_CREATION_FLAGS: int = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)

PLATFORM_ISOLATION_CASES = [
    pytest.param("linux", {"start_new_session": True}, id="posix_linux"),
    pytest.param("darwin", {"start_new_session": True}, id="posix_darwin"),
    pytest.param("win32", {"creationflags": WINDOWS_CREATION_FLAGS}, id="windows_win32"),
]


class ProcessIsolationPlatformTests:
    """Unit tests verifying subprocess process group isolation flags across platforms."""

    @pytest.mark.parametrize(("platform_name", "expected_kwargs"), PLATFORM_ISOLATION_CASES)
    def test_isolated_process_flags_match_platform_primitives(
        self,
        monkeypatch: pytest.MonkeyPatch,
        platform_name: str,
        expected_kwargs: dict[str, object],
    ) -> None:
        monkeypatch.setattr(sys, "platform", platform_name)
        assert get_isolated_process_kwargs() == expected_kwargs

    def test_ambient_platform_isolation_flags_match_system(self) -> None:
        expected = {"creationflags": WINDOWS_CREATION_FLAGS} if sys.platform == "win32" else {"start_new_session": True}
        assert get_isolated_process_kwargs() == expected


BOUNDED_CALL_DEADLINE_SECONDS: float = 30.0

PRINT_LINES_SCRIPT = "import sys; print('out1'); print('out2'); print('err1', file=sys.stderr)"
EMIT_BYTES_SCRIPT = "import sys; sys.stdout.buffer.write(bytes.fromhex(sys.argv[1]))"
PRINT_THEN_SLEEP_SCRIPT = "import sys, time; sys.stdout.write('ready\\n'); sys.stdout.flush(); time.sleep(60)"
SLEEP_SCRIPT = "import time; time.sleep(60)"
EXIT_WITHOUT_READING_SCRIPT = "import sys; sys.exit(3)"
FIFO_GATED_SCRIPT = "import sys; print('first', flush=True); open(sys.argv[1]).read(); print('second', flush=True)"
THREE_LINES_SCRIPT = "print('first'); print('second'); print('third')"


def _python(script: str, *args: str) -> list[str]:
    """Return a command running the given inline script under the current interpreter."""
    return [sys.executable, "-c", script, *args]


def _run_bounded(
    call: Callable[[], subprocess.CompletedProcess[bytes]],
) -> subprocess.CompletedProcess[bytes] | BaseException:
    """Run call in a daemon thread and fail the test instead of hanging when it does not return in time."""
    outcome: list[subprocess.CompletedProcess[bytes] | BaseException] = []

    def target() -> None:
        try:
            outcome.append(call())
        except BaseException as error:
            outcome.append(error)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(BOUNDED_CALL_DEADLINE_SECONDS)
    if thread.is_alive():
        pytest.fail("stream_isolated_process did not return within the deadline")
    return outcome[0]


class StreamIsolatedProcessTests:
    """Tier-1 tests for the line-streaming isolated process runner."""

    def test_lines_reach_their_stream_callbacks_in_order_and_bytes_are_complete(self) -> None:
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        completed = stream_isolated_process(
            _python(PRINT_LINES_SCRIPT), on_stdout_line=stdout_lines.append, on_stderr_line=stderr_lines.append
        )

        assert stdout_lines == ["out1", "out2"]
        assert stderr_lines == ["err1"]
        assert (completed.returncode, completed.stdout, completed.stderr) == (0, b"out1\nout2\n", b"err1\n")

    def test_line_is_delivered_before_the_child_exits(self, tmp_path: Path) -> None:
        if sys.platform == "win32":
            pytest.skip("POSIX FIFOs required")
        fifo = tmp_path / "release.fifo"
        os.mkfifo(fifo)

        def release_child(line: str) -> None:
            if line == "first":
                fifo.write_text("go")

        outcome = _run_bounded(
            lambda: stream_isolated_process(
                _python(FIFO_GATED_SCRIPT, str(fifo)), timeout_seconds=20.0, on_stdout_line=release_child
            )
        )

        assert isinstance(outcome, subprocess.CompletedProcess)
        assert outcome.stdout == b"first\nsecond\n"

    @pytest.mark.parametrize(
        ("emitted", "expected_lines"),
        [
            pytest.param(b"head\ntail", ["head", "tail"], id="unterminated-final-record"),
            pytest.param(b"bad\xff\n", ["bad\ufffd"], id="invalid-utf8"),
            pytest.param(b"one\r\ntwo\n", ["one", "two"], id="crlf-and-lf"),
        ],
    )
    def test_final_and_malformed_records_reach_the_callback_once_with_exact_bytes_kept(
        self, emitted: bytes, expected_lines: list[str]
    ) -> None:
        """Pass each emitted byte string to on_stdout_line decoded with replacement characters and newline-stripped."""
        stdout_lines: list[str] = []

        completed = stream_isolated_process(
            _python(EMIT_BYTES_SCRIPT, emitted.hex()), on_stdout_line=stdout_lines.append
        )

        assert stdout_lines == expected_lines
        assert completed.stdout == emitted

    def test_callback_that_raises_loses_only_its_own_line(self) -> None:
        received: list[str] = []

        def failing_callback(line: str) -> None:
            received.append(line)
            if line == "first":
                raise RuntimeError("callback failure")

        completed = stream_isolated_process(_python(THREE_LINES_SCRIPT), on_stdout_line=failing_callback)

        assert received == ["first", "second", "third"]
        assert (completed.returncode, completed.stdout) == (0, b"first\nsecond\nthird\n")

    def test_timeout_terminates_the_child_and_error_carries_pre_kill_output(self) -> None:
        stdout_lines: list[str] = []
        registered_before = len(process_registry)

        outcome = _run_bounded(
            lambda: stream_isolated_process(
                _python(PRINT_THEN_SLEEP_SCRIPT),
                timeout_seconds=1.0,
                grace_seconds=0.2,
                on_stdout_line=stdout_lines.append,
            )
        )

        assert isinstance(outcome, subprocess.TimeoutExpired)
        assert outcome.output == b"ready\n"
        assert stdout_lines == ["ready"]
        assert len(process_registry) == registered_before

    def test_large_input_to_child_that_never_reads_stdin_times_out(self) -> None:
        outcome = _run_bounded(
            lambda: stream_isolated_process(
                _python(SLEEP_SCRIPT), input_data=b"x" * (1024 * 1024), timeout_seconds=1.0, grace_seconds=0.5
            )
        )

        assert isinstance(outcome, subprocess.TimeoutExpired)

    def test_large_input_to_child_that_exits_early_returns_its_exit_code(self) -> None:
        outcome = _run_bounded(
            lambda: stream_isolated_process(
                _python(EXIT_WITHOUT_READING_SCRIPT), input_data=b"x" * (1024 * 1024), timeout_seconds=20.0
            )
        )

        assert isinstance(outcome, subprocess.CompletedProcess)
        assert outcome.returncode == 3

    def test_missing_executable_raises_file_not_found(self, tmp_path: Path) -> None:
        registered_before = len(process_registry)

        with pytest.raises(FileNotFoundError):
            stream_isolated_process([str(tmp_path / "no-such-executable")])

        assert len(process_registry) == registered_before
