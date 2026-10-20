"""Live agent progress: observer updates, session-log events, and the quiet window the heartbeat consults."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Final

from dovo.common.redact import SecretRedactor
from dovo.common.timing import Stopwatch
from dovo.core.agents.models import AgentProgress, AgentProgressKind
from dovo.core.sessions import SessionLogEvent, SessionLogEventType
from dovo.engine.executors.models import AgentProgressUpdate, ProgressCallback
from dovo.engine.executors.session_log import append_session_log_event

MAX_TARGET_CHARACTERS: Final[int] = 120
MAX_RESULT_LINES: Final[int] = 3
_ELLIPSIS: Final[str] = "…"


class AgentProgressReporter:
    """Delivers collapsed progress updates to observers, appends one session-log event per reported action or note and tracks when progress last arrived.

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
        on_progress: ProgressCallback | None,
        session_log_dir: Path | None,
        redactor: SecretRedactor,
    ) -> None:
        """Bind the step identity, the progress callback, the session log directory and the step's redactor."""
        self._step_id = step_id
        self._step_index = step_index
        self._attempt = attempt
        self._loop_id = loop_id
        self._iteration = iteration
        self._on_progress = on_progress
        self._session_log_dir = session_log_dir
        self._redactor = redactor
        self._lock = threading.Lock()
        self._held_notes: list[str] = []
        self._call_kinds: dict[str, AgentProgressKind] = {}
        self._last_reported: tuple[AgentProgressKind, str] | None = None
        self._since_last_report: Stopwatch | None = None

    def report(self, progress: AgentProgress) -> None:
        """Stamp the time, hold a note until the next action, and report any other item with the notes held before it.

        A note still held when the attempt ends is never reported, so the agent's closing message is not echoed.
        A result is reported only for an action that was itself reported, and it ends the collapsing of repeats
        so the same action run again afterwards is shown again.
        """
        with self._lock:
            self._since_last_report = Stopwatch.start()
            if progress.kind is AgentProgressKind.NOTE:
                self._held_notes.append(self._mask_then_truncate(progress.target))
            elif progress.kind is AgentProgressKind.RESULT:
                self._report_result(progress)
            else:
                self._report_action(progress)

    def seconds_since_last_report(self) -> float | None:
        """Return whole seconds since the last reported item, or None when none has arrived."""
        with self._lock:
            if self._since_last_report is None:
                return None

            return float(int(self._since_last_report.elapsed_seconds))

    def _report_action(self, progress: AgentProgress) -> None:
        """Report the held notes and then the action, remembering the action's kind under its call id when it was shown."""
        for note in self._held_notes:
            self._report_item(AgentProgressKind.NOTE, note)

        self._held_notes.clear()
        shown = self._report_item(progress.kind, self._mask_then_truncate(progress.target))
        if shown and progress.call_id is not None:
            self._call_kinds[progress.call_id] = progress.kind

    def _report_result(self, progress: AgentProgress) -> None:
        """Deliver an excerpt of the masked output under the action that produced it; results of unshown calls are dropped."""
        call_kind = self._call_kinds.pop(progress.call_id or "", None)
        if call_kind is None:
            return

        lines = self._redactor.redact_text(progress.target).splitlines()
        self._last_reported = None
        self._deliver(
            AgentProgressUpdate(
                kind=AgentProgressKind.RESULT,
                target="\n".join(self._truncate(line) for line in lines[:MAX_RESULT_LINES]),
                call_kind=call_kind,
                line_count=len(lines),
                is_error=progress.is_error,
            )
        )

    def _report_item(self, kind: AgentProgressKind, target: str) -> bool:
        """Deliver the item unless it repeats the previous one, and log it.

        Return whether it was delivered; the session-log event is appended even when delivery is collapsed.
        """
        shown = (kind, target) != self._last_reported
        if shown:
            self._last_reported = (kind, target)
            self._deliver(AgentProgressUpdate(kind=kind, target=target))

        append_session_log_event(
            self._session_log_dir,
            SessionLogEvent(
                event=SessionLogEventType.AGENT_PROGRESS,
                step_index=self._step_index,
                step_id=self._step_id,
                attempt=self._attempt,
                loop_id=self._loop_id,
                iteration=self._iteration,
                kind=kind.value,
                target=target,
            ),
        )

        return shown

    def _mask_then_truncate(self, text: str) -> str:
        """Mask secrets with the step redactor, then truncate to MAX_TARGET_CHARACTERS with a trailing ellipsis."""
        return self._truncate(self._redactor.redact_text(text))

    def _truncate(self, text: str) -> str:
        """Truncate to MAX_TARGET_CHARACTERS with a trailing ellipsis."""
        if len(text) <= MAX_TARGET_CHARACTERS:
            return text

        return text[: MAX_TARGET_CHARACTERS - 1] + _ELLIPSIS

    def _deliver(self, update: AgentProgressUpdate) -> None:
        """Send the update to the progress callback, dropping any callback failure."""
        if self._on_progress is None:
            return

        try:
            self._on_progress(update)
        except Exception:
            return
