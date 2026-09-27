"""Read persisted run.log timelines and per-attempt step capture files."""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

from pydantic import ValidationError

from worktree.core.logs.models import LogStreamFilter
from worktree.core.runtime import RunLogEvent

_STEP_LOG_NAME_RE = re.compile(
    r"^(?P<index>\d+)_(?P<step_id>.+?)(?:_iter_(?P<iteration>\d+))?_attempt_(?P<attempt>\d+)\.(?P<stream>stdout|stderr)\.log$"
)


class _StepLogFile(NamedTuple):
    """One per-attempt capture file, with its identity parsed from the filename."""

    step_index: int
    step_id: str
    iteration: int
    attempt: int
    stream: str
    path: Path


def _apply_tail[T](items: list[T], tail: int | None) -> list[T]:
    """Keep only the last `tail` items, or all of them when tail is None."""
    if tail is None:
        return items
    return items[max(len(items) - tail, 0) :]


def _parse_step_log_file(path: Path) -> _StepLogFile | None:
    """Parse a capture filename into its identity, or None when it is not a step log."""
    match = _STEP_LOG_NAME_RE.match(path.name)
    if match is None:
        return None
    return _StepLogFile(
        step_index=int(match["index"]),
        step_id=match["step_id"],
        iteration=int(match["iteration"] or 0),
        attempt=int(match["attempt"]),
        stream=match["stream"],
        path=path,
    )


def list_session_log_files(session_log_dir: Path) -> list[Path]:
    """Return sorted log file paths under session_log_dir, or an empty list if it doesn't exist."""
    if not session_log_dir.is_dir():
        return []
    return sorted(p for p in session_log_dir.iterdir() if p.is_file())


def read_run_log_events(session_log_dir: Path, *, tail: int | None) -> list[RunLogEvent]:
    """Parse run.log's JSON lines into RunLogEvents, skipping any unparseable line, then apply tail."""
    run_log = session_log_dir / "run.log"
    if not run_log.is_file():
        return []

    events: list[RunLogEvent] = []
    # errors="replace": a write cut short mid-character (disk full, SIGKILL) must not make the file unreadable.
    for line in run_log.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            events.append(RunLogEvent.model_validate_json(line))
        except ValidationError:
            # A SIGKILL mid-flush can leave a truncated trailing line.
            continue
    return _apply_tail(events, tail)


def _list_step_log_files(session_log_dir: Path) -> list[_StepLogFile]:
    """Return every per-attempt capture file under session_log_dir, ordered by step index."""
    parsed = (_parse_step_log_file(p) for p in list_session_log_files(session_log_dir))
    return sorted((f for f in parsed if f is not None), key=lambda f: f.step_index)


def _select_attempt_files(step_files: list[_StepLogFile], attempt: int, stream: LogStreamFilter) -> list[_StepLogFile]:
    """Pick one attempt's capture files for the requested stream(s), iteration-ordered with stdout before stderr."""
    streams = [LogStreamFilter.STDOUT, LogStreamFilter.STDERR] if stream == LogStreamFilter.BOTH else [stream]
    selected = [f for f in step_files if f.attempt == attempt and f.stream in streams]
    return sorted(selected, key=lambda f: (f.iteration, streams.index(LogStreamFilter(f.stream))))


def read_step_logs(
    session_log_dir: Path,
    *,
    step: str,
    attempt: int | None,
    stream: LogStreamFilter,
    tail: int | None,
) -> tuple[list[str], list[str], list[int]]:
    """Select and read matching raw step log lines, or return available steps/attempts on a filter miss."""
    step_files = _list_step_log_files(session_log_dir)
    available_steps = list(dict.fromkeys(f.step_id for f in step_files))
    matching_step = [f for f in step_files if f.step_id == step]
    available_attempts = sorted({f.attempt for f in matching_step})
    if not available_attempts:
        return [], available_steps, available_attempts

    selected_attempt = attempt if attempt is not None else available_attempts[-1]
    selected = _select_attempt_files(matching_step, selected_attempt, stream)
    lines = [line for f in selected for line in f.path.read_text(encoding="utf-8", errors="replace").splitlines()]
    return _apply_tail(lines, tail), available_steps, available_attempts
