"""Fluent agent request builder for Dovo CLI test suite."""

from __future__ import annotations

from pathlib import Path
from typing import Self

from dovo.core.agents import AgentFailurePayload, AgentRequest


class AgentRequestBuilder:
    """Fluent builder for constructing AgentRequest inputs in tests."""

    def __init__(self) -> None:
        self._payload: AgentFailurePayload = AgentFailurePayload(
            command="pytest",
            args=["-q"],
            trigger_status="failed",
            exit_code=1,
            timed_out=False,
            duration_ms=10,
            stdout="boom",
            stderr="",
        )
        self._sandbox_path: Path | None = None
        self._timeout_seconds: int = 10
        self._model: str | None = None
        self._endpoint: str | None = None
        self._temperature: float | None = None
        self._max_tokens: int | None = None
        self._max_files: int | None = None

    def with_sandbox_path(self, sandbox_path: Path) -> Self:
        """Set the sandbox checkout the agent request runs against."""
        self._sandbox_path = sandbox_path
        return self

    def with_model(self, model: str) -> Self:
        """Set the provider model identifier."""
        self._model = model
        return self

    def with_endpoint(self, endpoint: str) -> Self:
        """Set the provider's HTTP endpoint."""
        self._endpoint = endpoint
        return self

    def with_temperature(self, temperature: float) -> Self:
        """Set the provider's sampling temperature."""
        self._temperature = temperature
        return self

    def with_max_tokens(self, max_tokens: int) -> Self:
        """Set the provider's max output tokens."""
        self._max_tokens = max_tokens
        return self

    def with_max_files(self, max_files: int) -> Self:
        """Set the patch gate's max touched-files limit."""
        self._max_files = max_files
        return self

    def build(self) -> AgentRequest:
        """Assemble and return the complete AgentRequest."""
        if self._sandbox_path is None:
            raise ValueError("AgentRequestBuilder requires with_sandbox_path(...) before build()")
        return AgentRequest(
            mode="fix_failure",
            instruction="Fix the failing test.",
            payload=self._payload,
            sandbox_path=self._sandbox_path,
            timeout_seconds=self._timeout_seconds,
            model=self._model,
            endpoint=self._endpoint,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            max_files=self._max_files,
        )
