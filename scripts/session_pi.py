#!/usr/bin/env python3
"""Extracts the measurable facts the /dovo-pi skill analyses a dovo session from.

Reads `dovo session show <id> --logs --format json` for the blueprint, outcome
and step results, then the session's own log directory: `session.log` for
resumes and agent notes, `NN_<step>[_iter_K]_attempt_N.stdout.log` for the
Claude Code stream-json each agent step wrote, and the matching `.stderr.log`.
The effort each agent step asked for comes from the blueprint the session froze
under `sessions/<id>/definitions/`. Prints one JSON document (or writes it to
--out) holding the session summary, one row per step, the slowest model
responses and the flags that point at evidence; `--format markdown` renders the
same facts as a report. It makes no judgement: every number comes from a log,
and every flag names the file and line to read.

`--history PATH` instead reduces a finished `ship` session to one flat JSON row
(`schema_version` 2: identity, the issue delivered, `totals`, and a `steps`
dict keyed by step id with loop iterations summed) and upserts it by session id
into PATH, a JSONL file kept sorted by `started_at`, so runs can be compared
over time. It refuses any other blueprint and any running session.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from itertools import pairwise
from pathlib import Path

import yaml
from publish_review import run

MIN_TURNS_FOR_BATCHING = 10
MAX_UNBATCHED_RATIO = 1.1
MIN_SERIAL_EDITS = 3
MIN_READ_STEPS = 3
MAX_EVIDENCE = 5
HISTORY_BLUEPRINTS = ("ship",)
HISTORY_SCHEMA_VERSION = 2
HISTORY_TIME_KEYS = (
    "runs",
    "active_seconds",
    "waiting_seconds",
    "agent_seconds",
    "model_seconds",
    "thinking_seconds",
    "tool_seconds",
    "command_seconds",
    "turns",
    "tool_calls",
    "subagents",
)
HISTORY_TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "thinking_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)
# Where a blueprint's issue shows in the step results of its session.json: (step id, output key).
HISTORY_ISSUE_OUTPUT = {"ship": ("prepare", "issue")}
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
EFFORT_ENV = "CLAUDE_CODE_EFFORT_LEVEL"
SLOWEST_RESPONSES = 10
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
MODEL_USAGE_KEYS = {
    "inputTokens": "input_tokens",
    "outputTokens": "output_tokens",
    "thinkingTokens": "thinking_tokens",
    "cacheReadInputTokens": "cache_read_input_tokens",
    "cacheCreationInputTokens": "cache_creation_input_tokens",
}
AGENT_METRIC_KEYS = (
    "models",
    "model_seconds",
    "thinking_seconds",
    "tool_seconds",
    "turns",
    "tool_calls",
    "calls_per_turn",
    "subagents",
    "tokens",
    "cost_usd",
    "ended",
)

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
class Response:
    """One model response, by message.id: the wall time its events closed, its thinking time and what it produced."""

    line: int
    seconds: float = 0.0
    thinking_ms: float = 0.0
    produced: list[str] = field(default_factory=list)


@dataclass
class AgentLog:
    """What one step attempt's Claude Code stream-json holds.

    Wall time between two timestamped top-level events goes to the event that ends the gap: a tool result's
    gap is tool time, an assistant event's gap is its response's time.
    """

    file: str
    step: str
    iteration: int | None
    attempt: int
    turns: list[Turn] = field(default_factory=list)
    failed: list[FailedCall] = field(default_factory=list)
    result: dict[str, object] = field(default_factory=dict)
    init: dict[str, object] = field(default_factory=dict)
    responses: dict[str, Response] = field(default_factory=dict)
    tool_seconds: float = 0.0
    clock: datetime | None = None

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
    """Add one stream-json event to log: the result, the init, its wall time, top-level tool calls, or failed tool results."""
    kind = event.get("type")
    if kind == "result":
        log.result = event
    if event.get("parent_tool_use_id"):
        return
    if kind == "system" and event.get("subtype") == "init":
        log.init = event
    _clock(log, number, event)
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


def _timestamp(event: dict[str, object]) -> datetime | None:
    """Return the event's ISO 8601 timestamp (`timestamp`, or session.log's `ts`), or None when it has none."""
    value = event.get("timestamp") or event.get("ts")
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _clock(log: AgentLog, number: int, event: dict[str, object]) -> None:
    """Charge the wall time since the previous timestamped event to the tools or to this event's response."""
    now = _timestamp(event)
    if now is None:
        return
    gap = (now - log.clock).total_seconds() if log.clock is not None else 0.0
    log.clock = now
    if event.get("type") == "user":
        log.tool_seconds += gap
    elif event.get("type") == "assistant":
        _charge_response(log, number, event, gap)


def _charge_response(log: AgentLog, number: int, event: dict[str, object], gap: float) -> None:
    """Add gap, the event's thinking time and the blocks it produced to the response its message.id names."""
    key = _message_id(event) or f"line-{number}"
    response = log.responses.setdefault(key, Response(number))
    response.seconds += gap
    response.thinking_ms += _number(event.get("thinking_duration_ms"))
    for block in _content(event):
        kind = block.get("type")
        response.produced.append(str(block.get("name")) if kind == "tool_use" else str(kind))


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


def _thinking_tokens(result: dict[str, object]) -> int:
    """Return the thinking tokens of a result event's usage.output_tokens_details, 0 when it has none."""
    usage = result.get("usage")
    details = usage.get("output_tokens_details") if isinstance(usage, dict) else None
    return int(_number(details.get("thinking_tokens"))) if isinstance(details, dict) else 0


def _usage(logs: list[AgentLog]) -> dict[str, int]:
    """Return the token usage summed over the logs' result events, thinking tokens included."""
    total: Counter[str] = Counter()
    for log in logs:
        usage = log.result.get("usage")
        for key in USAGE_KEYS:
            total[key] += int(_number(usage.get(key))) if isinstance(usage, dict) else 0
        total["thinking_tokens"] += _thinking_tokens(log.result)
    return {key: total[key] for key in sorted(total)}


def model_usage(logs: list[AgentLog]) -> dict[str, dict[str, float]]:
    """Return the tokens and cost per model, summed over the logs' result modelUsage (subagents included)."""
    models: dict[str, dict[str, float]] = {}
    for log in logs:
        for model, counts in _model_counts(log):
            total = models.setdefault(model, {})
            for key, value in counts.items():
                total[key] = total.get(key, 0.0) + value
    return {model: _rounded(total) for model, total in sorted(models.items())}


def _model_counts(log: AgentLog) -> list[tuple[str, dict[str, float]]]:
    """Return each model of the log's result modelUsage with its tokens and cost under this script's keys."""
    usage = log.result.get("modelUsage")
    if not isinstance(usage, dict):
        return []
    sources = {**MODEL_USAGE_KEYS, "costUSD": "cost_usd"}
    return [
        (str(model), {key: _number(counts.get(source)) for source, key in sources.items()})
        for model, counts in usage.items()
        if isinstance(counts, dict)
    ]


def _rounded(total: dict[str, float]) -> dict[str, float]:
    """Return a model's usage with tokens as ints and cost to four decimals, keys sorted."""
    return {key: round(value, 4) if key == "cost_usd" else int(value) for key, value in sorted(total.items())}


def _seconds(logs: list[AgentLog], key: str) -> float:
    """Return the milliseconds field key of the logs' result events, summed and in seconds."""
    return round(sum(_number(log.result.get(key)) for log in logs) / 1000, 1)


def step_row(
    step: dict[str, object], logs: list[AgentLog], other: list[str], efforts: dict[str, str]
) -> dict[str, object]:
    """Return the timeline row of a payload step, with the metrics of its agent logs and the effort it asked for."""
    return {
        "id": step_label(step),
        "step_id": step.get("step_id"),
        "loop_id": step.get("loop_id"),
        "iteration": step_iteration(step),
        "type": "claude" if logs else ("other" if other else None),
        "status": step.get("state"),
        "attempts": step.get("attempts"),
        "wall_seconds": step.get("duration_seconds"),
        "effort": efforts.get(str(step.get("step_id"))),
        **agent_metrics(logs),
        "error_message": step.get("error_message"),
    }


def agent_metrics(logs: list[AgentLog]) -> dict[str, object]:
    """Return the time, turns, calls, tokens, cost and tools summed over a step's agent logs (None without logs)."""
    if not logs:
        return dict.fromkeys(AGENT_METRIC_KEYS) | {"top_tools": []}
    tools = Counter(call.name for log in logs for turn in log.turns for call in turn.calls)
    return {
        "models": sorted(model_usage(logs)),
        "model_seconds": _seconds(logs, "duration_api_ms"),
        **_activity(logs),
        "tokens": _usage(logs),
        "cost_usd": _cost(logs),
        "ended": [log.result.get("terminal_reason") or log.result.get("subtype") for log in logs],
        "top_tools": [[name, count] for name, count in tools.most_common(3)],
    }


def _activity(logs: list[AgentLog]) -> dict[str, object]:
    """Return the thinking and tool seconds, turns, tool calls, calls per turn and subagents of the logs."""
    turns = sum(len(log.turns) for log in logs)
    calls = sum(log.calls for log in logs)
    return {
        "thinking_seconds": round(_thinking_ms(logs) / 1000, 1),
        "tool_seconds": round(sum(log.tool_seconds for log in logs), 1),
        "turns": turns,
        "tool_calls": calls,
        "calls_per_turn": round(calls / turns, 2) if turns else None,
        "subagents": sum(_subagents(log) for log in logs),
    }


def _cost(logs: list[AgentLog]) -> float:
    """Return the logs' total_cost_usd, summed to four decimals."""
    return round(sum(_number(log.result.get("total_cost_usd")) for log in logs), 4)


def _thinking_ms(logs: list[AgentLog]) -> float:
    """Return the thinking milliseconds summed over the logs' responses."""
    return sum(response.thinking_ms for log in logs for response in log.responses.values())


def _subagents(log: AgentLog) -> int:
    """Return the subagents the log's result says the session spawned."""
    stats = log.result.get("subagent_stats")
    return int(_number(stats.get("spawned"))) if isinstance(stats, dict) else 0


def slowest_responses(logs: list[AgentLog]) -> list[dict[str, object]]:
    """Return the SLOWEST_RESPONSES model responses with the most wall time across the logs, slowest first."""
    ranked = sorted(
        ((log, response) for log in logs for response in log.responses.values()), key=lambda pair: -pair[1].seconds
    )
    return [
        {
            "step": row_id(log.step, log.iteration),
            "file": log.file,
            "line": response.line,
            "seconds": round(response.seconds, 1),
            "thinking_seconds": round(response.thinking_ms / 1000, 1),
            "produced": [name for name in response.produced if name != "thinking"],
        }
        for log, response in ranked[:SLOWEST_RESPONSES]
    ]


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
        "claude_code_versions": sorted(
            {str(log.init["claude_code_version"]) for log in logs if log.init.get("claude_code_version")}
        ),
        "total_cost_usd": _cost(logs),
        "tokens": _usage(logs),
        "models": model_usage(logs),
    }


def session_runs(events: list[tuple[int, dict[str, object]]]) -> list[tuple[datetime, datetime]]:
    """Return the (start, end) of each finished run of the session: a session_started to its session_completed."""
    runs: list[tuple[datetime, datetime]] = []
    start: datetime | None = None
    for _, event in events:
        now = _timestamp(event)
        if now is None:
            continue
        if event.get("event") == "session_started":
            start = now
        elif event.get("event") == "session_completed" and start is not None:
            runs.append((start, now))
            start = None
    return runs


def time_summary(
    events: list[tuple[int, dict[str, object]]], logs: list[AgentLog], rows: list[dict[str, object]]
) -> dict[str, object]:
    """Return where the session's time went: running versus waiting between runs, and agent versus command steps.

    `waiting_seconds` is the time between one run pausing and the next starting, usually a person answering a prompt.
    """
    runs = session_runs(events)
    return {
        "runs": len(runs),
        "active_seconds": round(sum((end - start).total_seconds() for start, end in runs), 1),
        "waiting_seconds": round(sum((after[0] - before[1]).total_seconds() for before, after in pairwise(runs)), 1),
        "agent_seconds": _seconds(logs, "duration_ms"),
        "model_seconds": _seconds(logs, "duration_api_ms"),
        "command_seconds": round(sum(_number(row["wall_seconds"]) for row in rows if row["type"] != "claude"), 1),
        **_activity(logs),
    }


def step_efforts(definition: Path | None) -> dict[str, str]:
    """Return the CLAUDE_CODE_EFFORT_LEVEL each step of a frozen blueprint sets in its env, by step id."""
    if definition is None or not definition.is_file():
        return {}
    try:
        document = yaml.safe_load(definition.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return {}
    efforts: dict[str, str] = {}
    _collect_efforts(document, efforts)
    return efforts


def _collect_efforts(node: object, efforts: dict[str, str]) -> None:
    """Add the effort of every step mapping under node, walking loop and if blocks."""
    if isinstance(node, list):
        for item in node:
            _collect_efforts(item, efforts)
        return
    if not isinstance(node, dict):
        return
    env = node.get("env")
    if isinstance(node.get("id"), str) and isinstance(env, dict) and env.get(EFFORT_ENV):
        efforts[node["id"]] = str(env[EFFORT_ENV])
    for value in node.values():
        if isinstance(value, (dict, list)):
            _collect_efforts(value, efforts)


def definition_path(log_dir: Path | None, blueprint: object) -> Path | None:
    """Return the frozen blueprint of the session whose log directory is log_dir, beside it under sessions/."""
    if log_dir is None or not isinstance(blueprint, str):
        return None
    return session_dir(log_dir) / "definitions" / f"{blueprint}.yml"


def session_dir(log_dir: Path) -> Path:
    """Return the session's directory under sessions/, which sits beside the logs/ directory holding log_dir."""
    return log_dir.parent.parent / "sessions" / log_dir.name


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
    """Return the session summary, step rows, slowest responses and flags for a `dovo session show` payload and its log directory."""
    agents, other, tracebacks = scan_logs(log_dir) if log_dir is not None and log_dir.is_dir() else ([], {}, [])
    events, events_file = read_session_log(log_dir) if log_dir is not None else ([], "session.log")
    summary = session_summary(payload, events, agents)
    efforts = step_efforts(definition_path(log_dir, summary["blueprint"]))
    rows = [step_row(step, *_logs_of(step, agents, other), efforts) for step in payload_steps(payload)]
    summary["time"] = time_summary(events, agents, rows)
    flags = [
        *session_flags(payload, summary),
        *step_flags(payload),
        *(flag for log in agents for flag in log_flags(log)),
        *repeated_reads(agents),
        *note_flags(events, events_file),
    ]
    if tracebacks:
        flags.append(make_flag("stderr_traceback", None, f"{len(tracebacks)} tracebacks in stderr logs", tracebacks))
    return {"session": summary, "steps": rows, "slowest_responses": slowest_responses(agents), "flags": flags}


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


def read_session_json(log_dir: Path | None) -> dict[str, object]:
    """Return the session's session.json, or an empty document when it is missing or unreadable."""
    if log_dir is None:
        return {}
    try:
        document = json.loads((session_dir(log_dir) / "session.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return document if isinstance(document, dict) else {}


def _results(document: dict[str, object]) -> dict[str, dict[str, object]]:
    """Return the step results of a session.json by step id; a step that ran more than once keeps its last."""
    raw = document.get("results")
    items = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    return {str(item.get("step_id")): item for item in items}


def _output(results: dict[str, dict[str, object]], where: tuple[str, str] | None) -> str | None:
    """Return the (step id, key) output of the step results as a string, or None when absent or empty."""
    if where is None:
        return None
    outputs = results.get(where[0], {}).get("outputs")
    value = outputs.get(where[1]) if isinstance(outputs, dict) else None
    return str(value) if value not in (None, "") else None


def run_target(blueprint: str, document: dict[str, object]) -> str | None:
    """Return the issue a run worked on from its session.json step results, or None when absent."""
    return _output(_results(document), HISTORY_ISSUE_OUTPUT.get(blueprint))


def _sum(values: list[object]) -> float | None:
    """Return the sum of the numbers among values to four decimals, or None when there are none."""
    numbers = [_number(value) for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
    return round(sum(numbers), 4) if numbers else None


def _count(value: float | None) -> int | None:
    """Return a summed count as an int, keeping None."""
    return None if value is None else int(value)


def _mapping(value: object) -> dict[str, object]:
    """Return value when it is a dict, else an empty dict."""
    return value if isinstance(value, dict) else {}


def _token_sum(rows: list[dict[str, object]], key: str) -> float | None:
    """Return the sum of the key token count over the rows' tokens dicts, or None when no row has tokens."""
    return _sum([_mapping(row["tokens"]).get(key) for row in rows if isinstance(row.get("tokens"), dict)])


def _step_entry(rows: list[dict[str, object]]) -> dict[str, object]:
    """Return the history entry of one step id: its rows (loop iterations) summed."""
    entry: dict[str, object] = {"iterations": len(rows)}
    for key in ("wall_seconds", "model_seconds", "thinking_seconds", "tool_seconds", "cost_usd"):
        entry[key] = _sum([row.get(key) for row in rows])
    entry["output_tokens"] = _count(_token_sum(rows, "output_tokens"))
    entry["thinking_tokens"] = _count(_token_sum(rows, "thinking_tokens"))
    for key in ("turns", "tool_calls"):
        entry[key] = _count(_sum([row.get(key) for row in rows]))
    return {**entry, **_attempts_and_effort(rows)}


def _attempts_and_effort(rows: list[dict[str, object]]) -> dict[str, object]:
    """Return the attempts summed over the rows and the first non-null effort."""
    attempts = _sum([row.get("attempts") for row in rows])
    efforts = [row["effort"] for row in rows if row.get("effort")]
    return {"attempts": int(attempts) if attempts is not None else None, "effort": efforts[0] if efforts else None}


def history_steps(rows: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    """Return the history entry per step id, summing loop iterations into one entry."""
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("step_id")), []).append(row)
    return {step: _step_entry(group) for step, group in grouped.items()}


def _retries(rows: list[dict[str, object]]) -> int:
    """Return the sum of attempts - 1 over the step rows."""
    return sum(max(0, int(_number(row.get("attempts"))) - 1) for row in rows)


def _flag_counts(flags: object) -> dict[str, int]:
    """Return how many flags of each kind there are."""
    kinds = [str(flag.get("kind")) for flag in flags if isinstance(flag, dict)] if isinstance(flags, list) else []
    return dict(sorted(Counter(kinds).items()))


def history_totals(analysis: dict[str, object]) -> dict[str, object]:
    """Return the history row's totals: wall time, the time block, cost, tokens, resumes, retries and flags per kind."""
    session = _mapping(analysis["session"])
    time = _mapping(session.get("time"))
    tokens = _mapping(session.get("tokens"))
    steps = analysis["steps"]
    return {
        "wall_seconds": session.get("wall_seconds"),
        **{key: time.get(key) for key in HISTORY_TIME_KEYS},
        "cost_usd": session.get("total_cost_usd"),
        **{key: tokens.get(key, 0) for key in HISTORY_TOKEN_KEYS},
        "resumes": session.get("resumes"),
        "retries": _retries(steps if isinstance(steps, list) else []),
        "flags": _flag_counts(analysis.get("flags")),
    }


def history_row(analysis: dict[str, object], document: dict[str, object]) -> dict[str, object]:
    """Return the flat history row of an analysed session: identity, the run's issue, totals and steps."""
    session = _mapping(analysis["session"])
    blueprint = _mapping(_mapping(document.get("manifest")).get("blueprint"))
    steps = analysis["steps"]
    return {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "session_id": session.get("id"),
        "blueprint": session.get("blueprint"),
        "blueprint_sha": blueprint.get("sha"),
        "status": session.get("status"),
        "started_at": session.get("started_at"),
        "completed_at": session.get("completed_at"),
        "issue": run_target(str(session.get("blueprint")), document),
        "totals": history_totals(analysis),
        "steps": history_steps(steps if isinstance(steps, list) else []),
    }


def history_refusal(payload: dict[str, object]) -> str | None:
    """Return why the session may not enter the history (wrong blueprint, still running), or None when it may."""
    session = payload.get("session")
    session = session if isinstance(session, dict) else {}
    if session.get("blueprint_name") not in HISTORY_BLUEPRINTS:
        return f"blueprint {session.get('blueprint_name')} is not one of {', '.join(HISTORY_BLUEPRINTS)}"
    if session.get("status") == "running":
        return "the session is still running, so its numbers are not final"
    return None


def write_history(path: Path, row: dict[str, object]) -> None:
    """Replace the row of row's session id in the JSONL file path (or add it), sorted by started_at, atomically."""
    rows: list[dict[str, object]] = []
    if path.exists():
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [old for old in rows if old.get("session_id") != row["session_id"]]
    rows = sorted([*rows, row], key=lambda item: (str(item.get("started_at")), str(item.get("session_id"))))
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text("".join(json.dumps(item) + "\n" for item in rows), encoding="utf-8")
    os.replace(temp, path)


def _cell(value: object) -> str:
    """Return value as a markdown table cell: '-' for None, floats to two decimals, ints with separators."""
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}"
    if isinstance(value, list):
        return ", ".join(_cell(item) for item in value) or "-"
    return str(value).replace("|", "\\|")


def _table(headers: list[str], rows: list[list[object]]) -> list[str]:
    """Return the lines of a markdown table."""
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    return [*lines, *("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows), ""]


def _session_lines(session: dict[str, object]) -> list[str]:
    """Return the report's session and time sections."""
    time = session.get("time")
    time = time if isinstance(time, dict) else {}
    keys = ("id", "blueprint", "status", "started_at", "completed_at", "wall_seconds", "resumes", "total_cost_usd")
    rows = [[key, session.get(key)] for key in keys]
    rows += [
        ["claude_code_versions", session.get("claude_code_versions")],
        ["error_message", session.get("error_message")],
    ]
    tokens = session.get("tokens")
    token_rows = [[key, value] for key, value in tokens.items()] if isinstance(tokens, dict) else []
    return [
        f"# Session {session.get('id')}",
        "",
        *_table(["field", "value"], rows),
        "## Time",
        "",
        *_table(["measure", "seconds or count"], [[key, value] for key, value in time.items()]),
        "## Tokens",
        "",
        *_table(["kind", "tokens"], token_rows),
    ]


def _model_lines(models: object) -> list[str]:
    """Return the report's per-model usage section."""
    if not isinstance(models, dict) or not models:
        return []
    keys = [
        "input_tokens",
        "output_tokens",
        "thinking_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "cost_usd",
    ]
    rows = [[model, *(usage.get(key) for key in keys)] for model, usage in models.items() if isinstance(usage, dict)]
    return [
        "## Models",
        "",
        *_table(["model", "input", "output", "thinking", "cache read", "cache write", "cost"], rows),
    ]


STEP_COLUMNS = {
    "step": "id",
    "status": "status",
    "tries": "attempts",
    "wall s": "wall_seconds",
    "model s": "model_seconds",
    "think s": "thinking_seconds",
    "tool s": "tool_seconds",
    "turns": "turns",
    "calls": "tool_calls",
    "effort": "effort",
    "cost": "cost_usd",
}


def _step_lines(steps: object) -> list[str]:
    """Return the report's step table, with each agent step's output and thinking tokens."""
    rows = []
    for step in steps if isinstance(steps, list) else []:
        tokens = step.get("tokens") if isinstance(step.get("tokens"), dict) else {}
        values = [step.get(key) for key in STEP_COLUMNS.values()]
        rows.append([*values, tokens.get("output_tokens"), tokens.get("thinking_tokens")])
    return ["## Steps", "", *_table([*STEP_COLUMNS, "out tok", "think tok"], rows)]


def _slow_lines(responses: object) -> list[str]:
    """Return the report's slowest-responses section."""
    keys = ("step", "seconds", "thinking_seconds", "produced", "file", "line")
    rows = (
        [[row.get(key) for key in keys] for row in responses if isinstance(row, dict)]
        if isinstance(responses, list)
        else []
    )
    return ["## Slowest model responses", "", *_table(list(keys), rows)] if rows else []


def _flag_lines(flags: object) -> list[str]:
    """Return the report's flags section."""
    rows = (
        [[f.get("kind"), f.get("step"), f.get("detail")] for f in flags if isinstance(f, dict)]
        if isinstance(flags, list)
        else []
    )
    return ["## Flags", "", *_table(["kind", "step", "detail"], rows)] if rows else []


def render_markdown(analysis: dict[str, object]) -> str:
    """Return the analysis as a markdown audit report: session, time, tokens, models, steps, slowest responses, flags."""
    session = analysis.get("session")
    session = session if isinstance(session, dict) else {}
    lines = [
        *_session_lines(session),
        *_model_lines(session.get("models")),
        *_step_lines(analysis.get("steps")),
        *_slow_lines(analysis.get("slowest_responses")),
        *_flag_lines(analysis.get("flags")),
    ]
    return "\n".join(lines).rstrip() + "\n"


def save_history(payload: dict[str, object], log_dir: Path | None, history: Path) -> int:
    """Write the session's history row into history, or say why it may not and write nothing."""
    refusal = history_refusal(payload)
    if refusal is not None:
        sys.stderr.write(f"session_pi.py: not written to the history: {refusal}\n")
        return 1
    row = history_row(analyse(payload, log_dir), read_session_json(log_dir))
    write_history(history, row)
    sys.stdout.write(f"session_pi.py: wrote {row['session_id']} to {history}\n")
    return 0


def main() -> int:
    """Print the session analysis as JSON or a markdown report, or write it to --out."""
    parser = argparse.ArgumentParser(description="Extract the facts /dovo-pi analyses a dovo session from.")
    parser.add_argument("session", nargs="?", help="Session id. Defaults to the latest session.")
    parser.add_argument("--out", type=Path, help="Write the output here instead of printing it.")
    parser.add_argument("--format", choices=("json", "markdown"), default="json", help="Output format.")
    parser.add_argument(
        "--history", type=Path, help="Upsert the session's flat row into this JSONL file instead of printing a report."
    )
    args = parser.parse_args()
    out: Path | None = args.out
    session: str | None = args.session
    try:
        payload, log_dir = fetch(session)
    except (subprocess.CalledProcessError, ValueError, json.JSONDecodeError) as error:
        sys.stderr.write(f"session_pi.py: could not read the session: {error}\n")
        return 1
    if args.history is not None:
        return save_history(payload, log_dir, args.history)
    analysis = analyse(payload, log_dir)
    text = render_markdown(analysis) if args.format == "markdown" else json.dumps(analysis, indent=2) + "\n"
    if out is None:
        sys.stdout.write(text)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
