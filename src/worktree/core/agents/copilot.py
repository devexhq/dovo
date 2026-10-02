"""GitHub Copilot CLI direct-mutation agent adapter."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from worktree.common.process import run_isolated_process
from worktree.core.agents.cli_mutation import (
    CliDirectMutationAdapter,
    CliMutationOutcome,
    CliMutationRunRequest,
)
from worktree.core.agents.models import AgentRequest

COPILOT_TOKEN_ENVS = ("GH_TOKEN", "GITHUB_TOKEN")


def resolve_copilot_token(env: dict[str, str] | None = None) -> str | None:
    """Resolve the Copilot auth token from the environment."""
    environ = env if env is not None else os.environ
    for name in COPILOT_TOKEN_ENVS:
        key = environ.get(name)
        if key is not None and key.strip():
            return key.strip()
    return None


def _extract_text(value: object) -> str | None:
    """Extract text content from Copilot event payload dictionary."""
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return None
    for key in ("content", "response"):
        val = value.get(key)
        if isinstance(val, str):
            return val
    message = value.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]
    return None


def _extract_exit_code(payload: dict[str, Any]) -> int | None:
    """Extract integer exit code from Copilot event payload dictionary."""
    raw_exit = payload.get("exitCode") if "exitCode" in payload else payload.get("exit_code")
    if isinstance(raw_exit, int):
        return raw_exit
    if isinstance(raw_exit, str) and raw_exit.isdigit():
        return int(raw_exit)
    return None


def _process_copilot_event(data: dict[str, Any], current_text: str | None) -> tuple[str | None, int | None]:
    """Update assistant text and extract exit code from a single Copilot event."""
    event_type = data.get("type")
    payload = data.get("data")
    if not isinstance(payload, dict):
        return current_text, None
    if event_type == "assistant.message":
        text = _extract_text(payload)
        return (text if text is not None else current_text), None
    if event_type == "result":
        exit_code = _extract_exit_code(payload)
        text = _extract_text(payload)
        new_text = text if (text is not None and current_text is None) else current_text
        return new_text, exit_code
    return current_text, None


def _parse_jsonl(stdout_text: str) -> tuple[str | None, int | None, str | None]:
    """Parse JSONL output lines from Copilot CLI stream."""
    assistant_text: str | None = None
    result_exit_code: int | None = None
    for raw_line in stdout_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            return None, None, f"invalid JSONL from Copilot CLI: {exc}"
        if isinstance(data, dict):
            assistant_text, code = _process_copilot_event(data, assistant_text)
            if code is not None:
                result_exit_code = code
    return assistant_text, result_exit_code, None


def _classify_copilot_output(completed_code: int, stdout_text: str, stderr_text: str) -> CliMutationOutcome:
    """Classify CLI process exit code and output text into a CliMutationOutcome."""
    if completed_code != 0:
        detail = stderr_text.strip() or stdout_text.strip() or f"exit {completed_code}"
        return CliMutationOutcome(status="error", error_detail=detail)

    assistant_text, exit_code, parse_error = _parse_jsonl(stdout_text)
    if parse_error is not None:
        return CliMutationOutcome(status="error", error_detail=parse_error)
    if exit_code is not None and exit_code != 0:
        return CliMutationOutcome(
            status="error",
            error_detail=f"Copilot CLI result exit code {exit_code}",
            result_text=assistant_text,
        )
    if assistant_text is None and not stdout_text.strip():
        return CliMutationOutcome(status="error", error_detail="empty Copilot CLI output")
    return CliMutationOutcome(status="finished", result_text=assistant_text or stdout_text.strip() or None)


def default_copilot_run(request: CliMutationRunRequest) -> CliMutationOutcome:
    """Invoke `gh copilot` and map its JSONL stream into an outcome."""
    token = resolve_copilot_token()
    if token is None:
        return CliMutationOutcome(
            status="error",
            error_detail="missing GH_TOKEN or GITHUB_TOKEN",
        )

    # Keep prompt off argv to avoid OS argument length limits on large payloads.
    cmd = [
        "gh",
        "copilot",
        "--",
        "-p",
        "",
        "--output-format",
        "json",
        "--silent",
        "--allow-all-tools",
        "--allow-all-paths",
        "--allow-all-urls",
    ]
    env = os.environ.copy()
    env.setdefault("GH_TOKEN", token)
    env.setdefault("GITHUB_TOKEN", token)
    if request.model:
        env["COPILOT_MODEL"] = request.model

    try:
        completed = run_isolated_process(
            cmd,
            cwd=request.sandbox_path,
            env=env,
            input_data=request.prompt.encode("utf-8"),
            timeout_seconds=request.timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return CliMutationOutcome(status="timeout")
    except FileNotFoundError as exc:
        return CliMutationOutcome(
            status="error",
            error_detail=(
                f"gh is not installed or not on PATH: {exc}. Fix: install the GitHub CLI (https://cli.github.com)"
            ),
        )
    except OSError as exc:
        return CliMutationOutcome(status="error", error_detail=str(exc))

    stdout_text = (completed.stdout or b"").decode("utf-8", errors="replace")
    stderr_text = (completed.stderr or b"").decode("utf-8", errors="replace")
    return _classify_copilot_output(int(completed.returncode), stdout_text, stderr_text)


class CopilotAgentAdapter(CliDirectMutationAdapter):
    """Run GitHub Copilot through the shared direct-mutation base."""

    def _preflight(self, request: AgentRequest) -> str | None:
        """Ensure GitHub authentication token is present in environment."""
        if resolve_copilot_token() is None:
            return "missing GH_TOKEN or GITHUB_TOKEN. Fix: export GH_TOKEN=..."
        return None

    def _provider_name(self) -> str:
        """Return the provider identifier string."""
        return "copilot"

    def _default_run(self, request: CliMutationRunRequest) -> CliMutationOutcome:
        """Execute Copilot CLI against mutation request."""
        return default_copilot_run(request)
