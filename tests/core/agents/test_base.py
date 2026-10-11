"""Contract tests for the agent provider base class and its descriptor-driven credential preflight."""

from __future__ import annotations

import abc
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

from dovo.common.tool_policy import ToolCapability, ToolPolicy, ToolRule
from dovo.core.agents import (
    AgentEnvMode,
    AgentRequest,
    AgentResponse,
    AgentResponseStatus,
    BaseAgentProvider,
    default_tool_policy,
    tool_policy_unsupported_message,
)
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

    def __init__(
        self,
        envs: tuple[str, ...] | None,
        response: AgentResponse = _PROVIDER_RESPONSE,
        control_envs: tuple[str, ...] = (),
        supports_tool_policy: bool = False,
        policy_rejection: str | None = None,
    ) -> None:
        self.invoke_calls: list[AgentRequest] = []
        self._response = response
        self._policy_rejection = policy_rejection
        self._spec = (
            None
            if envs is None
            else ProviderSpec(
                token="unit-test",
                credential_envs=envs,
                requires_model=False,
                supports_tool_policy=supports_tool_policy,
                supports_os_sandbox=False,
                build=lambda: self,
                control_envs=control_envs,
            )
        )

    def _provider_spec(self) -> ProviderSpec | None:
        return self._spec

    def _policy_unsupported(self, request: AgentRequest) -> str | None:
        return self._policy_rejection

    def _invoke(self, request: AgentRequest) -> AgentResponse:
        self.invoke_calls.append(request)
        return self._response


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


_POLICIES = {
    "default": default_tool_policy(),
    "empty": ToolPolicy(),
    "allow-all": ToolPolicy(allow=[ToolRule(capability=ToolCapability.SHELL)], allow_all=True),
    "allow-all-with-deny": ToolPolicy(allow_all=True, deny=[ToolRule(capability=ToolCapability.SHELL, pattern="rm *")]),
}


class ToolPolicyConformanceTests:
    @pytest.mark.parametrize(
        ("policy", "rejected"),
        [
            pytest.param("default", True, id="default-rejected"),
            pytest.param("empty", True, id="empty-rejected"),
            pytest.param("allow-all", False, id="allow-all-proceeds"),
            pytest.param("allow-all-with-deny", True, id="allow-all-with-deny-rejected"),
        ],
    )
    def test_non_policy_provider_rejects_restrictive_policy_before_invoke(
        self, tmp_path: Path, policy: str, rejected: bool
    ) -> None:
        """[tier-1/unit] BaseAgentProvider.invoke: supports_tool_policy=False double returns PROVIDER_ERROR with errors == [tool_policy_unsupported_message('unit-test')] and zero _invoke calls when rejected; otherwise delegates once."""
        provider = _DirectSubclassProvider(())
        request = AgentRequestBuilder().with_worktree_path(tmp_path).with_tools(_POLICIES[policy]).build()

        response = provider.invoke(request)

        if rejected:
            assert response.status == AgentResponseStatus.PROVIDER_ERROR
            assert response.errors == [tool_policy_unsupported_message("unit-test")]
            assert provider.invoke_calls == []
        else:
            assert response == _PROVIDER_RESPONSE
            assert len(provider.invoke_calls) == 1

    def test_policy_capable_provider_hook_rejection_blocks_invoke(self, tmp_path: Path) -> None:
        """[tier-1/unit] BaseAgentProvider.invoke: supports_tool_policy=True double whose _policy_unsupported returns a message yields PROVIDER_ERROR and zero _invoke calls."""
        provider = _DirectSubclassProvider((), supports_tool_policy=True, policy_rejection="hook says no")
        request = AgentRequestBuilder().with_worktree_path(tmp_path).with_tools(default_tool_policy()).build()

        response = provider.invoke(request)

        assert response.status == AgentResponseStatus.PROVIDER_ERROR
        assert response.errors == ["hook says no"]
        assert provider.invoke_calls == []

    def test_policy_capable_provider_without_rejection_delegates_to_provider(self, tmp_path: Path) -> None:
        """[tier-1/unit] BaseAgentProvider.invoke: supports_tool_policy=True double that accepts the default policy delegates to _invoke once."""
        provider = _DirectSubclassProvider((), supports_tool_policy=True)
        request = AgentRequestBuilder().with_worktree_path(tmp_path).with_tools(default_tool_policy()).build()

        response = provider.invoke(request)

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


def _reflecting_response(status: AgentResponseStatus, secret: str) -> AgentResponse:
    return AgentResponse(
        status=status,
        errors=[f"first {secret}", "second"],
        raw_text=f"raw {secret}",
        summary=f"summary {secret}",
        unified_diff=f"+{secret}\n",
        duration_ms=7,
    )


class InvokeRedactionTests:
    @pytest.mark.parametrize(
        "status",
        [
            AgentResponseStatus.TIMEOUT,
            AgentResponseStatus.PROVIDER_ERROR,
            AgentResponseStatus.NO_OP,
            AgentResponseStatus.PROPOSED_PATCH,
        ],
    )
    def test_invoke_masks_reflected_credential_in_every_response_branch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: AgentResponseStatus
    ) -> None:
        """[tier-1/unit] BaseAgentProvider.invoke: a credentialed double returning a response with errors, raw_text, and summary holding TEST_CREDENTIAL_PRIMARY's value returns those fields with the literal absent for each status."""
        _clear_test_credentials(monkeypatch)
        monkeypatch.setenv("TEST_CREDENTIAL_PRIMARY", "xq7")
        provider = _DirectSubclassProvider(
            ("TEST_CREDENTIAL_PRIMARY", "TEST_CREDENTIAL_FALLBACK"), _reflecting_response(status, "xq7")
        )

        response = provider.invoke(AgentRequestBuilder().with_worktree_path(tmp_path).build())

        assert response.status == status
        assert response.errors == ["first [REDACTED:TEST_CREDENTIAL_PRIMARY]", "second"]
        assert response.raw_text == "raw [REDACTED:TEST_CREDENTIAL_PRIMARY]"
        assert response.summary == "summary [REDACTED:TEST_CREDENTIAL_PRIMARY]"
        assert response.unified_diff == "+xq7\n"

    def test_invoke_masks_suffix_secret_for_a_credential_free_descriptor(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] BaseAgentProvider.invoke: a double with credential_envs=() reflecting SVC_SECRET's value returns it as "[REDACTED:SVC_SECRET]"."""
        monkeypatch.setenv("SVC_SECRET", "s3cr3t-value")
        provider = _DirectSubclassProvider((), _reflecting_response(AgentResponseStatus.NO_OP, "s3cr3t-value"))

        response = provider.invoke(AgentRequestBuilder().with_worktree_path(tmp_path).build())

        assert response.errors == ["first [REDACTED:SVC_SECRET]", "second"]

    def test_invoke_masks_the_alternative_credential_that_was_not_used(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] BaseAgentProvider.invoke: with TEST_CREDENTIAL_PRIMARY and TEST_CREDENTIAL_FALLBACK both set, a response reflecting the FALLBACK value returns it masked."""
        monkeypatch.setenv("TEST_CREDENTIAL_PRIMARY", "xq7")
        monkeypatch.setenv("TEST_CREDENTIAL_FALLBACK", "zk9")
        provider = _DirectSubclassProvider(
            ("TEST_CREDENTIAL_PRIMARY", "TEST_CREDENTIAL_FALLBACK"),
            _reflecting_response(AgentResponseStatus.PROVIDER_ERROR, "zk9"),
        )

        response = provider.invoke(AgentRequestBuilder().with_worktree_path(tmp_path).build())

        assert response.raw_text == "raw [REDACTED:TEST_CREDENTIAL_FALLBACK]"

    def test_invoke_masks_the_rotated_credential_on_the_next_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] BaseAgentProvider.invoke: TEST_CREDENTIAL_PRIMARY changed between two calls on one provider masks the second call's new value in errors."""
        monkeypatch.setenv("TEST_CREDENTIAL_PRIMARY", "xq7")
        provider = _DirectSubclassProvider(
            ("TEST_CREDENTIAL_PRIMARY", "TEST_CREDENTIAL_FALLBACK"),
            _reflecting_response(AgentResponseStatus.PROVIDER_ERROR, "rot8"),
        )
        request = AgentRequestBuilder().with_worktree_path(tmp_path).build()

        first = provider.invoke(request)
        monkeypatch.setenv("TEST_CREDENTIAL_PRIMARY", "rot8")
        second = provider.invoke(request)

        assert first.errors == ["first rot8", "second"]
        assert second.errors == ["first [REDACTED:TEST_CREDENTIAL_PRIMARY]", "second"]

    def test_invoke_leaves_response_without_secrets_equal_to_the_double_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] BaseAgentProvider.invoke: a response with no sensitive text is returned equal to the double's AgentResponse."""
        monkeypatch.setenv("TEST_CREDENTIAL_PRIMARY", "xq7")
        expected = AgentResponse(
            status=AgentResponseStatus.NO_OP, errors=["plain"], raw_text="raw", summary="done", duration_ms=3
        )
        provider = _DirectSubclassProvider(("TEST_CREDENTIAL_PRIMARY", "TEST_CREDENTIAL_FALLBACK"), expected)

        response = provider.invoke(AgentRequestBuilder().with_worktree_path(tmp_path).build())

        assert response == expected


_OVERRIDE_MESSAGE = (
    "Agent environment override '{name}' conflicts with provider or Dovo controls (AGENT_ENV_OVERRIDE_INVALID). "
    "Fix: remove it from agent.env_passthrough or the agent step's env."
)


class EnvPreflightConformanceTests:
    @pytest.mark.parametrize("mode", [pytest.param("allowlist", id="allowlist"), pytest.param("inherit", id="inherit")])
    def test_reserved_override_returns_provider_error_without_calling_the_provider(
        self, mode: AgentEnvMode, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] BaseAgentProvider.invoke: a reserved step env name returns PROVIDER_ERROR with errors == [the fixed AGENT_ENV_OVERRIDE_INVALID message] and _invoke is never called, in both env modes."""
        monkeypatch.setenv("TEST_CREDENTIAL_PRIMARY", "xq7")
        provider = _DirectSubclassProvider(
            ("TEST_CREDENTIAL_PRIMARY", "TEST_CREDENTIAL_FALLBACK"), control_envs=("TEST_CONTROL",)
        )
        request = AgentRequestBuilder().with_worktree_path(tmp_path).with_env({"TEST_CONTROL": "x"}).with_env_mode(mode)

        response = provider.invoke(request.build())

        assert response.status == AgentResponseStatus.PROVIDER_ERROR
        assert response.errors == [_OVERRIDE_MESSAGE.format(name="TEST_CONTROL")]
        assert provider.invoke_calls == []

    def test_provider_without_descriptor_skips_the_env_preflight(self, tmp_path: Path) -> None:
        """[tier-1/unit] BaseAgentProvider.invoke: a subclass whose _provider_spec returns None delegates to _invoke even when step env names a control-looking key."""
        provider = _DirectSubclassProvider(None)
        request = AgentRequestBuilder().with_worktree_path(tmp_path).with_env({"COPILOT_MODEL": "x"}).build()

        response = provider.invoke(request)

        assert response == _PROVIDER_RESPONSE
        assert len(provider.invoke_calls) == 1

    @pytest.mark.parametrize(
        "token",
        [pytest.param(token, id=token) for token, spec in PROVIDERS.items() if spec.control_envs],
    )
    def test_registered_adapter_rejects_each_of_its_control_names_before_any_backend_activity(
        self, token: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] PROVIDERS[token].build().invoke: step env naming each control_envs entry returns PROVIDER_ERROR with the fixed message and subprocess.Popen and resolve_pre_agent_baseline are never called."""
        spec = PROVIDERS[token]
        monkeypatch.setenv(spec.credential_envs[0], "tok-123")
        backend_calls: list[str] = []

        def _spy(*args: object, **kwargs: object) -> None:
            backend_calls.append("called")
            raise AssertionError("backend must not run for a rejected override")

        monkeypatch.setattr(subprocess, "Popen", _spy)
        monkeypatch.setattr("dovo.core.agents.cli_mutation.resolve_pre_agent_baseline", _spy)

        responses = [
            spec.build().invoke(AgentRequestBuilder().with_worktree_path(tmp_path).with_env({name: "x"}).build())
            for name in spec.control_envs
        ]

        assert [response.errors for response in responses] == [
            [_OVERRIDE_MESSAGE.format(name=name)] for name in spec.control_envs
        ]
        assert backend_calls == []


class InvokeEnvRedactionTests:
    def test_explicit_step_env_secret_and_proxy_password_are_masked_in_every_response_field(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] BaseAgentProvider.invoke: a step env value under MY_API_KEY and the password of HTTPS_PROXY reflected in errors, raw_text, and summary appear as '[REDACTED:MY_API_KEY]' and '[REDACTED:HTTPS_PROXY]'."""
        monkeypatch.setenv("HTTPS_PROXY", "http://user:p4ssw0rd@proxy:8080")
        reflected = "key abcdef123 proxy p4ssw0rd"
        provider = _DirectSubclassProvider(
            (),
            AgentResponse(
                status=AgentResponseStatus.PROVIDER_ERROR, errors=[reflected], raw_text=reflected, summary=reflected
            ),
        )
        request = AgentRequestBuilder().with_worktree_path(tmp_path).with_env({"MY_API_KEY": "abcdef123"}).build()

        response = provider.invoke(request)

        masked = "key [REDACTED:MY_API_KEY] proxy [REDACTED:HTTPS_PROXY]"
        assert (response.errors, response.raw_text, response.summary) == ([masked], masked, masked)
