"""Local subprocess agent adapter (v1 provider)."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from worktree.common.process import run_isolated_process
from worktree.core.agents.base import BaseAgentProvider
from worktree.core.agents.models import AgentRequest, AgentResponse, AgentResponseStatus

LOCAL_AGENT_CMD_ENV = "WORKTREE_LOCAL_AGENT_CMD"
DEFAULT_LOCAL_AGENT_CMD = "worktree-local-agent"


class LocalAgentStdout(BaseModel):
    """Strict stdout JSON schema for the local agent CLI."""

    model_config = {"extra": "forbid", "strict": True}

    unfixable: bool = False
    unfixable_reason: str | None = None
    unified_diff: str | None = None
    summary: str | None = None


def _resolve_local_argv() -> list[str]:
    """Resolve local agent argv from env or default command name."""
    raw = os.environ.get(LOCAL_AGENT_CMD_ENV)
    if raw is None or raw.strip() == "":
        return [DEFAULT_LOCAL_AGENT_CMD]
    parts = shlex.split(raw)
    if not parts:
        return [DEFAULT_LOCAL_AGENT_CMD]
    return parts


def _request_json_bytes(request: AgentRequest) -> bytes:
    """Serialize request for local agent stdin (paths as strings)."""
    payload = request.model_dump(mode="json")
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _map_local_stdout(parsed: LocalAgentStdout, *, raw_text: str, duration_ms: int) -> AgentResponse:
    """Map parsed local agent JSON payload into an AgentResponse model."""
    if parsed.unfixable:
        return AgentResponse(
            status=AgentResponseStatus.UNFIXABLE,
            unified_diff=None,
            summary=parsed.summary,
            unfixable_reason=parsed.unfixable_reason,
            raw_text=raw_text,
            duration_ms=duration_ms,
            errors=[],
        )
    diff = parsed.unified_diff
    if diff is not None and diff != "":
        return AgentResponse(
            status=AgentResponseStatus.PROPOSED_PATCH,
            unified_diff=diff,
            summary=parsed.summary,
            unfixable_reason=None,
            raw_text=raw_text,
            duration_ms=duration_ms,
            errors=[],
        )
    return AgentResponse(
        status=AgentResponseStatus.NO_OP,
        unified_diff=diff if diff == "" else None,
        summary=parsed.summary,
        unfixable_reason=None,
        raw_text=raw_text,
        duration_ms=duration_ms,
        errors=[],
    )


def _validate_local_request(request: AgentRequest, sandbox_cwd: Path, started: float) -> AgentResponse | None:
    """Validate local agent request parameters and directory before spawning process."""
    if request.timeout_seconds < 1:
        duration_ms = int((time.monotonic() - started) * 1000)
        return AgentResponse(
            status=AgentResponseStatus.PROVIDER_ERROR,
            duration_ms=duration_ms,
            errors=["Agent provider error (AGENT_PROVIDER_ERROR): timeout_seconds must be an integer >= 1"],
        )
    if not sandbox_cwd.is_dir():
        duration_ms = int((time.monotonic() - started) * 1000)
        return AgentResponse(
            status=AgentResponseStatus.PROVIDER_ERROR,
            duration_ms=duration_ms,
            errors=[
                "Agent provider error (AGENT_PROVIDER_ERROR): "
                f"sandbox path does not exist or is not a directory: "
                f"'{sandbox_cwd}'"
            ],
        )
    return None


def _dispatch_local_command(
    argv: list[str],
    sandbox_cwd: Path,
    request: AgentRequest,
    started: float,
) -> tuple[subprocess.CompletedProcess[Any] | None, AgentResponse | None]:
    """Execute external process with request JSON piped to standard input."""
    stdin_bytes = _request_json_bytes(request)
    try:
        completed = run_isolated_process(
            argv,
            cwd=sandbox_cwd,
            input_data=stdin_bytes,
            timeout_seconds=float(request.timeout_seconds),
        )
        return completed, None
    except subprocess.TimeoutExpired:
        duration_ms = int((time.monotonic() - started) * 1000)
        return None, AgentResponse(
            status=AgentResponseStatus.TIMEOUT,
            duration_ms=duration_ms,
            errors=[
                f"Agent timed out after {request.timeout_seconds}s "
                f"(provider=local).\n"
                "Fix:\n"
                "- raise agent.timeout_seconds on the blueprint"
            ],
        )
    except (FileNotFoundError, OSError) as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        return None, AgentResponse(
            status=AgentResponseStatus.PROVIDER_ERROR,
            duration_ms=duration_ms,
            errors=[f"Agent provider error (AGENT_PROVIDER_ERROR): failed to start '{argv[0]}': {exc}"],
        )


def _parse_and_map_local_output(
    completed: subprocess.CompletedProcess[Any],
    started: float,
) -> AgentResponse:
    """Parse stdout from completed process and construct appropriate AgentResponse."""
    duration_ms = int((time.monotonic() - started) * 1000)
    stdout_text = (completed.stdout or b"").decode("utf-8", errors="replace")
    stderr_text = (completed.stderr or b"").decode("utf-8", errors="replace")
    exit_code = int(completed.returncode)

    try:
        data = json.loads(stdout_text) if stdout_text.strip() else None
    except json.JSONDecodeError as exc:
        detail = f"invalid JSON on stdout (exit {exit_code}): {exc}"
        if stderr_text.strip():
            detail = f"{detail}; stderr: {stderr_text.strip()[:500]}"
        return AgentResponse(
            status=AgentResponseStatus.PROVIDER_ERROR,
            raw_text=stdout_text or None,
            duration_ms=duration_ms,
            errors=[f"Agent provider error (AGENT_PROVIDER_ERROR): {detail}"],
        )

    if data is None:
        detail = f"empty stdout (exit {exit_code})"
        if stderr_text.strip():
            detail = f"{detail}; stderr: {stderr_text.strip()[:500]}"
        return AgentResponse(
            status=AgentResponseStatus.PROVIDER_ERROR,
            raw_text=stdout_text or None,
            duration_ms=duration_ms,
            errors=[f"Agent provider error (AGENT_PROVIDER_ERROR): {detail}"],
        )

    try:
        parsed = LocalAgentStdout.model_validate(data)
    except ValidationError as exc:
        return AgentResponse(
            status=AgentResponseStatus.PROVIDER_ERROR,
            raw_text=stdout_text,
            duration_ms=duration_ms,
            errors=[f"Agent provider error (AGENT_PROVIDER_ERROR): stdout JSON failed schema validation: {exc}"],
        )

    return _map_local_stdout(parsed, raw_text=stdout_text, duration_ms=duration_ms)


class LocalAgentAdapter(BaseAgentProvider):
    """Invoke a local agent executable over JSON stdin/stdout."""

    def propose_fix(self, request: AgentRequest) -> AgentResponse:
        """Run the local agent CLI; never raises for classified outcomes."""
        started = time.monotonic()
        argv = _resolve_local_argv()
        sandbox = request.sandbox_path.expanduser()
        try:
            sandbox_cwd = sandbox.resolve()
        except OSError:
            sandbox_cwd = sandbox

        error_response = _validate_local_request(request, sandbox_cwd, started)
        if error_response is not None:
            return error_response

        completed, dispatch_error = _dispatch_local_command(argv, sandbox_cwd, request, started)
        if dispatch_error is not None:
            return dispatch_error
        if completed is None:
            return AgentResponse(
                status=AgentResponseStatus.PROVIDER_ERROR,
                duration_ms=int((time.monotonic() - started) * 1000),
                errors=["Agent provider error (AGENT_PROVIDER_ERROR): subprocess execution failed"],
            )

        return _parse_and_map_local_output(completed, started)


def resolve_local_agent_argv_for_tests() -> list[str]:
    """Test helper exposing argv resolution (not part of public package API)."""
    return _resolve_local_argv()
