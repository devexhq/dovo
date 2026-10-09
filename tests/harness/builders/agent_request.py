"""Fluent agent request builder for Dovo CLI test suite."""

from __future__ import annotations

from pathlib import Path
from typing import Self

from dovo.core.agents import (
    AgentEnvMode,
    AgentFailurePayload,
    AgentInvocationContext,
    AgentRequest,
    default_tool_policy,
)


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
        self._worktree_path: Path | None = None
        self._timeout_seconds: int = 10
        self._model: str | None = None
        self._endpoint: str | None = None
        self._temperature: float | None = None
        self._max_tokens: int | None = None
        self._max_files: int | None = None
        self._invocation: AgentInvocationContext | None = None
        self._env: dict[str, str] = {}
        self._metadata_env: dict[str, str] = {}
        self._env_passthrough: list[str] = []
        self._env_mode: AgentEnvMode = "allowlist"

    def with_worktree_path(self, worktree_path: Path) -> Self:
        """Set the worktree checkout the agent request runs against."""
        self._worktree_path = worktree_path
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

    def with_invocation(self, invocation: AgentInvocationContext) -> Self:
        """Set the authored attempt's private scratch and control paths."""
        self._invocation = invocation
        return self

    def with_env(self, env: dict[str, str]) -> Self:
        """Set the interpolated agent-step env forwarded to the provider subprocess."""
        self._env = dict(env)
        return self

    def with_metadata_env(self, metadata_env: dict[str, str]) -> Self:
        """Set the generated DOVO_* metadata map supplied by the execution boundary."""
        self._metadata_env = dict(metadata_env)
        return self

    def with_env_passthrough(self, env_passthrough: list[str]) -> Self:
        """Set the literal and prefix names to forward from the host environment."""
        self._env_passthrough = list(env_passthrough)
        return self

    def with_env_mode(self, env_mode: AgentEnvMode) -> Self:
        """Set the subprocess environment mode."""
        self._env_mode = env_mode
        return self

    def build(self) -> AgentRequest:
        """Assemble and return the complete AgentRequest."""
        if self._worktree_path is None:
            raise ValueError("AgentRequestBuilder requires with_worktree_path(...) before build()")
        return AgentRequest(
            mode="fix_failure",
            instruction="Fix the failing test.",
            payload=self._payload,
            worktree_path=self._worktree_path,
            timeout_seconds=self._timeout_seconds,
            model=self._model,
            endpoint=self._endpoint,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            max_files=self._max_files,
            agent_scratch_path=None if self._invocation is None else self._invocation.scratch_path,
            invocation=self._invocation,
            env=self._env,
            metadata_env=self._metadata_env,
            env_passthrough=self._env_passthrough,
            env_mode=self._env_mode,
            tools=default_tool_policy(),
        )
