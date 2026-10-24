"""Fake process-boundary runner for direct-mutation agent adapter tests.

Stands in for dovo.common.process.stream_isolated_process's call signature at the
subprocess boundary, so a test can monkeypatch a single configurable object instead of
hand-rolling a `fake_run` closure with an ad-hoc captured dict per test file. Configure it
to return a canned CompletedProcess via `.returning(...)`, or to raise a canned exception
via `.raising(...)`; every call is recorded on `.calls`/`.last_call` as a FakeAgentRunnerCall,
a pydantic model for inspecting call arguments.

FakeGitRunner is a GitRunner that records every git command and answers configured
argument prefixes with a canned GitProcessOutput or exception, running real git otherwise.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Self

import pytest
from pydantic import BaseModel

from dovo.core.agents import (
    AgentInvocationContext,
    AgentProgress,
    AgentRequest,
    AgentResponse,
    BaseAgentProvider,
)
from dovo.core.git import GitProcessOutput, GitRunner


class _ClaudeSubprocess:
    """Stand-in for the subprocess module inside dovo.core.agents.claude: run is replaced, every other name delegates."""

    def __init__(self, run: Callable[..., subprocess.CompletedProcess[bytes]]) -> None:
        self.run = run

    def __getattr__(self, name: str) -> object:
        return getattr(subprocess, name)


def patch_claude_probe(monkeypatch: pytest.MonkeyPatch, run: Callable[..., subprocess.CompletedProcess[bytes]]) -> None:
    """Replace the Claude login probe's subprocess.run with run, leaving subprocess.run untouched for every other module."""
    monkeypatch.setattr("dovo.core.agents.claude.subprocess", _ClaudeSubprocess(run))


class FakeAgentRunnerCall(BaseModel):
    """One recorded invocation of a FakeAgentRunner."""

    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    cmd: list[str]
    cwd: Path
    env: dict[str, str]
    input_data: bytes
    timeout_seconds: float


class FakeAgentRunner:
    """Configurable stand-in for stream_isolated_process, returning a result or raising."""

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


def real_invocation(root: Path) -> AgentInvocationContext:
    """Create real scratch and control directories under root and return the invocation naming them."""
    scratch, control = root / "scratch", root / "control"
    scratch.mkdir(parents=True)
    control.mkdir()

    return AgentInvocationContext(
        invocation_id="a" * 32, scratch_path=scratch.resolve(), control_path=control.resolve()
    )


class EditingAgentRunner(FakeAgentRunner):
    """FakeAgentRunner that writes files into the spawn cwd before returning, as an editing agent would."""

    def __init__(self, edits: Mapping[str, str]) -> None:
        super().__init__()
        self._edits = dict(edits)

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
        """Write every edit under cwd, then delegate to FakeAgentRunner."""
        for name, content in self._edits.items():
            (cwd / name).write_text(content, encoding="utf-8")
        return super().__call__(cmd, cwd=cwd, env=env, input_data=input_data, timeout_seconds=timeout_seconds, **kwargs)


class UnfixableAgentRunner(FakeAgentRunner):
    """FakeAgentRunner that writes reason to <agent_scratch_path>/unfixable.txt, read from the prompt's JSON body, before returning."""

    def __init__(self, reason: str) -> None:
        super().__init__()
        self._reason = reason

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
        """Write the reason to the scratch path named in the prompt, then delegate to FakeAgentRunner."""
        prompt = input_data.decode()
        body = json.loads(prompt[prompt.index("{") :])
        (Path(body["agent_scratch_path"]) / "unfixable.txt").write_text(self._reason, encoding="utf-8")
        return super().__call__(cmd, cwd=cwd, env=env, input_data=input_data, timeout_seconds=timeout_seconds, **kwargs)


def _claude_finished_stream(text: str) -> bytes:
    """Return a Claude Code stream-json run that finishes successfully with text as its result."""
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text}).encode() + b"\n"


def _copilot_finished_stream(text: str) -> bytes:
    """Return a Copilot JSONL run that finishes with exit code 0 after one assistant message carrying text."""
    return (
        json.dumps({"type": "assistant.message", "data": {"content": text}}).encode()
        + b'\n{"type":"result","data":{"exitCode":0}}\n'
    )


PROVIDER_FINISHED_STREAMS: Final[dict[str, Callable[[str], bytes]]] = {
    "claude": _claude_finished_stream,
    "copilot": _copilot_finished_stream,
}


class StreamingAgentRunner:
    """Typed stand-in for stream_isolated_process that replays canned lines through the stream callbacks it receives."""

    def __init__(self, *, stdout_lines: list[str], stderr_lines: list[str], completed_stdout: bytes) -> None:
        self._stdout_lines = stdout_lines
        self._stderr_lines = stderr_lines
        self._completed_stdout = completed_stdout
        self.received_stdout_callback: Callable[[str], None] | None = None
        self.received_stderr_callback: Callable[[str], None] | None = None

    def __call__(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        input_data: bytes,
        timeout_seconds: float,
        on_stdout_line: Callable[[str], None] | None = None,
        on_stderr_line: Callable[[str], None] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        self.received_stdout_callback = on_stdout_line
        self.received_stderr_callback = on_stderr_line
        if on_stdout_line is not None:
            for line in self._stdout_lines:
                on_stdout_line(line)
        if on_stderr_line is not None:
            for line in self._stderr_lines:
                on_stderr_line(line)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=self._completed_stdout, stderr=b"")


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

    def _invoke(self, request: AgentRequest) -> AgentResponse:
        """Record the request, run on_call, and return the next queued response (the last repeats)."""
        self._requests.append(request)
        if self._on_call is not None:
            self._on_call(request)
        index = min(len(self._requests), len(self._responses)) - 1
        return self._responses[index]


class RecordingProgressSink:
    """AgentProgressSink double recording every reported item and answering a scripted quiet window."""

    def __init__(self, seconds_since_last_report: float | None = None) -> None:
        self.reported: list[AgentProgress] = []
        self._seconds_since_last_report = seconds_since_last_report

    def report(self, progress: AgentProgress) -> None:
        """Record one progress item."""
        self.reported.append(progress)

    def seconds_since_last_report(self) -> float | None:
        """Return the scripted quiet window."""
        return self._seconds_since_last_report


class FakeGitRunner(GitRunner):
    """GitRunner that records every exec call and answers configured args prefixes, delegating the rest to real git."""

    def __init__(self, responses: dict[tuple[str, ...], GitProcessOutput | Exception] | None = None) -> None:
        self.responses = dict(responses or {})
        self.calls: list[tuple[str, ...]] = []

    def exec(
        self,
        args: Sequence[str],
        path: Path,
        *,
        input_text: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> GitProcessOutput:
        """Record args, then raise or return the response of the first key that prefixes args, else run real git."""
        called = tuple(args)
        self.calls.append(called)
        for key, response in self.responses.items():
            if called[: len(key)] == key:
                if isinstance(response, Exception):
                    raise response
                return response
        return super().exec(args, path, input_text=input_text, env=env)


def use_fake_git(monkeypatch: pytest.MonkeyPatch, fake: FakeGitRunner) -> None:
    """Make CliContext.build hand fake to every command, so CLI runner tests inject git at the composition root."""
    monkeypatch.setattr("dovo.cli.context.GitRunner", lambda: fake)


FIXED_NOW: Final[datetime] = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def fixed_clock() -> datetime:
    """Return FIXED_NOW on every call."""
    return FIXED_NOW


def fixed_new_id(kind: str) -> str:
    """Return f"{kind}_0000abcd" on every call."""
    return f"{kind}_0000abcd"


def use_fixed_clock_and_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make CliContext.build hand fixed_clock and fixed_new_id to every command."""
    monkeypatch.setattr("dovo.cli.context.utc_now", fixed_clock)
    monkeypatch.setattr("dovo.cli.context.new_session_id", fixed_new_id)
