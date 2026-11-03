"""Tier 1 tests for reading persisted per-attempt step capture files."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.core.sessions import LogStreamFilter, SessionError
from dovo.core.sessions.services.read_logs import read_step_logs
from dovo.engine.executors.metadata import resolve_step_log_paths

# (iteration, attempt, stdout text, stderr text) per capture pair written for step "build" at index 1.
type CaptureSpec = tuple[int | None, int, str, str]


class ReadStepLogsTests:
    @pytest.mark.parametrize(
        ("captures", "stream", "expected_lines", "expected_attempts"),
        [
            pytest.param(
                [(None, 1, "old out\n", "old err\n"), (None, 2, "new out\n", "new err\n")],
                LogStreamFilter.BOTH,
                ["new out", "new err"],
                [1, 2],
                id="non_loop",
            ),
            pytest.param(
                [(2, 1, "iteration2 out\n", "iteration2 err\n"), (1, 1, "iteration1 out\n", "iteration1 err\n")],
                LogStreamFilter.BOTH,
                ["iteration1 out", "iteration1 err", "iteration2 out", "iteration2 err"],
                [1],
                id="two_iteration_loop",
            ),
            pytest.param(
                [(None, 1, "out\n", "err\n")],
                LogStreamFilter.STDERR,
                ["err"],
                [1],
                id="stderr_only_stream",
            ),
        ],
    )
    def test_read_step_logs_selects_latest_attempt_ordered_by_iteration_then_stream(
        self,
        tmp_path: Path,
        captures: list[CaptureSpec],
        stream: LogStreamFilter,
        expected_lines: list[str],
        expected_attempts: list[int],
    ) -> None:
        for iteration, attempt, stdout_text, stderr_text in captures:
            stdout_path, stderr_path = resolve_step_log_paths(
                tmp_path, step_index=1, step_id="build", attempt=attempt, iteration=iteration
            )
            stdout_path.write_text(stdout_text, encoding="utf-8")
            stderr_path.write_text(stderr_text, encoding="utf-8")

        lines, available_steps, available_attempts = read_step_logs(
            tmp_path, step="build", attempt=None, stream=stream, tail=None
        )

        assert (lines, available_steps, available_attempts) == (expected_lines, ["build"], expected_attempts)

    def test_read_step_logs_with_unknown_step_returns_no_lines_and_the_captured_steps(self, tmp_path: Path) -> None:
        stdout_path, _ = resolve_step_log_paths(tmp_path, step_index=1, step_id="build", attempt=1, iteration=None)
        stdout_path.write_text("out\n", encoding="utf-8")

        result = read_step_logs(tmp_path, step="missing", attempt=None, stream=LogStreamFilter.BOTH, tail=None)

        assert result == ([], ["build"], [])

    def test_read_step_logs_with_unreadable_capture_raises_logs_unreadable_chained_to_the_os_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stdout_path, _ = resolve_step_log_paths(tmp_path, step_index=1, step_id="build", attempt=1, iteration=None)
        stdout_path.write_text("out\n", encoding="utf-8")
        denied = PermissionError("denied")

        def _deny_read(self: Path, *args: object, **kwargs: object) -> str:
            raise denied

        monkeypatch.setattr(Path, "read_text", _deny_read)

        with pytest.raises(SessionError) as raised:
            read_step_logs(tmp_path, step="build", attempt=None, stream=LogStreamFilter.BOTH, tail=None)

        assert raised.value.error_code is ErrorCode.SESSION_LOGS_UNREADABLE
        assert raised.value.message == f"Failed reading session logs in '{tmp_path}': denied"
        assert raised.value.__cause__ is denied
