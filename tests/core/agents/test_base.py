"""Contract tests for the agent provider base class and its descriptor-driven credential preflight."""

from __future__ import annotations

import abc
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

from dovo.core.agents import AgentRequest, AgentResponse, AgentResponseStatus, BaseAgentProvider
from dovo.core.agents.base import ProviderSpec, elapsed_ms
from dovo.core.agents.credentials import missing_credential_error
from dovo.core.agents.registry import PROVIDERS
from tests.harness import AgentRequestBuilder

_CANONICAL_MISSING_CREDENTIAL_ERROR = (
    "Agent provider error (AGENT_PROVIDER_ERROR): "
    "missing TEST_CREDENTIAL_PRIMARY or TEST_CREDENTIAL_FALLBACK. "
    "Fix: export TEST_CREDENTIAL_PRIMARY=... or export TEST_CREDENTIAL_FALLBACK=..."
)
_PROVIDER_RESPONSE = AgentResponse(status=AgentResponseStatus.NO_OP, duration_ms=7)


class _DirectSubclassProvider(BaseAgentProvider):
    """Non-direct provider double: subclasses the base itself and optionally declares a descriptor."""

    def __init__(self, envs: tuple[str, ...] | None) -> None:
        self.invoke_calls: list[AgentRequest] = []
        self._spec = (
            None
            if envs is None
            else ProviderSpec(
                token="unit-test",
                credential_envs=envs,
                requires_model=False,
                supports_tool_policy=False,
                supports_os_sandbox=False,
                build=lambda: self,
            )
        )

    def _provider_spec(self) -> ProviderSpec | None:
        return self._spec

    def _invoke(self, request: AgentRequest) -> AgentResponse:
        self.invoke_calls.append(request)
        return _PROVIDER_RESPONSE


def _clear_test_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("TEST_CREDENTIAL_PRIMARY", "TEST_CREDENTIAL_FALLBACK"):
        monkeypatch.delenv(name, raising=False)


class ElapsedMillisecondsTests:
    @pytest.mark.parametrize(
        ("age_seconds", "minimum_ms"),
        [
            pytest.param(0.0, 0, id="just-started-is-non-negative"),
            pytest.param(1.5, 1500, id="one-and-a-half-seconds-old"),
        ],
    )
    def test_started_timestamp_age_returns_whole_milliseconds_at_least_age(
        self, age_seconds: float, minimum_ms: int
    ) -> None:
        """[tier-1/unit] elapsed_ms: a monotonic start timestamp aged by age_seconds returns an int of at least minimum_ms."""
        elapsed = elapsed_ms(time.monotonic() - age_seconds)

        assert type(elapsed) is int
        assert elapsed >= minimum_ms


class BaseAgentProviderTests:
    def test_subclass_without_invoke_hook_cannot_be_instantiated(self) -> None:
        """[tier-1/unit] BaseAgentProvider: a subclass that omits _invoke raises TypeError on instantiation."""

        class _IncompleteProvider(BaseAgentProvider, abc.ABC):
            pass

        with pytest.raises(TypeError):
            _IncompleteProvider()  # pyright: ignore[reportAbstractUsage]  # intentional ill-typed input: the subject is the raised TypeError


class CredentialPreflightTests:
    def test_missing_credential_returns_canonical_error_without_calling_provider(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] BaseAgentProvider.invoke: a double declaring credential_envs ("TEST_CREDENTIAL_PRIMARY","TEST_CREDENTIAL_FALLBACK") with both unset returns PROVIDER_ERROR, errors == ["Agent provider error (AGENT_PROVIDER_ERROR): missing TEST_CREDENTIAL_PRIMARY or TEST_CREDENTIAL_FALLBACK. Fix: export TEST_CREDENTIAL_PRIMARY=... or export TEST_CREDENTIAL_FALLBACK=..."], and _invoke is never called."""
        _clear_test_credentials(monkeypatch)
        provider = _DirectSubclassProvider(("TEST_CREDENTIAL_PRIMARY", "TEST_CREDENTIAL_FALLBACK"))

        response = provider.invoke(AgentRequestBuilder().with_worktree_path(tmp_path).build())

        assert response.status == AgentResponseStatus.PROVIDER_ERROR
        assert response.errors == [_CANONICAL_MISSING_CREDENTIAL_ERROR]
        assert response.duration_ms >= 0
        assert provider.invoke_calls == []

    def test_blank_first_credential_with_usable_second_delegates_to_provider(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] BaseAgentProvider.invoke: TEST_CREDENTIAL_PRIMARY="  " and TEST_CREDENTIAL_FALLBACK="k" returns exactly the response _invoke produced."""
        monkeypatch.setenv("TEST_CREDENTIAL_PRIMARY", "  ")
        monkeypatch.setenv("TEST_CREDENTIAL_FALLBACK", "k")
        provider = _DirectSubclassProvider(("TEST_CREDENTIAL_PRIMARY", "TEST_CREDENTIAL_FALLBACK"))

        response = provider.invoke(AgentRequestBuilder().with_worktree_path(tmp_path).build())

        assert response == _PROVIDER_RESPONSE
        assert len(provider.invoke_calls) == 1

    @pytest.mark.parametrize(
        "envs",
        [pytest.param((), id="empty-credential-envs"), pytest.param(None, id="no-descriptor")],
    )
    def test_provider_without_mandatory_credential_delegates_to_provider(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, envs: tuple[str, ...] | None
    ) -> None:
        """[tier-1/unit] BaseAgentProvider.invoke: credential_envs=() or no spec with an empty environment calls _invoke and returns its response."""
        _clear_test_credentials(monkeypatch)
        provider = _DirectSubclassProvider(envs)

        response = provider.invoke(AgentRequestBuilder().with_worktree_path(tmp_path).build())

        assert response == _PROVIDER_RESPONSE
        assert len(provider.invoke_calls) == 1


class RegisteredAdapterCredentialContractTests:
    @pytest.mark.parametrize(
        "token",
        [pytest.param(token, id=token) for token, spec in PROVIDERS.items() if spec.credential_envs],
    )
    def test_missing_credential_returns_canonical_error_with_no_backend_activity(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, token: str
    ) -> None:
        """[tier-1/integration] PROVIDERS[token].build().invoke: every credential_envs name unset returns PROVIDER_ERROR with errors == [f"Agent provider error (AGENT_PROVIDER_ERROR): {missing_credential_error(spec)}"], and subprocess.Popen, urllib.request.urlopen, and resolve_pre_agent_baseline spies are never called."""
        spec = PROVIDERS[token]
        for name in spec.credential_envs:
            monkeypatch.delenv(name, raising=False)
        backend_calls: list[str] = []

        def _spy(label: str) -> object:
            def _record(*args: object, **kwargs: object) -> None:
                backend_calls.append(label)
                raise AssertionError(f"{label} must not run when the credential is missing")

            return _record

        monkeypatch.setattr(subprocess, "Popen", _spy("Popen"))
        monkeypatch.setattr(urllib.request, "urlopen", _spy("urlopen"))
        monkeypatch.setattr("dovo.core.agents.cli_mutation.resolve_pre_agent_baseline", _spy("baseline"))

        response = spec.build().invoke(AgentRequestBuilder().with_worktree_path(tmp_path).build())

        assert response.status == AgentResponseStatus.PROVIDER_ERROR
        assert response.errors == [f"Agent provider error (AGENT_PROVIDER_ERROR): {missing_credential_error(spec)}"]
        assert backend_calls == []
