"""Shared agent provider base class and provider descriptor."""

from __future__ import annotations

import abc
import time
from collections.abc import Callable
from dataclasses import dataclass

from dovo.common.redact import SecretRedactor
from dovo.core.agents.credentials import missing_credential_error, resolve_credential
from dovo.core.agents.environment import forwarded_env_secrets, validate_agent_env_request
from dovo.core.agents.models import AgentRequest, AgentResponse
from dovo.core.agents.redaction import build_response_redactor, redact_agent_response
from dovo.core.agents.responses import provider_error_response


def elapsed_ms(started: float) -> int:
    """Return elapsed milliseconds since the start timestamp."""
    return int((time.monotonic() - started) * 1000)


class BaseAgentProvider(abc.ABC):
    """Provider-agnostic contract for invoking an agent.

    ``invoke`` checks the descriptor's credentials before delegating to ``_invoke`` and masks secrets in the response's
    errors, raw_text, and summary; subclasses implement ``_invoke`` and never override ``invoke``.
    """

    def invoke(self, request: AgentRequest) -> AgentResponse:
        """Return the masked PROVIDER_ERROR when no credential is usable or an environment override is reserved, else the masked ``_invoke`` response."""
        started = time.monotonic()
        redactor = self._response_redactor(request)

        failure = self._preflight_failure(request, started)
        response = self._invoke(request) if failure is None else failure

        return redact_agent_response(response, redactor)

    def _preflight_failure(self, request: AgentRequest, started: float) -> AgentResponse | None:
        """Return the PROVIDER_ERROR for a missing credential or a reserved environment override, else None."""
        missing = self._credential_preflight()
        if missing is not None:
            return provider_error_response(duration_ms=elapsed_ms(started), detail=missing)

        override_error = self._env_preflight(request)
        if override_error is not None:
            return provider_error_response(duration_ms=elapsed_ms(started), errors=[override_error])

        return None

    def _env_preflight(self, request: AgentRequest) -> str | None:
        """Return the fixed AGENT_ENV_OVERRIDE_INVALID message when a literal passthrough or step env entry is reserved, else None."""
        spec = self._provider_spec()

        return None if spec is None else validate_agent_env_request(spec, request)

    def _response_redactor(self, request: AgentRequest) -> SecretRedactor:
        """Build the invocation-time redactor for this provider's descriptor, the request's worktree, and its forwarded env secrets."""
        spec = self._provider_spec()

        return build_response_redactor(
            () if spec is None else spec.credential_envs, request.worktree_path, forwarded_env_secrets(request)
        )

    @abc.abstractmethod
    def _invoke(self, request: AgentRequest) -> AgentResponse:
        """Invoke the agent for ``request`` and return its response."""
        raise NotImplementedError

    def _provider_spec(self) -> ProviderSpec | None:
        """Return this provider's registered descriptor, or None when it declares none."""
        return None

    def _credential_preflight(self) -> str | None:
        """Return the canonical missing-credential diagnostic when no declared alternative is usable, else None."""
        spec = self._provider_spec()
        if spec is None or not spec.credential_envs:
            return None

        if resolve_credential(spec.credential_envs) is None:
            return missing_credential_error(spec)

        return None


@dataclass(frozen=True)
class ProviderSpec:
    """Static descriptor of one agent provider and its implemented capabilities.

    ``credential_envs`` are alternatives (any one suffices; empty means none required), ``binary`` is set only for a fixed
    executable, ``control_envs`` are adapter-owned variables no environment layer may forward or override, and the capability flags describe current behavior, not planned features.
    """

    token: str
    credential_envs: tuple[str, ...]
    requires_model: bool
    supports_tool_policy: bool
    supports_os_sandbox: bool
    build: Callable[[], BaseAgentProvider]
    binary: str | None = None
    control_envs: tuple[str, ...] = ()
