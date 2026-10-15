"""Contract tests for the agent progress reporter: observer updates, session-log events and the quiet window."""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from dovo.common.constants import SESSION_LOG_FILENAME
from dovo.common.redact import SecretRedactor
from dovo.core.agents import AgentProgress, AgentProgressKind
from dovo.core.agents.claude_progress import map_claude_line
from dovo.core.sessions import SessionLogEvent, SessionLogEventType
from dovo.engine.executors.agent_progress import AgentProgressReporter
from dovo.engine.executors.models import AgentProgressUpdate
from tests.harness.fixtures import load_agent_fixture


def _reporter(
    *,
    updates: list[AgentProgressUpdate] | None = None,
    session_log_dir: Path | None = None,
    redactor: SecretRedactor | None = None,
    raising: bool = False,
    loop_id: str | None = None,
    iteration: int | None = None,
) -> AgentProgressReporter:
    def collect(update: AgentProgressUpdate) -> None:
        if raising:
            raise RuntimeError("terminal gone")

        if updates is not None:
            updates.append(update)

    return AgentProgressReporter(
        step_id="code",
        step_index=2,
        attempt=1,
        loop_id=loop_id,
        iteration=iteration,
        on_progress=collect,
        session_log_dir=session_log_dir,
        redactor=redactor or SecretRedactor([]),
    )


def _logged_events(session_log_dir: Path) -> list[SessionLogEvent]:
    lines = (session_log_dir / SESSION_LOG_FILENAME).read_text(encoding="utf-8").splitlines()

    return [SessionLogEvent.model_validate_json(line) for line in lines]


def _shown(updates: list[AgentProgressUpdate]) -> list[tuple[AgentProgressKind, str]]:
    return [(update.kind, update.target) for update in updates]


class AgentProgressReporterTests:
    @pytest.mark.parametrize(
        ("kind", "target"),
        [
            pytest.param(AgentProgressKind.EDIT, "src/x.py", id="edit"),
            pytest.param(AgentProgressKind.READ, "src/x.py", id="read"),
            pytest.param(AgentProgressKind.SEARCH, "Needle", id="search"),
            pytest.param(AgentProgressKind.SHELL, "uv run pytest", id="shell"),
        ],
    )
    def test_report_delivers_an_action_update_with_its_kind_and_target(
        self, kind: AgentProgressKind, target: str
    ) -> None:
        updates: list[AgentProgressUpdate] = []

        _reporter(updates=updates).report(AgentProgress(kind=kind, target=target))

        assert updates == [AgentProgressUpdate(kind=kind, target=target)]

    def test_consecutive_identical_items_are_delivered_once(self) -> None:
        updates: list[AgentProgressUpdate] = []
        reporter = _reporter(updates=updates)
        read = AgentProgress(kind=AgentProgressKind.READ, target="src/x.py")
        edit = AgentProgress(kind=AgentProgressKind.EDIT, target="src/x.py")

        for item in (read, read, edit, read):
            reporter.report(item)

        assert _shown(updates) == [
            (AgentProgressKind.READ, "src/x.py"),
            (AgentProgressKind.EDIT, "src/x.py"),
            (AgentProgressKind.READ, "src/x.py"),
        ]

    def test_notes_are_held_until_the_next_action_then_reported_before_it(self, tmp_path: Path) -> None:
        updates: list[AgentProgressUpdate] = []
        reporter = _reporter(updates=updates, session_log_dir=tmp_path)

        reporter.report(AgentProgress(kind=AgentProgressKind.NOTE, target="Step 1: read it."))
        reporter.report(AgentProgress(kind=AgentProgressKind.NOTE, target="Using Bash instead."))

        assert updates == []

        reporter.report(AgentProgress(kind=AgentProgressKind.SHELL, target="ls"))

        assert _shown(updates) == [
            (AgentProgressKind.NOTE, "Step 1: read it."),
            (AgentProgressKind.NOTE, "Using Bash instead."),
            (AgentProgressKind.SHELL, "ls"),
        ]
        assert [(event.kind, event.target) for event in _logged_events(tmp_path)] == [
            ("note", "Step 1: read it."),
            ("note", "Using Bash instead."),
            ("shell", "ls"),
        ]

    def test_note_still_held_when_the_attempt_ends_is_never_reported(self, tmp_path: Path) -> None:
        updates: list[AgentProgressUpdate] = []
        reporter = _reporter(updates=updates, session_log_dir=tmp_path)

        reporter.report(AgentProgress(kind=AgentProgressKind.READ, target="x"))
        reporter.report(AgentProgress(kind=AgentProgressKind.NOTE, target="All done."))

        assert _shown(updates) == [(AgentProgressKind.READ, "x")]
        assert [event.kind for event in _logged_events(tmp_path)] == ["read"]

    def test_held_note_is_masked_with_the_step_redactor(self) -> None:
        secret = "QWERTYUIOPASDFGHJKLZ"
        updates: list[AgentProgressUpdate] = []
        reporter = _reporter(updates=updates, redactor=SecretRedactor([("TOKEN", secret)]))

        reporter.report(AgentProgress(kind=AgentProgressKind.NOTE, target=f"Using {secret} now."))
        reporter.report(AgentProgress(kind=AgentProgressKind.READ, target="x"))

        assert secret not in "".join(update.target for update in updates)

    def test_on_progress_failure_does_not_raise(self, tmp_path: Path) -> None:
        reporter = _reporter(raising=True, session_log_dir=tmp_path)

        assert reporter.report(AgentProgress(kind=AgentProgressKind.READ, target="x")) is None
        assert [event.target for event in _logged_events(tmp_path)] == ["x"]

    def test_seconds_since_last_report_is_none_until_the_first_item(self, monkeypatch: pytest.MonkeyPatch) -> None:
        readings = itertools.chain([100.0, 110.4], itertools.repeat(110.4))
        monkeypatch.setattr("dovo.engine.executors.agent_progress.monotonic", lambda: next(readings))
        reporter = _reporter()

        assert reporter.seconds_since_last_report() is None

        reporter.report(AgentProgress(kind=AgentProgressKind.READ, target="x"))

        assert reporter.seconds_since_last_report() == 10


class AgentProgressResultTests:
    def test_result_of_a_shown_action_delivers_a_capped_excerpt_with_the_call_kind_and_line_count(self) -> None:
        updates: list[AgentProgressUpdate] = []
        reporter = _reporter(updates=updates)

        reporter.report(AgentProgress(kind=AgentProgressKind.SHELL, target="ls", call_id="c1"))
        reporter.report(AgentProgress(kind=AgentProgressKind.RESULT, target="a\nb\nc\nd\ne", call_id="c1"))

        assert updates[1] == AgentProgressUpdate(
            kind=AgentProgressKind.RESULT, target="a\nb\nc", call_kind=AgentProgressKind.SHELL, line_count=5
        )

    def test_error_result_is_flagged_on_the_update(self) -> None:
        updates: list[AgentProgressUpdate] = []
        reporter = _reporter(updates=updates)

        reporter.report(AgentProgress(kind=AgentProgressKind.EDIT, target="x.py", call_id="c1"))
        reporter.report(AgentProgress(kind=AgentProgressKind.RESULT, target="denied", call_id="c1", is_error=True))

        assert updates[1] == AgentProgressUpdate(
            kind=AgentProgressKind.RESULT,
            target="denied",
            call_kind=AgentProgressKind.EDIT,
            line_count=1,
            is_error=True,
        )

    @pytest.mark.parametrize(
        "result_call_id",
        [
            pytest.param("unknown", id="call-never-reported"),
            pytest.param(None, id="result-without-call-id"),
        ],
    )
    def test_result_without_a_reported_action_is_dropped(self, result_call_id: str | None) -> None:
        updates: list[AgentProgressUpdate] = []
        reporter = _reporter(updates=updates)

        reporter.report(AgentProgress(kind=AgentProgressKind.READ, target="x", call_id="c1"))
        reporter.report(AgentProgress(kind=AgentProgressKind.RESULT, target="out", call_id=result_call_id))

        assert _shown(updates) == [(AgentProgressKind.READ, "x")]

    def test_result_of_an_adjacent_duplicate_action_is_dropped_but_the_first_one_is_kept(self) -> None:
        updates: list[AgentProgressUpdate] = []
        reporter = _reporter(updates=updates)

        reporter.report(AgentProgress(kind=AgentProgressKind.READ, target="x", call_id="c1"))
        reporter.report(AgentProgress(kind=AgentProgressKind.READ, target="x", call_id="c2"))
        reporter.report(AgentProgress(kind=AgentProgressKind.RESULT, target="first", call_id="c1"))
        reporter.report(AgentProgress(kind=AgentProgressKind.RESULT, target="second", call_id="c2"))

        assert _shown(updates) == [(AgentProgressKind.READ, "x"), (AgentProgressKind.RESULT, "first")]

    def test_same_action_run_again_after_its_result_is_shown_again(self) -> None:
        updates: list[AgentProgressUpdate] = []
        reporter = _reporter(updates=updates)

        reporter.report(AgentProgress(kind=AgentProgressKind.SHELL, target="pytest", call_id="c1"))
        reporter.report(AgentProgress(kind=AgentProgressKind.RESULT, target="1 failed", call_id="c1"))
        reporter.report(AgentProgress(kind=AgentProgressKind.SHELL, target="pytest", call_id="c2"))
        reporter.report(AgentProgress(kind=AgentProgressKind.RESULT, target="1 passed", call_id="c2"))

        assert _shown(updates) == [
            (AgentProgressKind.SHELL, "pytest"),
            (AgentProgressKind.RESULT, "1 failed"),
            (AgentProgressKind.SHELL, "pytest"),
            (AgentProgressKind.RESULT, "1 passed"),
        ]

    def test_result_is_masked_and_each_line_truncated(self) -> None:
        secret = "QWERTYUIOPASDFGHJKLZ"
        updates: list[AgentProgressUpdate] = []
        reporter = _reporter(updates=updates, redactor=SecretRedactor([("TOKEN", secret)]))

        reporter.report(AgentProgress(kind=AgentProgressKind.SHELL, target="env", call_id="c1"))
        reporter.report(
            AgentProgress(kind=AgentProgressKind.RESULT, target=f"TOKEN={secret}\n{'x' * 300}", call_id="c1")
        )

        first_line, second_line = updates[1].target.splitlines()
        assert secret not in first_line
        assert second_line == "x" * 119 + "…"

    def test_result_is_not_appended_to_the_session_log(self, tmp_path: Path) -> None:
        reporter = _reporter(session_log_dir=tmp_path)

        reporter.report(AgentProgress(kind=AgentProgressKind.READ, target="x", call_id="c1"))
        reporter.report(AgentProgress(kind=AgentProgressKind.RESULT, target="out", call_id="c1"))

        assert [event.kind for event in _logged_events(tmp_path)] == ["read"]

    def test_claude_fixture_replay_reports_each_action_with_its_result_and_drops_the_closing_message(self) -> None:
        updates: list[AgentProgressUpdate] = []
        reporter = _reporter(updates=updates)

        for line in load_agent_fixture("claude", "implementing_edit"):
            for progress in map_claude_line(line, worktree_path=Path("/work")):
                reporter.report(progress)

        assert [(update.kind, update.call_kind, update.is_error) for update in updates] == [
            (AgentProgressKind.SHELL, None, False),
            (AgentProgressKind.RESULT, AgentProgressKind.SHELL, False),
            (AgentProgressKind.EDIT, None, False),
            (AgentProgressKind.RESULT, AgentProgressKind.EDIT, True),
        ]
        assert updates[0].target == "cat /work/calc.py; ls -la /work/"
        assert updates[2].target == "calc.py"


class AgentProgressSessionLogTests:
    def test_report_appends_one_event_per_item_with_step_and_loop_fields(self, tmp_path: Path) -> None:
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
        secret = "hunter2-hunter2"
        reporter = _reporter(session_log_dir=tmp_path, redactor=SecretRedactor([("TOKEN", secret)]))

        reporter.report(AgentProgress(kind=AgentProgressKind.SHELL, target=f"curl -H {secret}"))

        (event,) = _logged_events(tmp_path)
        assert event.target is not None
        assert "[REDACTED" in event.target
        assert secret not in event.target

    def test_report_without_a_session_log_dir_writes_no_event(self) -> None:
        updates: list[AgentProgressUpdate] = []

        result = _reporter(updates=updates).report(AgentProgress(kind=AgentProgressKind.READ, target="x"))

        assert result is None
        assert updates == [AgentProgressUpdate(kind=AgentProgressKind.READ, target="x")]

    def test_report_masks_a_step_only_secret_before_truncating_the_target(self, tmp_path: Path) -> None:
        secret = "QWERTYUIOPASDFGHJKLZ"
        redactor = SecretRedactor([("TOKEN", secret)])
        updates: list[AgentProgressUpdate] = []
        reporter = _reporter(updates=updates, session_log_dir=tmp_path, redactor=redactor)
        command = "a" * 110 + secret + "b" * 100
        expected = redactor.redact_text(command)[:119] + "…"

        reporter.report(AgentProgress(kind=AgentProgressKind.SHELL, target=command))

        (event,) = _logged_events(tmp_path)
        assert event.target == expected
        assert secret[:4] not in expected
        assert updates == [AgentProgressUpdate(kind=AgentProgressKind.SHELL, target=expected)]

    def test_report_truncates_a_long_target_to_119_characters_plus_ellipsis(self, tmp_path: Path) -> None:
        reporter = _reporter(session_log_dir=tmp_path)

        reporter.report(AgentProgress(kind=AgentProgressKind.SHELL, target="x" * 300))

        (event,) = _logged_events(tmp_path)
        assert event.target == "x" * 119 + "…"
