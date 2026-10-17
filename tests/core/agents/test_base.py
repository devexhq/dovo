"""Contract tests for the agent provider base class and its descriptor-driven credential preflight."""

from __future__ import annotations

import abc
import subprocess
import time
import urllib.request
from collections.abc import Callable
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
from tests.harness import PROVIDER_FINISHED_STREAMS, AgentRequestBuilder, FakeAgentRunner, real_invocation

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
        auth_label: Callable[[], str | None] | None = None,
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
                auth_label=auth_label,
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
        elapsed = elapsed_ms(time.monotonic() - age_seconds)

        assert type(elapsed) is int
        assert elapsed >= minimum_ms


class BaseAgentProviderTests:
    def test_subclass_without_invoke_hook_cannot_be_instantiated(self) -> None:

        class _IncompleteProvider(BaseAgentProvider, abc.ABC):
            pass

        with pytest.raises(TypeError):
            _IncompleteProvider()  # ty: ignore[call-non-callable]  # intentional ill-typed input: the subject is the raised TypeError


class CredentialPreflightTests:
    def test_missing_credential_returns_canonical_error_without_calling_provider(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_test_credentials(monkeypatch)
        provider = _DirectSubclassProvider(("TEST_CREDENTIAL_PRIMARY", "TEST_CREDENTIAL_FALLBACK"))

        response = provider.invoke(AgentRequestBuilder().with_worktree_path(tmp_path).build())

        assert response.status == AgentResponseStatus.PROVIDER_ERROR
        assert response.errors == [_CANONICAL_MISSING_CREDENTIAL_ERROR]
        assert provider.invoke_calls == []

    def test_blank_first_credential_with_usable_second_delegates_to_provider(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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
        _clear_test_credentials(monkeypatch)
        provider = _DirectSubclassProvider(envs)

        response = provider.invoke(AgentRequestBuilder().with_worktree_path(tmp_path).build())

        assert response == _PROVIDER_RESPONSE
        assert len(provider.invoke_calls) == 1


class CredentialPreflightProbeTests:
    @pytest.mark.parametrize(
        ("label_result", "has_label", "expected_errors", "expected_calls"),
        [
            pytest.param("login", True, [], 1, id="label-delegates"),
            pytest.param(None, True, [_CANONICAL_MISSING_CREDENTIAL_ERROR], 0, id="no-label-returns-missing-detail"),
            pytest.param(None, False, [_CANONICAL_MISSING_CREDENTIAL_ERROR], 0, id="no-hook-keeps-existing-behavior"),
        ],
    )
    def test_unset_credentials_delegate_only_when_the_auth_label_reports_a_login(
        self,
        label_result: str | None,
        has_label: bool,
        expected_errors: list[str],
        expected_calls: int,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Delegate only when auth_label names an authentication; otherwise return the missing-credential detail."""
        _clear_test_credentials(monkeypatch)
        label = (lambda: label_result) if has_label else None
        provider = _DirectSubclassProvider(("TEST_CREDENTIAL_PRIMARY", "TEST_CREDENTIAL_FALLBACK"), auth_label=label)

        response = provider.invoke(AgentRequestBuilder().with_worktree_path(tmp_path).build())

        assert (response.errors, len(provider.invoke_calls)) == (expected_errors, expected_calls)

    def test_label_is_not_called_when_a_credential_env_is_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Record zero auth_label calls when any credential_envs value is non-blank."""
        _clear_test_credentials(monkeypatch)
        monkeypatch.setenv("TEST_CREDENTIAL_FALLBACK", "k")
        label_calls: list[str] = []

        def _label() -> str | None:
            label_calls.append("called")
            return None

        provider = _DirectSubclassProvider(("TEST_CREDENTIAL_PRIMARY", "TEST_CREDENTIAL_FALLBACK"), auth_label=_label)

        provider.invoke(AgentRequestBuilder().with_worktree_path(tmp_path).build())

        assert (label_calls, len(provider.invoke_calls)) == ([], 1)


_POLICIES = {
    "default": default_tool_policy(),
    "empty": ToolPolicy(),
    "allow-all": ToolPolicy(allow=[ToolRule(capability=ToolCapability.SHELL)], allow_all=True),
    "allow-all-with-deny": ToolPolicy(allow_all=True, deny=[ToolRule(capability=ToolCapability.SHELL, pattern="rm *")]),
}


class ToolPolicyConformanceTests:
    @pytest.mark.parametrize(
        "policy",
        [
            pytest.param("default", id="default"),
            pytest.param("empty", id="empty"),
            pytest.param("allow-all-with-deny", id="allow-all-with-deny"),
        ],
    )
    def test_non_policy_provider_rejects_restrictive_policy_before_invoke(self, tmp_path: Path, policy: str) -> None:
        provider = _DirectSubclassProvider(())
        request = AgentRequestBuilder().with_worktree_path(tmp_path).with_tools(_POLICIES[policy]).build()

        response = provider.invoke(request)

        assert response.status == AgentResponseStatus.PROVIDER_ERROR
        assert response.errors == [tool_policy_unsupported_message("unit-test")]
        assert provider.invoke_calls == []

    def test_non_policy_provider_delegates_an_allow_all_policy(self, tmp_path: Path) -> None:
        provider = _DirectSubclassProvider(())
        request = AgentRequestBuilder().with_worktree_path(tmp_path).with_tools(_POLICIES["allow-all"]).build()

        response = provider.invoke(request)

        assert response == _PROVIDER_RESPONSE
        assert len(provider.invoke_calls) == 1

    def test_policy_capable_provider_hook_rejection_blocks_invoke(self, tmp_path: Path) -> None:
        provider = _DirectSubclassProvider((), supports_tool_policy=True, policy_rejection="hook says no")
        request = AgentRequestBuilder().with_worktree_path(tmp_path).with_tools(default_tool_policy()).build()

        response = provider.invoke(request)

        assert response.status == AgentResponseStatus.PROVIDER_ERROR
        assert response.errors == ["hook says no"]
        assert provider.invoke_calls == []

    def test_policy_capable_provider_without_rejection_delegates_to_provider(self, tmp_path: Path) -> None:
        provider = _DirectSubclassProvider((), supports_tool_policy=True)
        request = AgentRequestBuilder().with_worktree_path(tmp_path).with_tools(default_tool_policy()).build()

        response = provider.invoke(request)

        assert response == _PROVIDER_RESPONSE
        assert len(provider.invoke_calls) == 1


_REGISTERED = [pytest.param(token, id=token) for token in PROVIDERS]
_CREDENTIALED = [pytest.param(token, id=token) for token, spec in PROVIDERS.items() if spec.credential_envs]
_ALL_CREDENTIAL_ENVS = tuple(name for spec in PROVIDERS.values() for name in spec.credential_envs)


@pytest.fixture
def _isolated_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every registered provider's credential variables from the host environment."""
    for name in _ALL_CREDENTIAL_ENVS:
        monkeypatch.delenv(name, raising=False)


def _missing_credential_suffix(spec: ProviderSpec) -> str:
    """Return the text a provider's missing-credential error must end with."""
    return f"Fix: {spec.auth_fix}" if spec.auth_fix is not None else missing_credential_error(spec)


@pytest.mark.usefixtures("_isolated_credentials")
class RegisteredAdapterCredentialContractTests:
    @pytest.fixture(autouse=True)
    def _claude_on_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Make the Claude binary resolvable so a missing credential, not a missing install, is reported."""
        monkeypatch.setattr("dovo.core.agents.claude.shutil.which", lambda _name: "/usr/bin/claude")

    @pytest.mark.parametrize("token", _CREDENTIALED)
    def test_missing_credential_returns_provider_detail(self, tmp_path: Path, token: str) -> None:
        """Return PROVIDER_ERROR ending with each credentialed provider's descriptor-derived detail."""
        response = PROVIDERS[token].build().invoke(AgentRequestBuilder().with_worktree_path(tmp_path).build())

        [error] = response.errors
        assert response.status == AgentResponseStatus.PROVIDER_ERROR
        assert error.startswith("Agent provider error (AGENT_PROVIDER_ERROR): ")
        assert error.endswith(_missing_credential_suffix(PROVIDERS[token]))

    @pytest.mark.parametrize("token", _CREDENTIALED)
    def test_missing_credential_starts_no_backend_activity(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, token: str
    ) -> None:
        """Record no Popen, urlopen or baseline call when the credential is missing."""
        backend_calls: list[str] = []

        def _spy(label: str) -> object:
            def _record(*args: object, **kwargs: object) -> None:
                backend_calls.append(label)
                raise AssertionError(f"{label} must not run when the credential is missing")

            return _record

        monkeypatch.setattr(subprocess, "Popen", _spy("Popen"))
        monkeypatch.setattr(urllib.request, "urlopen", _spy("urlopen"))
        monkeypatch.setattr("dovo.core.agents.cli_mutation.resolve_pre_agent_baseline", _spy("baseline"))

        PROVIDERS[token].build().invoke(AgentRequestBuilder().with_worktree_path(tmp_path).build())

        assert backend_calls == []


_UNRELATED_SECRETS = ("AWS_SECRET_ACCESS_KEY", "SSH_AUTH_SOCK", "DOVO_TEST_UNRELATED_SECRET")


def _provider_with_fake_runner(
    token: str, monkeypatch: pytest.MonkeyPatch
) -> tuple[BaseAgentProvider, FakeAgentRunner]:
    """Build the registered provider with its winning credential set and a fake finished-stream runner."""
    spec = PROVIDERS[token]
    for name in spec.credential_envs[:1]:
        monkeypatch.setenv(name, "test-token")
    runner = FakeAgentRunner().returning(stdout=PROVIDER_FINISHED_STREAMS[token]("ok"))
    monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)

    return spec.build(), runner


@pytest.mark.usefixtures("_isolated_credentials")
class RegisteredAdapterEnvBoundaryTests:
    @pytest.mark.parametrize("token", _CREDENTIALED)
    def test_allowlist_spawn_env_holds_only_the_winning_credential_and_no_foreign_secrets(
        self, token: str, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spawn with credential_envs[0] == 'test-token' and none of the unrelated or foreign secrets."""
        provider, runner = _provider_with_fake_runner(token, monkeypatch)
        winner = PROVIDERS[token].credential_envs[0]
        secrets = [*_UNRELATED_SECRETS, *(name for name in _ALL_CREDENTIAL_ENVS if name != winner)]
        for name in secrets:
            monkeypatch.setenv(name, "host-value")

        provider.invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        env = runner.last_call.env
        assert env[winner] == "test-token"
        assert [name for name in secrets if name in env] == []

    @pytest.mark.parametrize("token", _REGISTERED)
    def test_inherit_spawn_env_keeps_unrelated_host_variables(
        self, token: str, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Keep DOVO_TEST_UNRELATED_SECRET == 'host-value' in the spawn env under env_mode 'inherit'."""
        provider, runner = _provider_with_fake_runner(token, monkeypatch)
        monkeypatch.setenv("DOVO_TEST_UNRELATED_SECRET", "host-value")

        provider.invoke(AgentRequestBuilder().with_worktree_path(git_repo).with_env_mode("inherit").build())

        assert runner.last_call.env["DOVO_TEST_UNRELATED_SECRET"] == "host-value"

    @pytest.mark.parametrize("mode", [pytest.param("allowlist", id="allowlist"), pytest.param("inherit", id="inherit")])
    @pytest.mark.parametrize("token", _REGISTERED)
    def test_host_control_variables_never_reach_the_spawn_env(
        self, token: str, mode: AgentEnvMode, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Leave every name in the provider's control_envs out of the spawn env."""
        provider, runner = _provider_with_fake_runner(token, monkeypatch)
        control_names = PROVIDERS[token].control_envs
        for name in control_names:
            monkeypatch.setenv(name, "ambient")

        provider.invoke(AgentRequestBuilder().with_worktree_path(git_repo).with_env_mode(mode).build())

        assert not set(control_names) & set(runner.last_call.env)

    @pytest.mark.parametrize("token", _REGISTERED)
    def test_scratch_keys_equal_the_invocation_scratch_path(
        self, token: str, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spawn with DOVO_AGENT_SCRATCH, TMPDIR, TMP and TEMP all equal to the invocation's scratch path."""
        provider, runner = _provider_with_fake_runner(token, monkeypatch)
        invocation = real_invocation(tmp_path)

        provider.invoke(AgentRequestBuilder().with_worktree_path(git_repo).with_invocation(invocation).build())

        env = runner.last_call.env
        assert env["DOVO_AGENT_SCRATCH"] == env["TMPDIR"] == env["TMP"] == env["TEMP"] == str(invocation.scratch_path)

    @pytest.mark.parametrize("token", _REGISTERED)
    def test_invocation_control_path_never_appears_in_the_spawn_env(
        self, token: str, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Leave both the invocation's control path and its parent directory out of every spawn env value."""
        provider, runner = _provider_with_fake_runner(token, monkeypatch)
        invocation = real_invocation(tmp_path)

        provider.invoke(AgentRequestBuilder().with_worktree_path(git_repo).with_invocation(invocation).build())

        assert str(invocation.control_path) not in runner.last_call.env.values()
        assert str(invocation.control_path.parent) not in runner.last_call.env.values()

    @pytest.mark.parametrize("token", _REGISTERED)
    def test_repeated_calls_follow_the_latest_invocation_scratch_path(
        self, token: str, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spawn the second call with TMPDIR equal to the second invocation's scratch path, not the first's."""
        provider, runner = _provider_with_fake_runner(token, monkeypatch)
        first, second = real_invocation(tmp_path / "first"), real_invocation(tmp_path / "second")

        provider.invoke(AgentRequestBuilder().with_worktree_path(git_repo).with_invocation(first).build())
        provider.invoke(AgentRequestBuilder().with_worktree_path(git_repo).with_invocation(second).build())

        assert runner.calls[0].env["TMPDIR"] == str(first.scratch_path)
        assert runner.calls[1].env["TMPDIR"] == str(second.scratch_path)


@pytest.mark.usefixtures("_isolated_credentials")
class RegisteredAdapterToolPolicyConformanceTests:
    @pytest.mark.parametrize("token", _REGISTERED)
    def test_allow_all_policy_reaches_the_process_boundary_once(
        self, token: str, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return NO_OP and record exactly one runner call for an allow-all policy."""
        provider, runner = _provider_with_fake_runner(token, monkeypatch)
        request = AgentRequestBuilder().with_worktree_path(git_repo).with_tools(ToolPolicy(allow_all=True)).build()

        response = provider.invoke(request)

        assert response.status == AgentResponseStatus.NO_OP
        assert len(runner.calls) == 1

    @pytest.mark.parametrize(
        "token", [pytest.param(token, id=token) for token, spec in PROVIDERS.items() if spec.supports_tool_policy]
    )
    def test_default_policy_without_an_invocation_is_rejected_before_any_spawn(
        self, token: str, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return PROVIDER_ERROR with errors == [tool_policy_unsupported_message(token)] and zero runner calls."""
        provider, runner = _provider_with_fake_runner(token, monkeypatch)
        request = AgentRequestBuilder().with_worktree_path(git_repo).with_tools(default_tool_policy()).build()

        response = provider.invoke(request)

        assert response.status == AgentResponseStatus.PROVIDER_ERROR
        assert response.errors == [tool_policy_unsupported_message(token)]
        assert runner.calls == []


def _response_containing_secret(status: AgentResponseStatus, secret: str) -> AgentResponse:
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
        _clear_test_credentials(monkeypatch)
        monkeypatch.setenv("TEST_CREDENTIAL_PRIMARY", "xq7")
        provider = _DirectSubclassProvider(
            ("TEST_CREDENTIAL_PRIMARY", "TEST_CREDENTIAL_FALLBACK"), _response_containing_secret(status, "xq7")
        )

        response = provider.invoke(AgentRequestBuilder().with_worktree_path(tmp_path).build())

        assert response.status == status
        assert response.errors == ["first [REDACTED:TEST_CREDENTIAL_PRIMARY]", "second"]
        assert response.raw_text == "raw [REDACTED:TEST_CREDENTIAL_PRIMARY]"
        assert response.summary == "summary [REDACTED:TEST_CREDENTIAL_PRIMARY]"

    def test_invoke_leaves_the_unified_diff_unmasked(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Return the diff verbatim even when it contains the credential value."""
        _clear_test_credentials(monkeypatch)
        monkeypatch.setenv("TEST_CREDENTIAL_PRIMARY", "xq7")
        provider = _DirectSubclassProvider(
            ("TEST_CREDENTIAL_PRIMARY", "TEST_CREDENTIAL_FALLBACK"),
            _response_containing_secret(AgentResponseStatus.PROPOSED_PATCH, "xq7"),
        )

        response = provider.invoke(AgentRequestBuilder().with_worktree_path(tmp_path).build())

        assert response.unified_diff == "+xq7\n"

    def test_invoke_masks_a_secret_matched_by_name_suffix_when_the_descriptor_declares_no_credentials(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SVC_SECRET", "s3cr3t-value")
        provider = _DirectSubclassProvider((), _response_containing_secret(AgentResponseStatus.NO_OP, "s3cr3t-value"))

        response = provider.invoke(AgentRequestBuilder().with_worktree_path(tmp_path).build())

        assert response.errors == ["first [REDACTED:SVC_SECRET]", "second"]

    def test_invoke_masks_the_alternative_credential_that_was_not_used(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TEST_CREDENTIAL_PRIMARY", "xq7")
        monkeypatch.setenv("TEST_CREDENTIAL_FALLBACK", "zk9")
        provider = _DirectSubclassProvider(
            ("TEST_CREDENTIAL_PRIMARY", "TEST_CREDENTIAL_FALLBACK"),
            _response_containing_secret(AgentResponseStatus.PROVIDER_ERROR, "zk9"),
        )

        response = provider.invoke(AgentRequestBuilder().with_worktree_path(tmp_path).build())

        assert response.raw_text == "raw [REDACTED:TEST_CREDENTIAL_FALLBACK]"

    def test_invoke_masks_the_rotated_credential_on_the_next_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TEST_CREDENTIAL_PRIMARY", "xq7")
        provider = _DirectSubclassProvider(
            ("TEST_CREDENTIAL_PRIMARY", "TEST_CREDENTIAL_FALLBACK"),
            _response_containing_secret(AgentResponseStatus.PROVIDER_ERROR, "rot8"),
        )
        request = AgentRequestBuilder().with_worktree_path(tmp_path).build()

        first = provider.invoke(request)
        monkeypatch.setenv("TEST_CREDENTIAL_PRIMARY", "rot8")
        second = provider.invoke(request)

        assert first.errors == ["first rot8", "second"]
        assert second.errors == ["first [REDACTED:TEST_CREDENTIAL_PRIMARY]", "second"]

    def test_invoke_returns_a_response_without_secrets_unchanged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TEST_CREDENTIAL_PRIMARY", "xq7")
        expected = AgentResponse(
            status=AgentResponseStatus.NO_OP, errors=["plain"], raw_text="raw", summary="done", duration_ms=3
        )
        provider = _DirectSubclassProvider(("TEST_CREDENTIAL_PRIMARY", "TEST_CREDENTIAL_FALLBACK"), expected)

        response = provider.invoke(AgentRequestBuilder().with_worktree_path(tmp_path).build())

        assert response == expected


_OVERRIDE_MESSAGE = (
    "Agent environment override '{name}' conflicts with provider or Dovo controls (AGENT_ENV_OVERRIDE_INVALID). "
    "Fix: remove it from agents.{provider}.env_passthrough or the agent step's env."
)


class EnvPreflightConformanceTests:
    @pytest.mark.parametrize("mode", [pytest.param("allowlist", id="allowlist"), pytest.param("inherit", id="inherit")])
    def test_reserved_override_returns_provider_error_without_calling_the_provider(
        self, mode: AgentEnvMode, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TEST_CREDENTIAL_PRIMARY", "xq7")
        provider = _DirectSubclassProvider(
            ("TEST_CREDENTIAL_PRIMARY", "TEST_CREDENTIAL_FALLBACK"), control_envs=("TEST_CONTROL",)
        )
        request = AgentRequestBuilder().with_worktree_path(tmp_path).with_env({"TEST_CONTROL": "x"}).with_env_mode(mode)

        response = provider.invoke(request.build())

        assert response.status == AgentResponseStatus.PROVIDER_ERROR
        assert response.errors == [_OVERRIDE_MESSAGE.format(name="TEST_CONTROL", provider="unit-test")]
        assert provider.invoke_calls == []

    def test_provider_without_descriptor_skips_the_env_preflight(self, tmp_path: Path) -> None:
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
            [_OVERRIDE_MESSAGE.format(name=name, provider=token)] for name in spec.control_envs
        ]
        assert backend_calls == []


class InvokeEnvRedactionTests:
    def test_explicit_step_env_secret_and_proxy_password_are_masked_in_every_response_field(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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
