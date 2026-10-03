"""Tests for the Cursor SDK direct-mutation coding agent adapter."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from dovo.core.agents import AgentResponseStatus, CursorAgentAdapter
from dovo.core.agents.cli_mutation import CliMutationOutcome, CliMutationRunRequest, CliMutationRunStatus
from dovo.core.agents.cursor import (
    CURSOR_API_KEY_ENV,
    cancel_cursor_run,
    cursor_outcome_from_result,
    default_cursor_run,
    resolve_cursor_api_key,
)
from tests.harness import AgentRequestBuilder


@pytest.fixture(autouse=True)
def _cursor_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CURSOR_API_KEY_ENV, "test-key")


class ResolveCursorApiKeyTests:
    @pytest.mark.parametrize(
        ("env", "expected"),
        [
            pytest.param({CURSOR_API_KEY_ENV: "abc"}, "abc", id="present"),
            pytest.param({}, None, id="missing"),
            pytest.param({CURSOR_API_KEY_ENV: "   "}, None, id="blank"),
        ],
    )
    def test_resolve_checks_cursor_api_key(self, env: dict[str, str], expected: str | None) -> None:
        """CURSOR_API_KEY resolves when non-blank; missing or blank resolves to None."""
        assert resolve_cursor_api_key(env) == expected


class CursorOutcomeMappingTests:
    @pytest.mark.parametrize(
        ("raw_status", "result_text", "expected_status", "expected_error_detail"),
        [
            pytest.param("finished", "patch applied", "finished", None, id="finished_with_text"),
            pytest.param("finished", None, "finished", None, id="finished_without_text"),
            pytest.param("cancelled", None, "timeout", None, id="cancelled_maps_to_timeout"),
            pytest.param("timeout", "timeout text", "timeout", None, id="timeout_status"),
            pytest.param("error", "token limit", "error", "token limit", id="error_with_detail"),
            pytest.param("error", None, "error", "error", id="error_fallback_detail"),
            pytest.param("expired", "session ended", "error", "session ended", id="expired_with_detail"),
            pytest.param("expired", None, "error", "expired", id="expired_fallback_detail"),
            pytest.param(
                "unknown_state",
                "txt",
                "error",
                "unrecognized Cursor run status 'unknown_state'",
                id="unrecognized_status",
            ),
        ],
    )
    def test_outcome_from_result_maps_status_and_text(
        self,
        raw_status: str,
        result_text: str | None,
        expected_status: CliMutationRunStatus,
        expected_error_detail: str | None,
    ) -> None:
        """Every SDK wait() status/text combination maps to the documented outcome fields."""
        outcome = cursor_outcome_from_result(SimpleNamespace(status=raw_status, result=result_text))
        assert outcome == CliMutationOutcome(
            status=expected_status, result_text=result_text, error_detail=expected_error_detail
        )


class CancelCursorRunTests:
    def test_callable_cancel_is_invoked(self) -> None:
        """A run handle exposing a callable cancel() has it invoked."""
        cancelled = False

        class _Run:
            def cancel(self) -> None:
                nonlocal cancelled
                cancelled = True

        cancel_cursor_run(_Run())

        assert cancelled is True

    def test_failing_cancel_swallows_exception(self) -> None:
        """A cancel() that raises never propagates past cancel_cursor_run."""

        class _FailingRun:
            def cancel(self) -> None:
                raise RuntimeError("SDK cancel failed")

        cancel_cursor_run(_FailingRun())

    def test_missing_or_non_callable_cancel_is_ignored(self) -> None:
        """A run handle with no cancel attribute, or a non-callable one, is a no-op."""
        cancel_cursor_run(object())
        cancel_cursor_run(SimpleNamespace(cancel="not-callable"))


class CursorAdapterTests:
    def test_missing_model_returns_provider_error_before_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cursor's own preflight requires a non-empty model, independent of the shared base."""
        monkeypatch.setattr(
            "dovo.core.agents.cursor.default_cursor_run",
            lambda _req: pytest.fail("must not be called when preflight fails"),
        )
        adapter = CursorAgentAdapter()

        resp = adapter.propose_fix(AgentRequestBuilder().with_worktree_path(tmp_path).build())

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert resp.errors == [
            "Agent provider error (AGENT_PROVIDER_ERROR): "
            "cursor requires a non-empty model. Fix: set agent.model in .dovo/config.json"
        ]

    def test_missing_api_key_returns_provider_error_before_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cursor's own preflight requires CURSOR_API_KEY once a model is present."""
        monkeypatch.delenv(CURSOR_API_KEY_ENV, raising=False)
        adapter = CursorAgentAdapter()

        resp = adapter.propose_fix(
            AgentRequestBuilder().with_worktree_path(tmp_path).with_model("composer-2.5").build()
        )

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert resp.errors == [
            f"Agent provider error (AGENT_PROVIDER_ERROR): "
            f"missing {CURSOR_API_KEY_ENV}. Fix: export {CURSOR_API_KEY_ENV}=..."
        ]


class DefaultCursorRunTests:
    def test_missing_sdk_returns_error_naming_install_extra(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unimportable cursor_sdk maps to an error outcome naming the install extra."""
        monkeypatch.setitem(sys.modules, "cursor_sdk", None)

        outcome = default_cursor_run(
            CliMutationRunRequest(model="composer-2.5", worktree_path=tmp_path, prompt="fix it", timeout_seconds=1.0)
        )

        assert outcome.status == "error"
        assert outcome.result_text is None
        assert outcome.error_detail is not None and "src[cursor]" in outcome.error_detail

    def test_missing_api_key_returns_error_outcome(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A missing CURSOR_API_KEY at run time (not just preflight) maps to an error outcome."""
        monkeypatch.delenv(CURSOR_API_KEY_ENV, raising=False)
        fake_sdk = SimpleNamespace(Agent=object, AgentOptions=object, LocalAgentOptions=object)
        monkeypatch.setitem(sys.modules, "cursor_sdk", fake_sdk)

        outcome = default_cursor_run(
            CliMutationRunRequest(model="composer-2.5", worktree_path=tmp_path, prompt="fix it", timeout_seconds=1.0)
        )

        assert outcome.status == "error"
        assert outcome.result_text is None
        assert outcome.error_detail == f"missing {CURSOR_API_KEY_ENV}"

    def test_thread_worker_timeout_invokes_cancel_and_returns_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A run that never finishes within the timeout is cancelled and reported as timeout."""
        cancelled = False
        event = threading.Event()

        class _FakeRun:
            def wait(self) -> object:
                event.wait(timeout=1.0)
                return SimpleNamespace(status="finished", result="done")

            def cancel(self) -> None:
                nonlocal cancelled
                cancelled = True
                event.set()

        class _FakeAgent:
            def __enter__(self) -> _FakeAgent:
                return self

            def __exit__(self, *args: object) -> None:
                pass

            def send(self, prompt: str) -> _FakeRun:
                return _FakeRun()

            @classmethod
            def create(cls, options: object) -> _FakeAgent:
                return cls()

        fake_sdk = SimpleNamespace(
            Agent=_FakeAgent,
            AgentOptions=lambda **kwargs: kwargs,
            LocalAgentOptions=lambda **kwargs: kwargs,
        )
        monkeypatch.setitem(sys.modules, "cursor_sdk", fake_sdk)

        outcome = default_cursor_run(
            CliMutationRunRequest(model="composer-2.5", worktree_path=tmp_path, prompt="fix it", timeout_seconds=0.01)
        )

        assert outcome.status == "timeout"
        assert outcome.result_text is None
        assert outcome.error_detail is None
        assert cancelled is True

    def test_thread_worker_exception_returns_error_outcome(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An exception raised inside the worker thread is classified as an error outcome."""
        fake_sdk = SimpleNamespace(Agent=object, AgentOptions=object, LocalAgentOptions=object)
        monkeypatch.setitem(sys.modules, "cursor_sdk", fake_sdk)
        monkeypatch.setattr(
            "dovo.core.agents.cursor._run_cursor_agent_thread",
            lambda *args, **kwargs: {"exception": RuntimeError("socket closed")},
        )

        outcome = default_cursor_run(
            CliMutationRunRequest(model="composer-2.5", worktree_path=tmp_path, prompt="fix it", timeout_seconds=1.0)
        )

        assert outcome.status == "error"
        assert outcome.result_text is None
        assert outcome.error_detail == "socket closed"

    def test_thread_worker_missing_result_returns_error_outcome(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A worker thread that produces neither a result nor an exception is still an error."""
        fake_sdk = SimpleNamespace(Agent=object, AgentOptions=object, LocalAgentOptions=object)
        monkeypatch.setitem(sys.modules, "cursor_sdk", fake_sdk)
        monkeypatch.setattr("dovo.core.agents.cursor._run_cursor_agent_thread", lambda *args, **kwargs: {})

        outcome = default_cursor_run(
            CliMutationRunRequest(model="composer-2.5", worktree_path=tmp_path, prompt="fix it", timeout_seconds=1.0)
        )

        assert outcome.status == "error"
        assert outcome.result_text is None
        assert outcome.error_detail == "no run result"
