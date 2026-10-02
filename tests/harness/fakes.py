"""Fake process-boundary runner for direct-mutation agent adapter tests.

Stands in for worktree.common.process.run_isolated_process's call signature at the
subprocess boundary, so a test can monkeypatch a single configurable object instead of
hand-rolling a `fake_run` closure with an ad-hoc captured dict per test file. Configure it
to return a canned CompletedProcess via `.returning(...)`, or to raise a canned exception
via `.raising(...)`; every call is recorded on `.calls`/`.last_call` as a FakeAgentRunnerCall,
a pydantic model for inspecting call arguments.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Self

from pydantic import BaseModel

from worktree.core.agents import AgentRequest, AgentResponse, BaseAgentProvider


class FakeAgentRunnerCall(BaseModel):
    """One recorded invocation of a FakeAgentRunner."""

    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    cmd: list[str]
    cwd: Path
    env: dict[str, str]
    input_data: bytes
    timeout_seconds: float


class FakeAgentRunner:
    """Configurable stand-in for run_isolated_process, returning a result or raising."""

    def __init__(self) -> None:
        self._returncode: int | None = None
        self._stdout: bytes = b""
        self._stderr: bytes = b""
        self._exception: Exception | None = None
        self.calls: list[FakeAgentRunnerCall] = []

    def returning(self, *, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> Self:
        """Configure calls to return a completed process with this outcome."""
        self._returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._exception = None
        return self

    def raising(self, exc: Exception) -> Self:
        """Configure calls to raise exc instead of returning."""
        self._exception = exc
        self._returncode = None
        return self

    def __call__(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        input_data: bytes,
        timeout_seconds: float,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(
            FakeAgentRunnerCall(cmd=cmd, cwd=cwd, env=env, input_data=input_data, timeout_seconds=timeout_seconds)
        )
        if self._exception is not None:
            raise self._exception
        if self._returncode is None:
            raise AssertionError("FakeAgentRunner called without .returning(...) or .raising(...) configured")
        return subprocess.CompletedProcess(
            args=cmd, returncode=self._returncode, stdout=self._stdout, stderr=self._stderr
        )

    @property
    def last_call(self) -> FakeAgentRunnerCall:
        """The most recent recorded call; raises if the runner was never called."""
        if not self.calls:
            raise AssertionError("FakeAgentRunner was never called")
        return self.calls[-1]


class FakeAgentProvider(BaseAgentProvider):
    """Provider double returning queued responses and recording every AgentRequest it receives."""

    def __init__(self, *responses: AgentResponse, on_call: Callable[[AgentRequest], None] | None = None) -> None:
        if not responses:
            raise ValueError("FakeAgentProvider requires at least one response")
        self._responses = list(responses)
        self._on_call = on_call
        self._requests: list[AgentRequest] = []

    @property
    def requests(self) -> list[AgentRequest]:
        """Return the requests received, in call order."""
        return self._requests

    def propose_fix(self, request: AgentRequest) -> AgentResponse:
        """Record the request, run on_call, and return the next queued response (the last repeats)."""
        self._requests.append(request)
        if self._on_call is not None:
            self._on_call(request)
        index = min(len(self._requests), len(self._responses)) - 1
        return self._responses[index]
