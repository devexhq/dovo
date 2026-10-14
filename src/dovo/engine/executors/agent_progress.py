"""Live agent progress: terminal lines, session-log events, and the quiet window the heartbeat consults."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from pathlib import Path
from time import monotonic
from types import MappingProxyType
from typing import Final

from dovo.common.redact import SecretRedactor
from dovo.core.agents.models import AgentProgress, AgentProgressKind
from dovo.core.sessions import SessionLogEvent, SessionLogEventType
from dovo.engine.executors.models import OutputCallback
from dovo.engine.executors.session_log import append_session_log_event

MAX_TARGET_CHARACTERS: Final[int] = 120
_ELLIPSIS: Final[str] = "…"

AGENT_PROGRESS_LINE_FORMATS: Final[Mapping[AgentProgressKind, str]] = MappingProxyType(
    {
        AgentProgressKind.EDIT: "Editing {target}",
        AgentProgressKind.READ: "Reading {target}",
        AgentProgressKind.SEARCH: "Searching for {target}",
        AgentProgressKind.SHELL: "Running {target}",
    }
)


class AgentProgressReporter:
    """Prints collapsed progress lines, appends one session-log event per item and tracks when progress last arrived.

    ``report`` is called from the provider's stdout reader thread while the heartbeat thread reads
    ``seconds_since_last_report``; both go through one lock.
    """

    def __init__(
        self,
        *,
        step_id: str,
        step_index: int,
        attempt: int,
        loop_id: str | None,
        iteration: int | None,
        on_output: OutputCallback | None,
        session_log_dir: Path | None,
        redactor: SecretRedactor,
    ) -> None:
        """Bind the step identity, the output callback, the session log directory and the step's redactor."""
        self._step_id = step_id
        self._step_index = step_index
        self._attempt = attempt
        self._loop_id = loop_id
        self._iteration = iteration
        self._on_output = on_output
        self._session_log_dir = session_log_dir
        self._redactor = redactor
        self._lock = threading.Lock()
        self._last_line: str | None = None
        self._last_report_at: float | None = None

    def report(self, progress: AgentProgress) -> None:
        """Print the item unless it repeats the previous line, append its agent_progress event, and stamp the time."""
        target = self._mask_then_truncate(progress.target)
        line = f"[{self._step_id}] {AGENT_PROGRESS_LINE_FORMATS[progress.kind].format(target=target)}\n"
        with self._lock:
            self._last_report_at = monotonic()
            if line != self._last_line:
                self._last_line = line
                self._print(line)

            append_session_log_event(
                self._session_log_dir,
                SessionLogEvent(
                    event=SessionLogEventType.AGENT_PROGRESS,
                    step_index=self._step_index,
                    step_id=self._step_id,
                    attempt=self._attempt,
                    loop_id=self._loop_id,
                    iteration=self._iteration,
                    kind=progress.kind.value,
                    target=target,
                ),
            )

    def seconds_since_last_report(self) -> float | None:
        """Return whole seconds since the last reported item, or None when none has arrived."""
        with self._lock:
            if self._last_report_at is None:
                return None

            return float(int(monotonic() - self._last_report_at))

    def _mask_then_truncate(self, text: str) -> str:
        """Mask secrets with the step redactor, then truncate to MAX_TARGET_CHARACTERS with a trailing ellipsis."""
        masked = self._redactor.redact_text(text)
        if len(masked) <= MAX_TARGET_CHARACTERS:
            return masked

        return masked[: MAX_TARGET_CHARACTERS - 1] + _ELLIPSIS

    def _print(self, line: str) -> None:
        """Send the line to the output callback, dropping any callback failure."""
        if self._on_output is None:
            return

        try:
            self._on_output("stdout", line)
        except Exception:
            return
