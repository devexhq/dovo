"""Contract tests for the agent progress reporter: terminal lines, session-log events and the quiet window."""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from dovo.common.constants import SESSION_LOG_FILENAME
from dovo.common.redact import SecretRedactor
from dovo.core.agents import AgentProgress, AgentProgressKind
from dovo.core.sessions import SessionLogEvent, SessionLogEventType
from dovo.engine.executors.agent_progress import AgentProgressReporter


def _reporter(
    *,
    on_output: list[tuple[str, str]] | None = None,
    session_log_dir: Path | None = None,
    redactor: SecretRedactor | None = None,
    raising: bool = False,
    loop_id: str | None = None,
    iteration: int | None = None,
) -> AgentProgressReporter:
    def collect(stream_name: str, line: str) -> None:
        if raising:
            raise RuntimeError("terminal gone")

        if on_output is not None:
            on_output.append((stream_name, line))

    return AgentProgressReporter(
        step_id="code",
        step_index=2,
        attempt=1,
        loop_id=loop_id,
        iteration=iteration,
        on_output=collect,
        session_log_dir=session_log_dir,
        redactor=redactor or SecretRedactor([]),
    )


def _logged_events(session_log_dir: Path) -> list[SessionLogEvent]:
    lines = (session_log_dir / SESSION_LOG_FILENAME).read_text(encoding="utf-8").splitlines()

    return [SessionLogEvent.model_validate_json(line) for line in lines]


class AgentProgressReporterTests:
    @pytest.mark.parametrize(
        ("kind", "target", "expected"),
        [
            pytest.param(AgentProgressKind.EDIT, "src/x.py", "[code] Editing src/x.py\n", id="edit"),
            pytest.param(AgentProgressKind.READ, "src/x.py", "[code] Reading src/x.py\n", id="read"),
            pytest.param(AgentProgressKind.SEARCH, "Needle", "[code] Searching for Needle\n", id="search"),
            pytest.param(AgentProgressKind.SHELL, "uv run pytest", "[code] Running uv run pytest\n", id="shell"),
        ],
    )
    def test_report_prints_the_kind_format_with_the_step_id(
        self, kind: AgentProgressKind, target: str, expected: str
    ) -> None:
        """[tier-1/unit] AgentProgressReporter.report: step_id 'code' and one item of the given kind and target call on_output once with ('stdout', expected)."""
        printed: list[tuple[str, str]] = []

        _reporter(on_output=printed).report(AgentProgress(kind=kind, target=target))

        assert printed == [("stdout", expected)]

    def test_consecutive_identical_items_print_once(self) -> None:
        """[tier-1/unit] AgentProgressReporter.report: reporting READ src/x.py twice then EDIT src/x.py then READ src/x.py prints three lines, the repeat collapsed."""
        printed: list[tuple[str, str]] = []
        reporter = _reporter(on_output=printed)
        read = AgentProgress(kind=AgentProgressKind.READ, target="src/x.py")
        edit = AgentProgress(kind=AgentProgressKind.EDIT, target="src/x.py")

        for item in (read, read, edit, read):
            reporter.report(item)

        assert [line for _, line in printed] == [
            "[code] Reading src/x.py\n",
            "[code] Editing src/x.py\n",
            "[code] Reading src/x.py\n",
        ]

    def test_on_output_failure_does_not_raise(self, tmp_path: Path) -> None:
        """[tier-1/unit] AgentProgressReporter.report: an on_output that raises RuntimeError makes report return None and still appends the session-log event."""
        reporter = _reporter(raising=True, session_log_dir=tmp_path)

        assert reporter.report(AgentProgress(kind=AgentProgressKind.READ, target="x")) is None
        assert [event.target for event in _logged_events(tmp_path)] == ["x"]

    def test_seconds_since_last_report_is_none_until_the_first_item(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """[tier-1/unit] AgentProgressReporter.seconds_since_last_report: returns None before any report and 10 after a report with the module clock advanced 10.4 seconds."""
        readings = itertools.chain([100.0, 110.4], itertools.repeat(110.4))
        monkeypatch.setattr("dovo.engine.executors.agent_progress.monotonic", lambda: next(readings))
        reporter = _reporter()

        assert reporter.seconds_since_last_report() is None

        reporter.report(AgentProgress(kind=AgentProgressKind.READ, target="x"))

        assert reporter.seconds_since_last_report() == 10


class AgentProgressSessionLogTests:
    def test_report_appends_one_event_per_item_with_step_and_loop_fields(self, tmp_path: Path) -> None:
        """[tier-1/unit] AgentProgressReporter.report: with step_id 'code', step_index 2, attempt 1, loop_id 'code-loop', iteration 2, two identical items append two session.log lines whose events are agent_progress with those fields plus kind 'read' and the target."""
        reporter = _reporter(session_log_dir=tmp_path, loop_id="code-loop", iteration=2)
        item = AgentProgress(kind=AgentProgressKind.READ, target="src/x.py")

        reporter.report(item)
        reporter.report(item)

        events = _logged_events(tmp_path)
        assert [event.model_copy(update={"ts": ""}) for event in events] == [
            SessionLogEvent(
                event=SessionLogEventType.AGENT_PROGRESS,
                step_index=2,
                step_id="code",
                attempt=1,
                loop_id="code-loop",
                iteration=2,
                kind="read",
                target="src/x.py",
            )
        ] * 2

    def test_report_masks_the_target_with_the_step_redactor(self, tmp_path: Path) -> None:
        """[tier-1/unit] AgentProgressReporter.report: an item whose target holds a secret known only to the reporter's redactor writes a session.log target containing '[REDACTED' and not the secret."""
        secret = "hunter2-hunter2"
        reporter = _reporter(session_log_dir=tmp_path, redactor=SecretRedactor([("TOKEN", secret)]))

        reporter.report(AgentProgress(kind=AgentProgressKind.SHELL, target=f"curl -H {secret}"))

        (event,) = _logged_events(tmp_path)
        assert event.target is not None
        assert "[REDACTED" in event.target
        assert secret not in event.target

    def test_report_without_a_session_log_dir_writes_no_event(self) -> None:
        """[tier-1/unit] AgentProgressReporter.report: session_log_dir None returns None, prints the line."""
        printed: list[tuple[str, str]] = []

        result = _reporter(on_output=printed).report(AgentProgress(kind=AgentProgressKind.READ, target="x"))

        assert result is None
        assert printed == [("stdout", "[code] Reading x\n")]

    def test_report_masks_a_step_only_secret_before_truncating_the_target(self, tmp_path: Path) -> None:
        """[tier-1/unit] AgentProgressReporter.report: a 300-character shell target holding a secret known only to the reporter's redactor, starting at index 110, prints and logs a target of exactly the masked text cut to 119 characters plus '…', with no character of the secret."""
        secret = "QWERTYUIOPASDFGHJKLZ"
        redactor = SecretRedactor([("TOKEN", secret)])
        printed: list[tuple[str, str]] = []
        reporter = _reporter(on_output=printed, session_log_dir=tmp_path, redactor=redactor)
        command = "a" * 110 + secret + "b" * 100
        expected = redactor.redact_text(command)[:119] + "…"

        reporter.report(AgentProgress(kind=AgentProgressKind.SHELL, target=command))

        (event,) = _logged_events(tmp_path)
        assert event.target == expected
        assert secret[:4] not in expected
        assert printed == [("stdout", f"[code] Running {expected}\n")]

    def test_report_truncates_a_long_target_to_119_characters_plus_ellipsis(self, tmp_path: Path) -> None:
        """[tier-1/unit] AgentProgressReporter.report: a 300-character target without secrets logs a target of length 120 ending in '…'."""
        reporter = _reporter(session_log_dir=tmp_path)

        reporter.report(AgentProgress(kind=AgentProgressKind.SHELL, target="x" * 300))

        (event,) = _logged_events(tmp_path)
        assert event.target == "x" * 119 + "…"
