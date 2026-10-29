"""Tier 1 contract: scripts/session_pi.py extracts the facts /dovo-pi analyses a dovo session from."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType

import pytest

from tests.lint.astlib import REPO_ROOT

SESSION = "abc123"
TRACEBACK_LINE = "Traceback (most recent call last):"


def _load_script(name: str, module_name: str) -> ModuleType:
    """Load scripts/<name>.py by path as module_name, since scripts/ is not a package."""
    spec = importlib.util.spec_from_file_location(module_name, REPO_ROOT / "scripts" / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_load_script("publish_review", "publish_review")
PI = _load_script("session_pi", "session_pi_under_test")


def _use(call_id: str, name: str, **tool_input: object) -> dict[str, object]:
    """Return a top-level assistant event with one tool_use block."""
    block = {"type": "tool_use", "id": call_id, "name": name, "input": tool_input}
    return {"type": "assistant", "message": {"id": call_id, "content": [block]}}


def _response(message_id: str, calls: Sequence[tuple[str, Mapping[str, object]]]) -> list[dict[str, object]]:
    """Return one model response as Claude Code streams it: a text event, then one event per tool_use block."""
    text: dict[str, object] = {
        "type": "assistant",
        "message": {"id": message_id, "content": [{"type": "text", "text": "Working."}]},
    }
    uses: list[dict[str, object]] = [
        {
            "type": "assistant",
            "message": {
                "id": message_id,
                "content": [{"type": "tool_use", "id": f"{message_id}{n}", "name": name, "input": tool_input}],
            },
        }
        for n, (name, tool_input) in enumerate(calls)
    ]
    return [text, *uses]


def _result(call_id: str, *, error: bool = False) -> dict[str, object]:
    """Return a user event with the tool_result of call_id."""
    block = {"type": "tool_result", "tool_use_id": call_id, "is_error": error}
    return {"type": "user", "message": {"content": [block]}}


def _final(**extra: object) -> dict[str, object]:
    """Return the stream's result event."""
    return {
        "type": "result",
        "duration_ms": 9000,
        "duration_api_ms": 7500,
        "total_cost_usd": 0.5,
        "usage": {"input_tokens": 10, "output_tokens": 20},
        **extra,
    }


def _write_log(path: Path, events: Sequence[Mapping[str, object]]) -> None:
    """Write events as JSON lines, preceded by one noise line that is not JSON."""
    path.write_text("\n".join(["plain noise", *(json.dumps(e) for e in events)]) + "\n", encoding="utf-8")


def _step(step_id: str, **fields: object) -> dict[str, object]:
    """Return a payload step entry."""
    return {
        "kind": "step",
        "step_id": step_id,
        "state": "completed",
        "attempts": 1,
        "duration_seconds": 12.0,
        "error_message": None,
        "rejected_patch_path": None,
        "loop_id": None,
        "iteration": None,
        **fields,
    }


def _payload(steps: list[dict[str, object]], **session: object) -> dict[str, object]:
    """Return a `dovo session show` payload around steps."""
    return {
        "session_id": SESSION,
        "session": {
            "session_id": SESSION,
            "blueprint_name": "demo",
            "status": "completed",
            "duration_seconds": 100.0,
            "error_message": None,
            **session,
        },
        "steps": steps,
        "worktree_kept": False,
        "errors": [],
        "warnings": [],
        "remediations": [],
        "diagnostics": [],
    }


def _flags(result: dict[str, object], kind: str) -> list[dict[str, object]]:
    """Return the flags of result with the given kind."""
    flags = result["flags"]
    assert isinstance(flags, list)
    return [flag for flag in flags if flag["kind"] == kind]


def _evidence(flag: dict[str, object]) -> list[dict[str, object]]:
    """Return the evidence entries of flag."""
    evidence = flag["evidence"]
    assert isinstance(evidence, list)
    return evidence


class TestStepRows:
    def test_agent_step_reports_turns_calls_cost_and_top_tools(self, tmp_path: Path) -> None:
        events = [
            {"type": "system", "subtype": "init"},
            _use("a", "Read", file_path="/x/a.py"),
            _result("a"),
            _use("b", "Bash", command="ls"),
            _result("b"),
            _use("sub", "Bash", command="hidden") | {"parent_tool_use_id": "a"},
            *_response("c", [("Read", {}), ("Grep", {})]),
            _final(),
        ]
        _write_log(tmp_path / "01_code_attempt_1.stdout.log", events)

        result = PI.analyse(_payload([_step("code", duration_seconds=30.5)]), tmp_path)

        assert result["steps"] == [
            {
                "id": "code",
                "step_id": "code",
                "loop_id": None,
                "iteration": None,
                "type": "claude",
                "status": "completed",
                "attempts": 1,
                "wall_seconds": 30.5,
                "model_seconds": 7.5,
                "turns": 3,
                "tool_calls": 4,
                "calls_per_turn": 1.33,
                "cost_usd": 0.5,
                "top_tools": [["Read", 2], ["Bash", 1], ["Grep", 1]],
                "error_message": None,
            }
        ]
        assert result["session"]["total_cost_usd"] == 0.5
        assert result["session"]["tokens"] == {
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "input_tokens": 10,
            "output_tokens": 20,
        }

    def test_events_of_one_response_count_as_one_turn(self, tmp_path: Path) -> None:
        calls = [("Read", {"file_path": "/x/a.py"}), ("Read", {"file_path": "/x/b.py"}), ("Grep", {"pattern": "x"})]
        events = _response("m1", calls)
        _write_log(tmp_path / "01_code_attempt_1.stdout.log", [events[0], events[1], _result("m10"), *events[2:]])

        row = PI.analyse(_payload([_step("code")]), tmp_path)["steps"][0]

        assert (row["turns"], row["tool_calls"], row["calls_per_turn"]) == (1, 3, 3.0)

    def test_loop_iterations_get_their_own_rows(self, tmp_path: Path) -> None:
        for iteration in (1, 2):
            _write_log(tmp_path / f"01_fix_iter_{iteration}_attempt_1.stdout.log", [_use("a", "Bash"), _final()])
        steps = [_step("fix", loop_id="loop", iteration=1), _step("fix", loop_id="loop", iteration=2)]

        result = PI.analyse(_payload(steps), tmp_path)

        assert [(row["id"], row["turns"]) for row in result["steps"]] == [("fix#1", 1), ("fix#2", 1)]

    def test_step_without_a_recognised_log_has_no_agent_metrics(self, tmp_path: Path) -> None:
        (tmp_path / "01_build_attempt_1.stdout.log").write_text("copilot says hello\n", encoding="utf-8")

        result = PI.analyse(_payload([_step("build"), _step("lint")]), tmp_path)

        assert [(row["type"], row["turns"], row["cost_usd"]) for row in result["steps"]] == [
            ("other", None, None),
            (None, None, None),
        ]

    def test_without_a_log_directory_rows_come_from_the_payload_alone(self) -> None:
        result = PI.analyse(_payload([_step("build")]), None)

        assert [row["id"] for row in result["steps"]] == ["build"]
        assert result["flags"] == []


class TestFlags:
    def test_single_call_turns_are_flagged_as_unbatched(self, tmp_path: Path) -> None:
        events = [_use(f"t{n}", "Bash", command=f"echo {n}") for n in range(10)]
        _write_log(tmp_path / "01_code_attempt_1.stdout.log", [*events, _final()])

        flags = _flags(PI.analyse(_payload([_step("code")]), tmp_path), "unbatched_calls")

        assert [(f["step"], f["detail"]) for f in flags] == [("code", "10 calls in 10 turns")]
        assert flags[0]["evidence"] == [{"file": "01_code_attempt_1.stdout.log", "line": 2}]

    def test_unbatched_evidence_is_the_first_tool_use_event_of_the_response(self, tmp_path: Path) -> None:
        events = [event for n in range(10) for event in _response(f"t{n}", [("Bash", {"command": "ls"})])]
        _write_log(tmp_path / "01_code_attempt_1.stdout.log", [*events, _final()])

        flags = _flags(PI.analyse(_payload([_step("code")]), tmp_path), "unbatched_calls")

        assert [f["detail"] for f in flags] == ["10 calls in 10 turns"]
        assert flags[0]["evidence"] == [{"file": "01_code_attempt_1.stdout.log", "line": 3}]

    def test_batched_responses_streamed_one_block_per_event_are_not_flagged(self, tmp_path: Path) -> None:
        events = [event for n in range(10) for event in _response(f"t{n}", [("Read", {}), ("Read", {})])]
        _write_log(tmp_path / "01_code_attempt_1.stdout.log", [*events, _final()])

        assert _flags(PI.analyse(_payload([_step("code")]), tmp_path), "unbatched_calls") == []

    def test_consecutive_single_edits_to_one_file_are_flagged_once_per_log(self, tmp_path: Path) -> None:
        events = [_use(f"e{n}", "Edit", file_path="/x/a.py") for n in range(3)]
        events += [_use("r", "Read", file_path="/x/a.py")]
        events += [_use(f"f{n}", "Edit", file_path="/x/b.py") for n in range(4)]
        events += [_use("g", "Edit", file_path="/x/c.py"), _use("h", "Edit", file_path="/x/d.py")]
        _write_log(tmp_path / "01_code_attempt_1.stdout.log", events)

        flags = _flags(PI.analyse(_payload([_step("code")]), tmp_path), "serial_edits")

        assert len(flags) == 1
        assert flags[0]["detail"] == "2 runs of single-call edits; longest 4 to /x/b.py"
        assert [e["edits"] for e in _evidence(flags[0])] == [4, 3]

    def test_edits_made_in_one_response_are_not_serial(self, tmp_path: Path) -> None:
        edit = ("Edit", {"file_path": "/x/a.py"})
        events = [*_response("m1", [edit, edit, edit]), *_response("m2", [edit]), *_response("m3", [edit])]
        _write_log(tmp_path / "01_code_attempt_1.stdout.log", events)

        assert _flags(PI.analyse(_payload([_step("code")]), tmp_path), "serial_edits") == []

    def test_file_read_in_three_steps_is_flagged(self, tmp_path: Path) -> None:
        for index, name in enumerate(("a", "b", "c"), start=1):
            _write_log(tmp_path / f"0{index}_{name}_attempt_1.stdout.log", [_use("r", "Read", file_path="/x/z.py")])
        steps = [_step("a"), _step("b"), _step("c")]

        flags = _flags(PI.analyse(_payload(steps), tmp_path), "repeated_reads")

        assert [f["detail"] for f in flags] == ["/x/z.py read in 3 steps"]
        assert [e["step"] for e in _evidence(flags[0])] == ["a", "b", "c"]

    def test_failed_tool_results_and_repeated_failing_commands(self, tmp_path: Path) -> None:
        events = [
            _use("a", "Bash", command="make"),
            _result("a", error=True),
            _use("b", "Bash", command="make"),
            _result("b", error=True),
            _use("c", "Bash", command="ls"),
            _result("c"),
        ]
        _write_log(tmp_path / "01_code_attempt_1.stdout.log", events)

        result = PI.analyse(_payload([_step("code")]), tmp_path)

        assert [f["detail"] for f in _flags(result, "failed_tool_results")] == ["2 failed tool results"]
        repeated = _flags(result, "repeated_failing_command")
        assert [f["detail"] for f in repeated] == ["failed 2 times: make"]
        assert repeated[0]["evidence"] == [
            {"file": "01_code_attempt_1.stdout.log", "line": 3},
            {"file": "01_code_attempt_1.stdout.log", "line": 5},
        ]

    def test_permission_denials_are_flagged(self, tmp_path: Path) -> None:
        denied = [{"tool_name": "Bash"}, {"tool_name": "Write"}]
        _write_log(tmp_path / "01_code_attempt_1.stdout.log", [_final(permission_denials=denied)])

        flags = _flags(PI.analyse(_payload([_step("code")]), tmp_path), "permission_denials")

        assert [f["detail"] for f in flags] == ["2 denied: Bash, Write"]

    def test_tracebacks_in_stderr_are_flagged_with_their_line(self, tmp_path: Path) -> None:
        (tmp_path / "02_code_attempt_1.stderr.log").write_text(f"noise\n{TRACEBACK_LINE}\n  boom\n", encoding="utf-8")

        flags = _flags(PI.analyse(_payload([_step("code")]), tmp_path), "stderr_traceback")

        assert flags[0]["evidence"] == [{"file": "02_code_attempt_1.stderr.log", "line": 2, "step": "code"}]

    def test_retries_failures_and_rejected_patches_come_from_the_step_results(self) -> None:
        steps = [
            _step("push", attempts=3),
            _step("land", state="failed", error_message="Command failed with exit code 1."),
            _step("fix", rejected_patch_path="/p/rejected.patch"),
        ]

        result = PI.analyse(_payload(steps, status="failed", error_message="Step 'land' failed."), None)

        assert [f["detail"] for f in _flags(result, "retries")] == ["3 attempts"]
        assert [f["detail"] for f in _flags(result, "step_not_completed")] == [
            "failed: Command failed with exit code 1."
        ]
        assert [f["detail"] for f in _flags(result, "rejected_patch")] == ["/p/rejected.patch"]
        assert [f["detail"] for f in _flags(result, "session_not_completed")] == ["failed: Step 'land' failed."]

    def test_extra_session_starts_count_as_resumes(self, tmp_path: Path) -> None:
        started = {"event": "session_started"}
        _write_log(tmp_path / "session.log", [started, {"event": "session_completed"}, started])

        result = PI.analyse(_payload([]), tmp_path)

        assert result["session"]["resumes"] == 1
        assert [f["detail"] for f in _flags(result, "resumed")] == ["1 resumes"]

    def test_only_workaround_notes_are_flagged(self, tmp_path: Path) -> None:
        def note(text: str, kind: str = "note") -> dict[str, object]:
            return {"event": "agent_progress", "kind": kind, "step_id": "code", "target": text, "ts": "T"}

        events = [
            note("Next I will debug the parser."),
            note("The CLI doesn't support this flag, so I will do it manually."),
            note("manually editing", kind="shell"),
        ]
        _write_log(tmp_path / "session.log", events)

        flags = _flags(PI.analyse(_payload([_step("code")]), tmp_path), "workaround_note")

        assert [(f["step"], f["evidence"]) for f in flags] == [
            ("code", [{"file": "session.log", "line": 3, "ts": "T"}])
        ]


class TestFingerprint:
    @pytest.mark.parametrize(
        ("first", "second"),
        [
            ("failed in /tmp/a/b.py at 3f2a9c1", "failed in /home/x/c.py at 9e8d7c6"),
            ("Exit 12 after 30 tries", "exit 7 after 4 tries"),
        ],
    )
    def test_paths_shas_and_numbers_do_not_change_the_fingerprint(self, first: str, second: str) -> None:
        assert PI.fingerprint("land", "failed", first) == PI.fingerprint("land", "failed", second)

    def test_step_and_code_change_the_fingerprint(self) -> None:
        base = PI.fingerprint("land", "failed", "boom")

        assert base == PI.fingerprint("land", "failed", "boom")
        assert base != PI.fingerprint("push", "failed", "boom")
        assert base != PI.fingerprint("land", "ignored", "boom")
        assert len(base) == 12


class TestMain:
    def test_main_writes_the_analysis_for_a_session(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        logs = tmp_path / "logs"
        logs.mkdir()
        _write_log(logs / "01_code_attempt_1.stdout.log", [_use("a", "Bash"), _final()])
        shown = {
            "event_type": "SessionShowResult",
            "payload": {**_payload([_step("code")]), "log_files": [str(logs / "x.log")]},
        }
        commands: list[tuple[str, ...]] = []

        def fake_run(*args: str) -> str:
            commands.append(args)
            return json.dumps(shown)

        monkeypatch.setattr(PI, "run", fake_run)
        out = tmp_path / "pi" / "out.json"
        monkeypatch.setattr(sys, "argv", ["session_pi.py", SESSION, "--out", str(out)])

        assert PI.main() == 0

        assert commands == [("uv", "run", "dovo", "session", "show", SESSION, "--logs", "--format", "json")]
        written = json.loads(out.read_text(encoding="utf-8"))
        assert written["session"]["id"] == SESSION
        assert [(row["id"], row["tool_calls"]) for row in written["steps"]] == [("code", 1)]
        assert capsys.readouterr().out == ""

    def test_main_prints_json_for_the_latest_session_by_default(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        commands: list[tuple[str, ...]] = []

        def fake_run(*args: str) -> str:
            commands.append(args)
            return json.dumps({"payload": _payload([])})

        monkeypatch.setattr(PI, "run", fake_run)
        monkeypatch.setattr(sys, "argv", ["session_pi.py"])

        assert PI.main() == 0

        assert commands == [("uv", "run", "dovo", "session", "show", "--logs", "--format", "json")]
        assert json.loads(capsys.readouterr().out)["session"]["blueprint"] == "demo"

    def test_main_reports_a_session_it_cannot_read(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(PI, "run", lambda *args: json.dumps({"payload": None}))
        monkeypatch.setattr(sys, "argv", ["session_pi.py", SESSION])

        assert PI.main() == 1
        assert "could not read the session" in capsys.readouterr().err
