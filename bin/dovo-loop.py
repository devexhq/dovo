#!/usr/bin/env python3
"""Drive the dovo-plan / dovo-code / dovo-review / dovo-push skills as one loop, a fresh agent session per phase."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from rich.console import Console
from rich.live import Live
from rich.status import Status
from rich.table import Table

AGENTIC = Path(".agentic")
PLAN = AGENTIC / "plan.md"
REVIEW_MD = AGENTIC / "review.md"
REVIEW_JSON = AGENTIC / "review.json"
ROUNDS = AGENTIC / "rounds"
SKILLS = Path(".agents/skills")

# Argv template per host. "{prompt}" and "{timeout}" are substituted per phase.
# agy notes, all load-bearing:
#   --print-timeout defaults to 5m, which truncates any real implementation phase.
#   Headless tools are handled by policy, so without --dangerously-skip-permissions a write is denied.
#   `agy -p` writes nothing to stdout when stdout is not a TTY, so never redirect this driver's
#   output; run it in a terminal, or wrap it in `script -qec '...' /dev/null`.
#   Headless uses cached credentials: authenticate once interactively, or set GEMINI_API_KEY.
HOSTS: dict[str, list[str]] = {
    "agy": [
        "agy",
        "-p",
        "{prompt}",
        "--output-format",
        "stream-json",
        "--dangerously-skip-permissions",
        "--print-timeout",
        "{timeout}",
    ],
    "copilot": ["copilot", "-p", "{prompt}", "--allow-all-tools"],
    "cursor": ["cursor-agent", "-p", "{prompt}"],
}

VERDICT_APPROVE = "APPROVE"
VERDICT_CHANGES = "CHANGES_REQUIRED"


@dataclass(frozen=True)
class PhaseResult:
    """Outcome of one agent session."""

    phase: str
    exit_code: int
    seconds: float
    usage: dict[str, int] | None = None


@dataclass
class ToolRecord:
    """Record of a single tool execution."""

    tool: str
    action: str
    target_detail: str
    description: str
    start_monotonic: float
    duration: float = 0.0
    status: str = "running"  # "running" | "ok" | "error"
    error_message: str | None = None


class DisplayHandler(Protocol):
    """Protocol for terminal event display implementations."""

    def on_session_start(self, phase: str) -> None: ...

    def on_tool_active(self, record: ToolRecord) -> None: ...

    def on_tool_done(self, record: ToolRecord) -> None: ...

    def on_agent_delta(self, delta: str) -> None: ...

    def on_session_end(self) -> None: ...


def truncate_detail(text: str, max_len: int = 45) -> str:
    """Truncate long detail string, preserving path suffixes if text starts with /."""
    if len(text) <= max_len:
        return text
    if text.startswith("/"):
        return f"...{text[-(max_len - 3):]}"
    return f"{text[: max_len - 3]}..."


class StreamDisplay:
    """Rich status spinner and semantic completion log (Option 1b)."""

    def __init__(self, console: Console) -> None:
        self.console = console
        self._status: Status | None = None

    def on_session_start(self, phase: str) -> None:
        self._stop_status()

    def _stop_status(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None

    def on_tool_active(self, record: ToolRecord) -> None:
        msg = f"[dovo-loop] ⠋ {record.description}..."
        if self._status is None:
            self._status = self.console.status(msg, spinner="dots")
            self._status.start()
        else:
            self._status.update(msg)

    def on_tool_done(self, record: ToolRecord) -> None:
        self._stop_status()
        dur_str = f"({record.duration:.1f}s)"
        if record.status == "ok":
            self.console.print(f"[dovo-loop] [green]✓[/green] {record.description} {dur_str}")
        else:
            self.console.print(f"[dovo-loop] [red]✗[/red] {record.description} {dur_str}")

    def on_agent_delta(self, delta: str) -> None:
        self._stop_status()
        print(delta, end="", flush=True)

    def on_session_end(self) -> None:
        self._stop_status()


class TableDisplay:
    """Rich Live dynamic table dashboard (Option 2b)."""

    def __init__(self, console: Console) -> None:
        self.console = console
        self.records: list[ToolRecord] = []
        self._live: Live | None = None
        self._rendered_row_count = 0

    def _build_table(self) -> Table:
        table = Table(box=None, pad_edge=False, show_header=True, header_style="bold")
        table.add_column("ACTION", width=14, no_wrap=True)
        table.add_column("TARGET / DETAIL", width=46, no_wrap=True)
        table.add_column("TIME", width=7, no_wrap=True)
        table.add_column("STATUS", width=10, no_wrap=True)

        for rec in self.records[self._rendered_row_count :]:
            detail = truncate_detail(rec.target_detail, 45)
            if rec.status == "running":
                elapsed = time.monotonic() - rec.start_monotonic
                time_str = f"{elapsed:.1f}s"
                status_str = "[yellow]• run[/yellow]"
            elif rec.status == "ok":
                time_str = f"{rec.duration:.1f}s"
                status_str = "[green]✓ ok[/green]"
            else:
                time_str = f"{rec.duration:.1f}s"
                status_str = "[red]✗ error[/red]"
            table.add_row(rec.action, detail, time_str, status_str)
        return table

    def on_session_start(self, phase: str) -> None:
        self.records.clear()
        self._rendered_row_count = 0
        if self._live is not None:
            self._live.stop()
            self._live = None

    def _ensure_live(self) -> None:
        if self._live is None:
            self._live = Live(
                self._build_table(),
                console=self.console,
                refresh_per_second=4,
                transient=False,
            )
            self._live.start()
        else:
            self._live.update(self._build_table())

    def on_tool_active(self, record: ToolRecord) -> None:
        self.records.append(record)
        self._ensure_live()

    def on_tool_done(self, record: ToolRecord) -> None:
        if self._live is not None:
            self._live.update(self._build_table())

    def on_agent_delta(self, delta: str) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None
            self._rendered_row_count = len(self.records)
        print(delta, end="", flush=True)

    def on_session_end(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None
            self._rendered_row_count = len(self.records)


class SessionLogger:
    """Logs raw 2a tabular data to scratch/loops/session-{identifier}/session-{identifier}-YYYYMMDDHHmmss.log."""

    def __init__(self, phase: str) -> None:
        self.phase = phase
        self.sanitized_phase = phase.replace(" ", "-").replace("(", "").replace(")", "").replace("/", "-").lower()
        self.timestamp = time.strftime("%Y%m%d%H%M%S")
        self.conversation_id: str | None = None
        self.log_path: Path | None = None

    def set_conversation_id(self, conv_id: str) -> None:
        """Capture conversation identifier if not already set."""
        if not self.conversation_id and conv_id:
            self.conversation_id = conv_id

    def ensure_log_file(self) -> Path:
        """Ensure session directory and log file are initialized with 2a header."""
        if self.log_path is not None:
            return self.log_path

        short_id = self.conversation_id[:8] if self.conversation_id else None
        identifier = f"{self.sanitized_phase}-{short_id}" if short_id else self.sanitized_phase
        session_dir = Path("scratch/loops") / f"session-{identifier}"
        session_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = session_dir / f"session-{identifier}-{self.timestamp}.log"
        if not self.log_path.exists():
            header = f"{'ACTION':<14}{'TARGET / DETAIL':<46}{'TIME':<7}{'STATUS'}\n"
            self.log_path.write_text(header)
        return self.log_path

    def record_tool(self, record: ToolRecord) -> None:
        """Append one tool execution row to the 2a raw log."""
        path = self.ensure_log_file()
        status_str = "✓ ok" if record.status == "ok" else "✗ error"
        detail = truncate_detail(record.target_detail, 45)
        dur_str = f"{record.duration:.1f}s"
        line = f"{record.action:<14}{detail:<46}{dur_str:<7}{status_str}\n"
        with path.open("a") as f:
            f.write(line)

    def finish(self) -> None:
        """Ensure log file is initialized even if no tools were called."""
        self.ensure_log_file()


@dataclass
class StreamState:
    """Track streaming text state during subprocess execution."""

    pending_newline: bool = False
    streamed_any_text: bool = False
    usage: dict[str, int] | None = None
    active_tools: dict[int, ToolRecord] = field(default_factory=dict)

    def write_delta(self, delta: str) -> None:
        """Stream an agent text delta."""
        print(delta, end="", flush=True)
        self.pending_newline = not delta.endswith("\n")
        self.streamed_any_text = True

    def ensure_newline(self) -> None:
        """Ensure terminal cursor is on a fresh line before driver logs."""
        if self.pending_newline:
            print()
            self.pending_newline = False


def log(message: str) -> None:
    """Print a driver-level message, distinguishable from agent output."""
    print(f"[dovo-loop] {message}", flush=True)


def format_tool_target(params: dict[str, object]) -> str:
    """Extract a relative path and optional line range from tool parameters."""
    target = str(params.get("AbsolutePath") or params.get("TargetFile") or "")
    try:
        target = str(Path(target).relative_to(Path.cwd()))
    except ValueError:
        pass
    if "StartLine" in params and "EndLine" in params:
        return f"{target}:{params['StartLine']}-{params['EndLine']}"
    return target


def summarize_tool(tool_name: str, params: dict[str, object]) -> str:
    """Format a compact, single-line summary of a tool call."""
    if tool_name == "run_command":
        return str(params.get("CommandLine", ""))
    if tool_name in {"view_file", "replace_file_content", "write_to_file"}:
        return format_tool_target(params)
    if tool_name == "grep_search":
        return f"query={params.get('Query', '')!r}"

    parts: list[str] = []
    for k, v in params.items():
        if k in {"CodeContent", "ReplacementContent", "TargetContent", "toolSummary", "toolAction"}:
            continue
        v_str = str(v)
        parts.append(f"{k}={v_str[:37]}..." if len(v_str) > 40 else f"{k}={v_str}")
    return " ".join(parts)


def derive_tool_action_and_detail(tool: str, params: dict[str, object]) -> tuple[str, str]:
    """Derive clean action name and target detail for tabular display and logs."""
    if tool == "run_command":
        cmd = str(params.get("CommandLine", "")).strip()
        if cmd.startswith("git "):
            return "git", cmd[4:].strip()
        return "run_command", cmd
    if tool in {"view_file", "replace_file_content", "write_to_file"}:
        return tool, format_tool_target(params)
    if tool == "list_dir":
        path_str = str(params.get("DirectoryPath", ""))
        try:
            path_str = str(Path(path_str).relative_to(Path.cwd()))
        except ValueError:
            pass
        return "list_dir", path_str
    if tool == "grep_search":
        return "grep_search", f"query={params.get('Query', '')!r}"
    return tool, summarize_tool(tool, params)


def _format_error_message(msg: str) -> str:
    """Format single concise error message line."""
    if "no such file or directory" in msg or "failed to read file" in msg:
        return "file not found"
    lines = [line.strip() for line in msg.splitlines() if line.strip()]
    if not lines:
        return "error"
    last = lines[-1]
    return f"{last[:40]}..." if len(last) > 40 else last


def extract_error_detail(tool_info: dict[str, object]) -> str:
    """Extract a concise error description from tool failure payload."""
    err_obj = tool_info.get("error")
    if not isinstance(err_obj, dict):
        return "error"
    msg = str(err_obj.get("message", "")).strip()
    return _format_error_message(msg) if msg else "error"


def _derive_error_description(tool: str, action: str, detail: str, error: str) -> str:
    """Derive failure description for stream spinner/log."""
    if tool == "view_file":
        return f"Failed to read {detail}: {error}"
    if tool in {"replace_file_content", "write_to_file"}:
        return f"Failed to write {detail}: {error}"
    return f"Failed {action} {detail}: {error}"


def _derive_git_description(detail: str) -> str:
    """Derive intent description for git commands."""
    if "diff" in detail:
        return "Inspecting git diff"
    if "checkout" in detail or "branch" in detail:
        return f"Checked out {detail.split()[-1]}"
    if any(keyword in detail for keyword in ("pull", "merge", "fetch")):
        return "Synced base branch origin/main"
    return f"git {detail}"


def _derive_file_description(tool: str, detail: str) -> str:
    """Derive intent description for file operations."""
    if tool == "view_file":
        return f"Read {detail}"
    if tool in {"replace_file_content", "write_to_file"}:
        return f"Updated {detail}"
    return ""


def derive_tool_description(
    tool: str, action: str, detail: str, params: dict[str, object], error: str | None = None
) -> str:
    """Derive human-friendly intent description for stream spinner/log."""
    if error:
        return _derive_error_description(tool, action, detail, error)

    summary = str(params.get("toolSummary") or params.get("toolAction") or "").strip()
    if summary:
        return summary

    if action == "git":
        return _derive_git_description(detail)

    file_desc = _derive_file_description(tool, detail)
    return file_desc or f"{action} {detail}".strip()


def handle_tool_step(
    update: dict[str, object],
    state: StreamState,
    display: DisplayHandler,
    logger: SessionLogger,
) -> None:
    """Log a tool invocation or completion, updating live display and writing 2a raw log."""
    state.ensure_newline()
    tool = str(update.get("tool_name", "tool"))
    tool_state = str(update.get("state", ""))
    tool_info = update.get("tool_info")
    params: dict[str, object] = tool_info.get("parameters", {}) if isinstance(tool_info, dict) else {}
    step_idx = int(update.get("step_index", 0))

    if tool_state == "ACTIVE":
        action, target_detail = derive_tool_action_and_detail(tool, params)
        desc = derive_tool_description(tool, action, target_detail, params)
        record = ToolRecord(
            tool=tool,
            action=action,
            target_detail=target_detail,
            description=desc,
            start_monotonic=time.monotonic(),
        )
        state.active_tools[step_idx] = record
        display.on_tool_active(record)
        return

    record = state.active_tools.pop(step_idx, None)
    if record is None:
        action, target_detail = derive_tool_action_and_detail(tool, params)
        record = ToolRecord(
            tool=tool,
            action=action,
            target_detail=target_detail,
            description=f"{action} {target_detail}",
            start_monotonic=time.monotonic(),
        )

    duration = update.get("duration_seconds")
    record.duration = (
        float(duration) if isinstance(duration, (int, float)) else (time.monotonic() - record.start_monotonic)
    )
    record.status = "ok" if tool_state == "DONE" else "error"
    if tool_state == "ERROR" and isinstance(tool_info, dict):
        record.error_message = extract_error_detail(tool_info)
        record.description = derive_tool_description(
            record.tool, record.action, record.target_detail, params, error=record.error_message
        )

    display.on_tool_done(record)
    logger.record_tool(record)


def handle_agent_response(
    update: dict[str, object],
    state: StreamState,
    display: DisplayHandler,
) -> None:
    """Stream response delta if present."""
    delta = update.get("text_delta")
    if isinstance(delta, str) and delta:
        state.ensure_newline()
        display.on_agent_delta(delta)
        state.pending_newline = not delta.endswith("\n")
        state.streamed_any_text = True


def handle_step_update(
    update: dict[str, object],
    state: StreamState,
    display: DisplayHandler,
    logger: SessionLogger,
) -> None:
    """Dispatch step update events."""
    step_type = update.get("step_type")
    if step_type == "tool":
        handle_tool_step(update, state, display, logger)
    elif step_type == "agent_response":
        handle_agent_response(update, state, display)


def handle_result(
    result: dict[str, object],
    state: StreamState,
    display: DisplayHandler,
) -> None:
    """Capture token usage and print the final response if no deltas were streamed."""
    usage_data = result.get("usage")
    if isinstance(usage_data, dict):
        state.usage = {str(k): int(v) for k, v in usage_data.items() if isinstance(v, (int, float))}

    if state.streamed_any_text:
        return
    resp = result.get("response")
    if isinstance(resp, str) and resp:
        display.on_session_end()
        state.ensure_newline()
        print(resp, flush=True)


def handle_stream_event(
    event_obj: dict[str, object],
    state: StreamState,
    display: DisplayHandler,
    logger: SessionLogger,
) -> None:
    """Dispatch a parsed NDJSON stream event."""
    conv_id = str(event_obj.get("conversation_id", ""))
    if conv_id:
        logger.set_conversation_id(conv_id)

    event = event_obj.get("event")
    payload = event_obj.get(str(event))
    if not isinstance(payload, dict):
        return

    payload_conv_id = str(payload.get("conversation_id", ""))
    if payload_conv_id:
        logger.set_conversation_id(payload_conv_id)

    if event == "step_update":
        handle_step_update(payload, state, display, logger)
    elif event == "result":
        handle_result(payload, state, display)


def stream_process_output(
    process: subprocess.Popen[str],
    display: DisplayHandler,
    logger: SessionLogger,
) -> StreamState:
    """Stream process stdout line by line, parsing JSON events when present."""
    state = StreamState()
    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.strip()
        if not line:
            continue
        try:
            event_obj = json.loads(line)
        except json.JSONDecodeError:
            display.on_agent_delta(raw_line)
            state.streamed_any_text = True
            continue

        if isinstance(event_obj, dict):
            handle_stream_event(event_obj, state, display, logger)

    display.on_session_end()
    logger.finish()
    state.ensure_newline()
    return state


def format_duration(seconds: float) -> str:
    """Format duration in seconds to a human-readable string."""
    total = round(seconds)
    if total < 60:
        return f"{total}s"
    m, s = divmod(total, 60)
    return f"{m}m {s:02d}s"


def format_token_count(tokens: int) -> str:
    """Format token count with a metric suffix."""
    if tokens >= 1000:
        return f"{tokens / 1000:.1f}k"
    return str(tokens)


def format_round_summary(verdict: str, results: list[PhaseResult]) -> str:
    """Format a summary string for a round including time and token usage."""
    total_seconds = sum(r.seconds for r in results)
    dur_str = format_duration(total_seconds)
    breakdown = ""
    if len(results) > 1:
        parts = [f"{r.phase.split(' ')[0]}: {format_duration(r.seconds)}" for r in results]
        breakdown = f" ({', '.join(parts)})"

    total_tokens = sum((r.usage or {}).get("total_tokens", 0) for r in results)
    in_tokens = sum((r.usage or {}).get("input_tokens", 0) for r in results)
    out_tokens = sum((r.usage or {}).get("output_tokens", 0) for r in results)

    tok_str = ""
    if total_tokens > 0:
        tok_str = (
            f" | tokens: {format_token_count(total_tokens)} total "
            f"({format_token_count(in_tokens)} in, {format_token_count(out_tokens)} out)"
        )

    return f"{verdict} in {dur_str}{breakdown}{tok_str}"


def git_state() -> str:
    """Return the porcelain working-tree state."""
    return subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=True).stdout


def build_prompt(skill: str, instruction: str, inline: bool) -> str:
    """Build a phase prompt, inlining the SKILL.md body for hosts that do not discover skills."""
    if not inline:
        return f"Use the {skill} skill. {instruction}".strip()

    body = (SKILLS / skill / "SKILL.md").read_text()
    return f"Follow these instructions exactly:\n\n{body}\n\n---\n\n{instruction}".strip()


def fill(part: str, prompt: str, timeout: str) -> str:
    """Substitute one argv placeholder."""
    if part == "{prompt}":
        return prompt
    if part == "{timeout}":
        return timeout
    return part


def run_phase(phase: str, host: str, prompt: str, args: argparse.Namespace) -> PhaseResult:
    """Run one agent session to completion and time it."""
    dry_run = args.dry_run
    argv = [fill(part, prompt, args.phase_timeout) for part in HOSTS[host]]

    log(f"{phase}: {host} session starting")
    if dry_run:
        log(f"{phase}: dry run, would exec: {argv[0]} ... ({len(prompt)} char prompt)")
        return PhaseResult(phase=phase, exit_code=0, seconds=0.0)

    console = Console()
    display: DisplayHandler = (
        TableDisplay(console) if getattr(args, "display", "table") == "table" else StreamDisplay(console)
    )
    display.on_session_start(phase)
    logger = SessionLogger(phase)

    started = time.monotonic()
    process = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    state = stream_process_output(process, display, logger)
    process.wait()
    elapsed = time.monotonic() - started

    tok = state.usage.get("total_tokens", 0) if state.usage else 0
    tok_str = f" ({format_token_count(tok)} tokens)" if tok > 0 else ""
    log(f"{phase}: exit {process.returncode} in {elapsed:.0f}s{tok_str}")
    if logger.log_path is not None:
        log(f"{phase}: 2a raw log saved to {logger.log_path}")
    return PhaseResult(phase=phase, exit_code=process.returncode, seconds=elapsed, usage=state.usage)


def read_verdict() -> dict[str, object]:
    """Read and validate .agentic/review.json, raising when it is missing or self-inconsistent."""
    if not REVIEW_JSON.exists():
        raise RuntimeError(f"{REVIEW_JSON} was not written; the review session produced no verdict")

    payload = json.loads(REVIEW_JSON.read_text())
    verdict = payload.get("verdict")
    if verdict not in {VERDICT_APPROVE, VERDICT_CHANGES}:
        raise RuntimeError(f"unrecognized verdict {verdict!r} in {REVIEW_JSON}")

    counts = payload.get("counts", {})
    blocking = counts.get("blocking")
    if (verdict == VERDICT_APPROVE) != (blocking == 0):
        raise RuntimeError(f"verdict {verdict} disagrees with blocking count {blocking}")

    return payload


def archive_round(number: int, results: list[PhaseResult]) -> None:
    """Snapshot the round's verdict, timings, and token metrics under .agentic/rounds/."""
    ROUNDS.mkdir(parents=True, exist_ok=True)
    if REVIEW_JSON.exists():
        shutil.copy(REVIEW_JSON, ROUNDS / f"round-{number}-review.json")

    timings = {result.phase: round(result.seconds) for result in results}
    timings["total"] = round(sum(result.seconds for result in results))
    (ROUNDS / f"round-{number}-timings.json").write_text(json.dumps(timings, indent=2) + "\n")

    usage_metrics: dict[str, object] = {result.phase: result.usage or {} for result in results}
    usage_metrics["total"] = {
        "input_tokens": sum((r.usage or {}).get("input_tokens", 0) for r in results),
        "output_tokens": sum((r.usage or {}).get("output_tokens", 0) for r in results),
        "total_tokens": sum((r.usage or {}).get("total_tokens", 0) for r in results),
    }
    (ROUNDS / f"round-{number}-usage.json").write_text(json.dumps(usage_metrics, indent=2) + "\n")


def command_plan(args: argparse.Namespace) -> int:
    """Run the planning session, then stop for human review."""
    prompt = build_prompt("dovo-plan", args.task, args.inline_skills)
    result = run_phase("plan", args.host, prompt, args)

    if not args.dry_run and not PLAN.exists():
        log(f"FAILED: {PLAN} was not written (exit {result.exit_code}). Check the host's permission flags.")
        return 1

    log(f"plan written to {PLAN}. Review it, then run: {sys.argv[0]} run")
    return 0


def code_and_review(args: argparse.Namespace, number: int) -> tuple[str, list[PhaseResult]]:
    """Run one implement-then-review round and return its verdict."""
    fix_scope = getattr(args, "fix", "blockers")
    instruction = (
        "Implement .agentic/plan.md."
        if number == 1
        else f"Run in review mode (--fix {fix_scope}): address the findings in .agentic/review.md."
    )
    code_prompt = build_prompt("dovo-code", instruction, args.inline_skills)

    before = git_state()
    code_result = run_phase(f"code (round {number})", args.host, code_prompt, args)
    if not args.dry_run and git_state() == before:
        log("WARNING: the working tree is unchanged after the code session; the host may be soft-denying writes")

    review_prompt = build_prompt("dovo-review", "", args.inline_skills)
    review_result = run_phase(f"review (round {number})", args.host, review_prompt, args)

    results = [code_result, review_result]
    if args.dry_run:
        return VERDICT_APPROVE, results

    payload = read_verdict()
    archive_round(number, results)
    return str(payload["verdict"]), results


def command_run(args: argparse.Namespace) -> int:
    """Loop implement-and-review until APPROVE or the round cap, then optionally push."""
    if not PLAN.exists() and not args.dry_run:
        log(f"FAILED: {PLAN} is missing. Run `{sys.argv[0]} plan '<task>'` first.")
        return 1

    verdict = VERDICT_CHANGES
    for number in range(1, args.max_rounds + 1):
        verdict, results = code_and_review(args, number)
        log(f"round {number}: {format_round_summary(verdict, results)}")
        if verdict == VERDICT_APPROVE:
            break

    if verdict != VERDICT_APPROVE:
        log(f"STOPPED: still {verdict} after {args.max_rounds} rounds. See {REVIEW_MD}.")
        return 1

    if not args.push:
        log("APPROVE. Nothing pushed; run with --push, or use /dovo-push yourself.")
        return 0

    push_prompt = build_prompt("dovo-push", "", args.inline_skills)
    push_result = run_phase("push", args.host, push_prompt, args)
    return 0 if push_result.exit_code == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=sorted(HOSTS), default="agy", help="agent CLI to spawn per phase")
    parser.add_argument(
        "--phase-timeout",
        default="4h",
        help="per-session cap; agy's --print-timeout default of 5m truncates an implementation phase",
    )
    parser.add_argument(
        "--inline-skills",
        action="store_true",
        help="paste SKILL.md into the prompt; unnecessary for agy, which discovers .agents/skills natively",
    )
    parser.add_argument(
        "--display",
        choices=["table", "stream"],
        default="table",
        help="terminal display mode: 'table' (2b Rich live table) or 'stream' (1b status spinner)",
    )
    parser.add_argument("--dry-run", action="store_true", help="print what would run without spawning")
    parser.add_argument(
        "--fix",
        choices=["suggestions", "blockers", "all"],
        default="blockers",
        help="findings to address in review mode: 'suggestions', 'blockers', or 'all' (default: blockers)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="plan only, then stop for human review")
    plan_parser.add_argument("task", help="issue number or plain-language task")
    plan_parser.set_defaults(func=command_plan)

    run_parser = subparsers.add_parser("run", help="implement, review, loop to APPROVE")
    run_parser.add_argument("--max-rounds", type=int, default=3, help="review rounds before giving up")
    run_parser.add_argument("--push", action="store_true", help="run dovo-push after APPROVE")
    run_parser.add_argument(
        "--fix",
        choices=["suggestions", "blockers", "all"],
        default=argparse.SUPPRESS,
        help="findings to address in review mode: 'suggestions', 'blockers', or 'all' (default: blockers)",
    )
    run_parser.set_defaults(func=command_run)

    return parser


def main() -> int:
    """Entry point."""
    args = build_parser().parse_args()
    try:
        return int(args.func(args))
    except (RuntimeError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        log(f"FAILED: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
