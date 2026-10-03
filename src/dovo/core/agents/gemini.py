"""Gemini CLI direct-mutation agent adapter."""

from __future__ import annotations

import json
import os
import subprocess

from dovo.common.process import run_isolated_process
from dovo.core.agents.cli_mutation import (
    CliDirectMutationAdapter,
    CliMutationOutcome,
    CliMutationRunRequest,
)
from dovo.core.agents.models import AgentRequest

GEMINI_API_KEY_ENV = "GEMINI_API_KEY"


def resolve_gemini_api_key(env: dict[str, str] | None = None) -> str | None:
    """Resolve the Gemini API key from the environment."""
    environ = env if env is not None else os.environ
    key = environ.get(GEMINI_API_KEY_ENV)
    if key is None or not key.strip():
        return None
    return key.strip()


def _decode_json_response(stdout_text: str) -> tuple[str | None, str | None]:
    """Decode JSON output from Gemini CLI into response text or error."""
    text = stdout_text.strip()
    if not text:
        return None, "empty Gemini output"
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON from Gemini CLI: {exc}"
    if not isinstance(data, dict):
        return None, "Gemini CLI returned a non-object"
    response = data.get("response")
    if isinstance(response, str):
        return response, None
    if isinstance(data.get("content"), str):
        return str(data["content"]), None
    return None, "Gemini CLI JSON missing response"


def default_gemini_run(request: CliMutationRunRequest) -> CliMutationOutcome:
    """Invoke the Gemini CLI and map its result into an outcome."""
    api_key = resolve_gemini_api_key()
    if api_key is None:
        return CliMutationOutcome(
            status="error",
            error_detail=f"missing {GEMINI_API_KEY_ENV}",
        )

    # Keep prompt off argv to avoid OS argument length limits on large payloads.
    cmd = ["gemini", "-p", "", "-o", "json", "--yolo"]
    if request.model:
        cmd.extend(["-m", request.model])

    env = os.environ.copy()
    env[GEMINI_API_KEY_ENV] = api_key

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
                f"gemini is not installed or not on PATH: {exc}. "
                "Fix: install the Gemini CLI (https://github.com/google-gemini/gemini-cli)"
            ),
        )
    except OSError as exc:
        return CliMutationOutcome(status="error", error_detail=str(exc))

    stdout_text = (completed.stdout or b"").decode("utf-8", errors="replace")
    stderr_text = (completed.stderr or b"").decode("utf-8", errors="replace")
    if completed.returncode != 0:
        detail = stderr_text.strip() or stdout_text.strip() or f"exit {completed.returncode}"
        return CliMutationOutcome(status="error", error_detail=detail)

    result_text, parse_error = _decode_json_response(stdout_text)
    if parse_error is not None:
        return CliMutationOutcome(status="error", error_detail=parse_error)
    return CliMutationOutcome(status="finished", result_text=result_text)


class GeminiAgentAdapter(CliDirectMutationAdapter):
    """Run the Gemini CLI through the shared direct-mutation base."""

    def _preflight(self, request: AgentRequest) -> str | None:
        """Ensure Gemini API key is configured before running."""
        if resolve_gemini_api_key() is None:
            return f"missing {GEMINI_API_KEY_ENV}. Fix: export {GEMINI_API_KEY_ENV}=..."
        return None

    def _provider_name(self) -> str:
        """Return the provider identifier string."""
        return "gemini"

    def _default_run(self, request: CliMutationRunRequest) -> CliMutationOutcome:
        """Execute Gemini CLI against mutation request."""
        return default_gemini_run(request)
