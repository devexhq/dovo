"""Cursor SDK direct-mutation coding agent adapter."""

from __future__ import annotations

import os
import threading
from typing import Any

from dovo.core.agents.cli_mutation import (
    CliDirectMutationAdapter,
    CliMutationOutcome,
    CliMutationRunRequest,
)
from dovo.core.agents.models import AgentRequest

CURSOR_API_KEY_ENV = "CURSOR_API_KEY"


def resolve_cursor_api_key(env: dict[str, str] | None = None) -> str | None:
    """Resolve the Cursor API key from the environment."""
    environ = env if env is not None else os.environ
    key = environ.get(CURSOR_API_KEY_ENV)
    if key is None or not key.strip():
        return None
    return key.strip()


def cancel_cursor_run(run: object) -> None:
    """Best-effort cancel for a timed-out Cursor SDK run handle.

    Args:
        run: Cursor SDK run handle with optional cancel() method.
    """
    try:
        cancel = getattr(run, "cancel", None)
        if callable(cancel):
            cancel()
    except Exception:
        # A failed `run.cancel()` after a timeout is non-fatal: the caller already
        # returns CliMutationOutcome(status="timeout") regardless of cancel success.
        pass


def cursor_outcome_from_result(result: object) -> CliMutationOutcome:
    """Map a finished Cursor SDK wait() result into a CliMutationOutcome.

    Args:
        result: Result object returned by Cursor SDK run.wait().

    Returns:
        Structured mutation outcome with classified status.
    """
    raw_status = str(getattr(result, "status", "error"))
    text = getattr(result, "result", None)
    text_str = text if isinstance(text, str) else None
    if raw_status == "finished":
        return CliMutationOutcome(status="finished", result_text=text_str)
    if raw_status in {"cancelled", "timeout"}:
        return CliMutationOutcome(status="timeout", result_text=text_str)
    if raw_status in {"error", "expired"}:
        return CliMutationOutcome(
            status="error",
            result_text=text_str,
            error_detail=text_str or raw_status,
        )
    return CliMutationOutcome(
        status="error",
        result_text=text_str,
        error_detail=f"unrecognized Cursor run status {raw_status!r}",
    )


def _run_cursor_agent_thread(
    request: CliMutationRunRequest,
    *,
    api_key: str,
    agent_cls: type,
    agent_options_cls: type,
    local_options_cls: type,
) -> dict[str, Any]:
    """Start the Cursor agent on a worker thread and collect run/result/exception."""
    outcome: dict[str, Any] = {}

    def _run() -> None:
        try:
            with agent_cls.create(
                agent_options_cls(
                    model=request.model,
                    api_key=api_key,
                    local=local_options_cls(cwd=str(request.sandbox_path)),
                )
            ) as agent:
                run = agent.send(request.prompt)
                outcome["run"] = run
                outcome["result"] = run.wait()
        except Exception as exc:  # defensive: classify, never raise cross-thread
            outcome["exception"] = exc

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=request.timeout_seconds)
    if thread.is_alive():
        run = outcome.get("run")
        if run is not None:
            cancel_cursor_run(run)
        thread.join(timeout=5)
        outcome["timed_out"] = True
    return outcome


def default_cursor_run(request: CliMutationRunRequest) -> CliMutationOutcome:
    """Invoke the real Cursor SDK agent; lazily imported."""
    try:
        from cursor_sdk import Agent, AgentOptions, LocalAgentOptions
    except ImportError as exc:
        return CliMutationOutcome(
            status="error",
            error_detail=(f"cursor-sdk is not installed ({exc}). Fix: pip install src[cursor]"),
        )

    api_key = resolve_cursor_api_key()
    if api_key is None:
        return CliMutationOutcome(
            status="error",
            error_detail=f"missing {CURSOR_API_KEY_ENV}",
        )

    outcome = _run_cursor_agent_thread(
        request,
        api_key=api_key,
        agent_cls=Agent,
        agent_options_cls=AgentOptions,
        local_options_cls=LocalAgentOptions,
    )
    if outcome.get("timed_out"):
        return CliMutationOutcome(status="timeout")

    exception = outcome.get("exception")
    if exception is not None:
        return CliMutationOutcome(status="error", error_detail=str(exception))

    result = outcome.get("result")
    if result is None:
        return CliMutationOutcome(status="error", error_detail="no run result")
    return cursor_outcome_from_result(result)


class CursorAgentAdapter(CliDirectMutationAdapter):
    """Run the Cursor SDK coding agent directly against a sandbox checkout."""

    def _preflight(self, request: AgentRequest) -> str | None:
        """Validate cursor model configuration and API key."""
        model = request.model.strip() if request.model else ""
        if not model:
            return "cursor requires a non-empty model. Fix: set agent.model in .dovo/config.json"
        if resolve_cursor_api_key() is None:
            return f"missing {CURSOR_API_KEY_ENV}. Fix: export {CURSOR_API_KEY_ENV}=..."
        return None

    def _provider_name(self) -> str:
        """Return the provider identifier string."""
        return "cursor"

    def _default_run(self, request: CliMutationRunRequest) -> CliMutationOutcome:
        """Execute Cursor agent against mutation request."""
        return default_cursor_run(request)
