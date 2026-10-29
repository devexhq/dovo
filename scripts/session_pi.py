#!/usr/bin/env python3
"""Extracts the measurable facts the /dovo-pi skill analyses a dovo session from.

Reads `dovo session show <id> --logs --format json` for the blueprint, outcome
and step results, then the session's own log directory: `session.log` for
resumes and agent notes, `NN_<step>[_iter_K]_attempt_N.stdout.log` for the
Claude Code stream-json each agent step wrote, and the matching `.stderr.log`.
Prints one JSON document (or writes it to --out) holding the session summary,
one row per step and the flags that point at evidence. It makes no judgement:
every number comes from a log, and every flag names the file and line to read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from publish_review import run

MIN_TURNS_FOR_BATCHING = 10
MAX_UNBATCHED_RATIO = 1.1
MIN_SERIAL_EDITS = 3
MIN_READ_STEPS = 3
MAX_EVIDENCE = 5
EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
TARGET_KEYS = ("file_path", "command", "skill", "pattern", "url", "description")
TARGET_WIDTH = 160
TRACEBACK = "Traceback (most recent call last)"
WORKAROUND = re.compile(r"doesn't support|does not support|work around|workaround|manually|\bbugs?\b", re.IGNORECASE)
STREAM_TYPES = frozenset({"system", "assistant", "user", "result"})
DONE_STATES = frozenset({"completed", "skipped"})
LOG_NAME = re.compile(
    r"^\d+_(?P<step>.+?)(?:_iter_(?P<iteration>\d+))?_attempt_(?P<attempt>\d+)\.(?P<stream>stdout|stderr)\.log$"
)
PATH_TEXT = re.compile(r"(?:~|\.{0,2})/[^\s'\"]+")
SHA_TEXT = re.compile(r"\b[0-9a-f]{7,40}\b")
NUMBER_TEXT = re.compile(r"\d+")

Evidence = dict[str, object]
Flag = dict[str, object]


@dataclass
class Call:
    """One top-level tool call of an agent turn, with the log line of its tool_use event."""

    name: str
    target: str
    file_path: str | None
    line: int


@dataclass
class Turn:
    """One model response that carried tool calls, with the log line of its first tool_use event."""

    line: int
    calls: list[Call]


@dataclass
class FailedCall:
    """A tool call whose tool_result came back with is_error."""

    name: str
    target: str
    line: int


@dataclass
class AgentLog:
    """What one step attempt's Claude Code stream-json holds."""

    file: str
    step: str
    iteration: int | None
    attempt: int
    turns: list[Turn] = field(default_factory=list)
    failed: list[FailedCall] = field(default_factory=list)
    result: dict[str, object] = field(default_factory=dict)

    @property
    def calls(self) -> int:
        """Return the number of top-level tool calls."""
        return sum(len(turn.calls) for turn in self.turns)


def describe(tool_input: dict[str, object]) -> tuple[str, str | None]:
    """Return a one-line target for a tool call's input, and its file_path when it has one."""
    file_path = tool_input.get("file_path")
    for key in TARGET_KEYS:
        value = tool_input.get(key)
        if value:
            return str(value).replace("\n", " ⏎ ")[:TARGET_WIDTH], str(file_path) if file_path else None
    return "", None


def read_events(path: Path) -> list[tuple[int, dict[str, object]]]:
    """Return the JSON object lines of path with their 1-based line numbers, skipping anything else."""
    events = []
    for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append((number, event))
    return events


def _content(event: dict[str, object]) -> list[dict[str, object]]:
    """Return the content blocks of a stream-json event's message."""
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return [block for block in content if isinstance(block, dict)] if isinstance(content, list) else []


def _calls(number: int, event: dict[str, object], uses: dict[str, Call]) -> list[Call]:
    """Return the tool calls of an assistant event, registering them in uses."""
    calls = []
    for block in _content(event):
        if block.get("type") != "tool_use":
            continue
        tool_input = block.get("input")
        target, file_path = describe(tool_input if isinstance(tool_input, dict) else {})
        call = Call(str(block.get("name")), target, file_path, number)
        calls.append(call)
        uses[str(block.get("id"))] = call
    return calls


def _message_id(event: dict[str, object]) -> str | None:
    """Return the message.id of a stream-json event, or None when it has none."""
    message = event.get("message")
    value = message.get("id") if isinstance(message, dict) else None
    return str(value) if value else None


def _failures(number: int, event: dict[str, object], uses: dict[str, Call]) -> list[FailedCall]:
    """Return the failed tool results of a user event, matched to their calls."""
    failed = []
    for block in _content(event):
        used = uses.get(str(block.get("tool_use_id")))
        if block.get("type") == "tool_result" and block.get("is_error") and used is not None:
            failed.append(FailedCall(used.name, used.target, number))
    return failed


def parse_agent_log(path: Path, step: str, iteration: int | None, attempt: int) -> AgentLog | None:
    """Return the parsed stream-json of path, or None when it holds no Claude Code events.

    Claude Code emits each content block of a response as its own assistant event, so events sharing a
    message.id form one turn. Subagent turns (those with a parent_tool_use_id) are left out.
    """
    log = AgentLog(path.name, step, iteration, attempt)
    uses: dict[str, Call] = {}
    responses: dict[str, Turn] = {}
    events = read_events(path)
    for number, event in events:
        _apply_event(log, number, event, uses, responses)
    return log if any(event.get("type") in STREAM_TYPES for _, event in events) else None


def _apply_event(
    log: AgentLog,
    number: int,
    event: dict[str, object],
    uses: dict[str, Call],
    responses: dict[str, Turn],
) -> None:
    """Add one stream-json event to log: the result, top-level tool calls, or failed tool results."""
    kind = event.get("type")
    if kind == "result":
        log.result = event
    if event.get("parent_tool_use_id"):
        return
    if kind == "assistant":
        _add_calls(log, number, event, _calls(number, event, uses), responses)
    if kind == "user":
        log.failed.extend(_failures(number, event, uses))


def _add_calls(
    log: AgentLog, number: int, event: dict[str, object], calls: list[Call], responses: dict[str, Turn]
) -> None:
    """Add calls to the turn of the event's response, opening that turn on its first tool_use event."""
    if not calls:
        return
    message_id = _message_id(event)
    turn = responses.get(message_id) if message_id else None
    if turn is not None:
        turn.calls.extend(calls)
        return
    turn = Turn(number, calls)
    log.turns.append(turn)
    if message_id:
        responses[message_id] = turn


def scan_logs(log_dir: Path) -> tuple[list[AgentLog], dict[tuple[str, int | None, int], str], list[Evidence]]:
    """Return the agent logs, the stdout/stderr file names without agent events, and tracebacks found in stderr.

    The second item maps (step, iteration, attempt) to a stdout file that is not stream-json.
    """
    agents: list[AgentLog] = []
    other: dict[tuple[str, int | None, int], str] = {}
    tracebacks: list[Evidence] = []
    for path in sorted(log_dir.glob("*.log")):
        match = LOG_NAME.match(path.name)
        if match is None:
            continue
        iteration = int(match["iteration"]) if match["iteration"] else None
        key = (match["step"], iteration, int(match["attempt"]))
        if match["stream"] == "stderr":
            tracebacks.extend(_traceback_evidence(path, match["step"], iteration))
            continue
        parsed = parse_agent_log(path, *key)
        if parsed is None:
            other[key] = path.name
        else:
            agents.append(parsed)
    return agents, other, tracebacks


def _traceback_evidence(path: Path, step: str, iteration: int | None) -> list[Evidence]:
    """Return one evidence entry per traceback in the stderr log path."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return [
        {"file": path.name, "line": number, "step": row_id(step, iteration)}
        for number, line in enumerate(lines, start=1)
        if TRACEBACK in line
    ]


def row_id(step: str, iteration: int | None) -> str:
    """Return the id of a step row: the step id, suffixed with its loop iteration."""
    return step if iteration is None else f"{step}#{iteration}"


def normalise(message: str) -> str:
    """Return message with paths, shas and numbers replaced, so one failure hashes alike across runs."""
    text = PATH_TEXT.sub("<path>", message.lower())
    text = SHA_TEXT.sub("<sha>", text)
    return " ".join(NUMBER_TEXT.sub("<n>", text).split())


def fingerprint(step: str, code: str, message: str) -> str:
    """Return the 12-hex-digit fingerprint of a step id, error code and normalised message."""
    return hashlib.sha256(f"{step}|{code}|{normalise(message)}".encode()).hexdigest()[:12]


def make_flag(kind: str, step: str | None, detail: str, evidence: list[Evidence], code: str | None = None) -> Flag:
    """Return a flag with its fingerprint, keeping at most MAX_EVIDENCE evidence entries."""
    step_id = (step or "").split("#")[0]
    return {
        "kind": kind,
        "step": step,
        "detail": detail,
        "evidence": evidence[:MAX_EVIDENCE],
        "fingerprint": fingerprint(step_id, code or kind, detail),
    }


def _number(value: object) -> float:
    """Return value as a float, or 0.0 when it is not a number."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _usage(logs: list[AgentLog]) -> dict[str, int]:
    """Return the token usage summed over the logs' result events."""
    total: Counter[str] = Counter()
    for log in logs:
        usage = log.result.get("usage")
        for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            total[key] += int(_number(usage.get(key))) if isinstance(usage, dict) else 0
    return {key: total[key] for key in sorted(total)}


def step_row(step: dict[str, object], logs: list[AgentLog], other: list[str]) -> dict[str, object]:
    """Return the timeline row of a payload step, with the metrics of its agent logs."""
    return {
        "id": step_label(step),
        "step_id": step.get("step_id"),
        "loop_id": step.get("loop_id"),
        "iteration": step_iteration(step),
        "type": "claude" if logs else ("other" if other else None),
        "status": step.get("state"),
        "attempts": step.get("attempts"),
        "wall_seconds": step.get("duration_seconds"),
        **agent_metrics(logs),
        "error_message": step.get("error_message"),
    }


def agent_metrics(logs: list[AgentLog]) -> dict[str, object]:
    """Return the model time, turns, calls, cost and top tools summed over a step's agent logs (None without logs)."""
    if not logs:
        return {
            "model_seconds": None,
            "turns": None,
            "tool_calls": None,
            "calls_per_turn": None,
            "cost_usd": None,
            "top_tools": [],
        }
    turns = sum(len(log.turns) for log in logs)
    calls = sum(log.calls for log in logs)
    tools = Counter(call.name for log in logs for turn in log.turns for call in turn.calls)
    return {
        "model_seconds": round(sum(_number(log.result.get("duration_api_ms")) for log in logs) / 1000, 1),
        "turns": turns,
        "tool_calls": calls,
        "calls_per_turn": round(calls / turns, 2) if turns else None,
        "cost_usd": round(sum(_number(log.result.get("total_cost_usd")) for log in logs), 4),
        "top_tools": [[name, count] for name, count in tools.most_common(3)],
    }


def step_iteration(step: dict[str, object]) -> int | None:
    """Return the loop iteration of a payload step, or None outside a loop."""
    iteration = step.get("iteration")
    return iteration if isinstance(iteration, int) else None


def step_label(step: dict[str, object]) -> str:
    """Return the row id of a payload step."""
    return row_id(str(step.get("step_id")), step_iteration(step))


def payload_steps(payload: dict[str, object]) -> list[dict[str, object]]:
    """Return the payload's step entries, leaving out loops and anything that is not an object."""
    raw = payload.get("steps")
    return (
        [step for step in raw if isinstance(step, dict) and step.get("kind") == "step"] if isinstance(raw, list) else []
    )


def read_session_log(log_dir: Path) -> tuple[list[tuple[int, dict[str, object]]], str]:
    """Return the events of the session's session.log and its file name, or no events when it is missing."""
    path = log_dir / "session.log"
    return (read_events(path) if path.exists() else []), path.name


def session_summary(
    payload: dict[str, object], events: list[tuple[int, dict[str, object]]], logs: list[AgentLog]
) -> dict[str, object]:
    """Return the session id, blueprint, outcome, wall time, resume count and total cost and tokens."""
    session = payload.get("session")
    session = session if isinstance(session, dict) else {}
    starts = sum(1 for _, event in events if event.get("event") == "session_started")
    return {
        "id": payload.get("session_id") or session.get("session_id"),
        "blueprint": session.get("blueprint_name"),
        "status": session.get("status"),
        "started_at": session.get("started_at"),
        "completed_at": session.get("completed_at"),
        "wall_seconds": session.get("duration_seconds"),
        "error_message": session.get("error_message"),
        "worktree_kept": payload.get("worktree_kept"),
        "resumes": max(0, starts - 1),
        "total_cost_usd": round(sum(_number(log.result.get("total_cost_usd")) for log in logs), 4),
        "tokens": _usage(logs),
    }


def log_flags(log: AgentLog) -> list[Flag]:
    """Return the flags one agent log raises: batching, serial edits, failed tool results and permission denials."""
    step = row_id(log.step, log.iteration)
    flags = [*_unbatched(log, step), *_serial_edits(log, step), *_failed_results(log, step)]
    denials = log.result.get("permission_denials")
    if isinstance(denials, list) and denials:
        names = sorted({str(d.get("tool_name")) for d in denials if isinstance(d, dict)})
        flags.append(
            make_flag("permission_denials", step, f"{len(denials)} denied: {', '.join(names)}", [{"file": log.file}])
        )
    return flags


def _unbatched(log: AgentLog, step: str) -> list[Flag]:
    """Return a flag when the log averages at most MAX_UNBATCHED_RATIO calls per turn over enough turns."""
    turns = len(log.turns)
    if turns < MIN_TURNS_FOR_BATCHING or log.calls / turns > MAX_UNBATCHED_RATIO:
        return []
    evidence = [{"file": log.file, "line": log.turns[0].line}]
    return [make_flag("unbatched_calls", step, f"{log.calls} calls in {turns} turns", evidence)]


def _single_edit(turn: Turn) -> str | None:
    """Return the file a turn edited when its only call is an edit, else None."""
    only = turn.calls[0] if len(turn.calls) == 1 else None
    return only.file_path if only is not None and only.name in EDIT_TOOLS else None


def _edit_runs(turns: list[Turn]) -> list[tuple[str, list[Turn]]]:
    """Return the runs of MIN_SERIAL_EDITS or more consecutive single-call edits to one file, with the file."""
    runs: list[tuple[str, list[Turn]]] = []
    current: list[Turn] = []
    previous: str | None = None
    for turn in [*turns, Turn(0, [])]:
        target = _single_edit(turn)
        if target is not None and target == previous:
            current.append(turn)
            continue
        if len(current) >= MIN_SERIAL_EDITS and previous is not None:
            runs.append((previous, current))
        current = [turn] if target is not None else []
        previous = target
    return runs


def _serial_edits(log: AgentLog, step: str) -> list[Flag]:
    """Return one flag for the log's runs of consecutive single-call edits to the same file, longest run first."""
    runs = sorted(_edit_runs(log.turns), key=lambda item: -len(item[1]))
    if not runs:
        return []
    path, longest = runs[0]
    detail = f"{len(runs)} runs of single-call edits; longest {len(longest)} to {path}"
    evidence = [
        {"file": log.file, "line": turns[0].line, "edits": len(turns), "path": target} for target, turns in runs
    ]
    return [make_flag("serial_edits", step, detail, evidence)]


def _failed_results(log: AgentLog, step: str) -> list[Flag]:
    """Return a flag for the failed tool results of the log and one per command that failed more than once."""
    if not log.failed:
        return []
    evidence = [{"file": log.file, "line": f.line, "call": f"{f.name} {f.target}"} for f in log.failed]
    flags = [make_flag("failed_tool_results", step, f"{len(log.failed)} failed tool results", evidence)]
    return [*flags, *_repeated_commands(log, step)]


def _repeated_commands(log: AgentLog, step: str) -> list[Flag]:
    """Return a flag per Bash command that failed at least twice in the log."""
    commands = Counter(f.target for f in log.failed if f.name == "Bash")
    return [
        make_flag(
            "repeated_failing_command",
            step,
            f"failed {count} times: {command}",
            [{"file": log.file, "line": f.line} for f in log.failed if f.target == command],
        )
        for command, count in commands.items()
        if count >= 2
    ]


def _reads(log: AgentLog) -> list[tuple[str, int]]:
    """Return the (file, line) of every Read call in the log."""
    return [
        (call.file_path, call.line)
        for turn in log.turns
        for call in turn.calls
        if call.name == "Read" and call.file_path
    ]


def repeated_reads(logs: list[AgentLog]) -> list[Flag]:
    """Return a flag per file the agent read in MIN_READ_STEPS or more different steps."""
    readers: dict[str, dict[str, Evidence]] = {}
    for log in logs:
        step = row_id(log.step, log.iteration)
        for path, line in _reads(log):
            readers.setdefault(path, {}).setdefault(step, {"file": log.file, "line": line, "step": step})
    return [
        make_flag("repeated_reads", None, f"{path} read in {len(steps)} steps", list(steps.values()))
        for path, steps in sorted(readers.items())
        if len(steps) >= MIN_READ_STEPS
    ]


def step_flags(payload: dict[str, object]) -> list[Flag]:
    """Return the flags the payload's own step results raise: retries, non-completed steps and rejected patches."""
    return [flag for step in payload_steps(payload) for flag in _flags_of_step(step)]


def _flags_of_step(step: dict[str, object]) -> list[Flag]:
    """Return the flags of one payload step."""
    label = step_label(step)
    attempts = step.get("attempts")
    patch = step.get("rejected_patch_path")
    flags = []
    if isinstance(attempts, int) and attempts > 1:
        flags.append(make_flag("retries", label, f"{attempts} attempts", [{"file": "session.log", "step": label}]))
    if step.get("state") not in DONE_STATES:
        detail = f"{step.get('state')}: {step.get('error_message') or ''}"
        evidence = [{"file": "session.json", "step": label}]
        flags.append(make_flag("step_not_completed", label, detail, evidence, code=str(step.get("state"))))
    if patch:
        flags.append(make_flag("rejected_patch", label, str(patch), [{"file": str(patch)}]))
    return flags


def session_flags(payload: dict[str, object], summary: dict[str, object]) -> list[Flag]:
    """Return the flags of the session as a whole: its failure, resumes and the errors, remediations and diagnostics it carries."""
    flags: list[Flag] = []
    if summary["status"] != "completed":
        detail = f"{summary['status']}: {summary['error_message'] or ''}"
        flags.append(
            make_flag("session_not_completed", None, detail, [{"file": "session.log"}], code=str(summary["status"]))
        )
    for key in ("errors", "warnings", "remediations", "diagnostics"):
        items = payload.get(key)
        if isinstance(items, list) and items:
            flags.append(
                make_flag(f"session_{key}", None, json.dumps(items, default=str)[:400], [{"file": "session.json"}])
            )
    resumes = summary["resumes"]
    if isinstance(resumes, int) and resumes > 0:
        flags.append(make_flag("resumed", None, f"{resumes} resumes", [{"file": "session.log"}]))
    return flags


def note_flags(events: list[tuple[int, dict[str, object]]], file: str) -> list[Flag]:
    """Return a flag per agent note that mentions a workaround phrase."""
    flags = []
    for number, event in events:
        text = str(event.get("target") or "")
        if event.get("event") == "agent_progress" and event.get("kind") == "note" and WORKAROUND.search(text):
            step = row_id(
                str(event.get("step_id")), event.get("iteration") if isinstance(event.get("iteration"), int) else None
            )
            flags.append(
                make_flag("workaround_note", step, text, [{"file": file, "line": number, "ts": event.get("ts")}])
            )
    return flags


def _logs_of(
    step: dict[str, object], agents: list[AgentLog], other: dict[tuple[str, int | None, int], str]
) -> tuple[list[AgentLog], list[str]]:
    """Return the agent logs and the non-stream-json stdout files of a payload step's attempts."""
    key = (step.get("step_id"), step_iteration(step))
    return (
        [log for log in agents if (log.step, log.iteration) == key],
        [name for (s, i, _), name in other.items() if (s, i) == key],
    )


def analyse(payload: dict[str, object], log_dir: Path | None) -> dict[str, object]:
    """Return the session summary, step rows and flags for a `dovo session show` payload and its log directory."""
    agents, other, tracebacks = scan_logs(log_dir) if log_dir is not None and log_dir.is_dir() else ([], {}, [])
    events, events_file = read_session_log(log_dir) if log_dir is not None else ([], "session.log")
    summary = session_summary(payload, events, agents)
    rows = [step_row(step, *_logs_of(step, agents, other)) for step in payload_steps(payload)]
    flags = [
        *session_flags(payload, summary),
        *step_flags(payload),
        *(flag for log in agents for flag in log_flags(log)),
        *repeated_reads(agents),
        *note_flags(events, events_file),
    ]
    if tracebacks:
        flags.append(make_flag("stderr_traceback", None, f"{len(tracebacks)} tracebacks in stderr logs", tracebacks))
    return {"session": summary, "steps": rows, "flags": flags}


def fetch(session: str | None) -> tuple[dict[str, object], Path | None]:
    """Return the payload of `dovo session show` for session (the latest when None) and its log directory."""
    command = ["uv", "run", "dovo", "session", "show", *([session] if session else []), "--logs", "--format", "json"]
    shown = json.loads(run(*command))
    payload = shown.get("payload") if isinstance(shown, dict) else None
    if not isinstance(payload, dict):
        raise ValueError("dovo session show returned no payload")
    files = payload.get("log_files")
    first = files[0] if isinstance(files, list) and files else None
    return payload, Path(str(first)).parent if first else None


def main() -> int:
    """Print the session analysis as JSON, or write it to --out."""
    parser = argparse.ArgumentParser(description="Extract the facts /dovo-pi analyses a dovo session from.")
    parser.add_argument("session", nargs="?", help="Session id. Defaults to the latest session.")
    parser.add_argument("--out", type=Path, help="Write the JSON here instead of printing it.")
    args = parser.parse_args()
    out: Path | None = args.out
    session: str | None = args.session
    try:
        payload, log_dir = fetch(session)
    except (subprocess.CalledProcessError, ValueError, json.JSONDecodeError) as error:
        sys.stderr.write(f"session_pi.py: could not read the session: {error}\n")
        return 1
    text = json.dumps(analyse(payload, log_dir), indent=2) + "\n"
    if out is None:
        sys.stdout.write(text)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
