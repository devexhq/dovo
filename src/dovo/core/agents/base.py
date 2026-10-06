"""Shared agent provider base class and provider descriptor."""

from __future__ import annotations

import abc
import time
from collections.abc import Callable
from dataclasses import dataclass

from dovo.core.agents.credentials import missing_credential_error, resolve_credential
from dovo.core.agents.models import AgentRequest, AgentResponse
from dovo.core.agents.responses import provider_error_response


def elapsed_ms(started: float) -> int:
    """Return elapsed milliseconds since the start timestamp."""
    return int((time.monotonic() - started) * 1000)


class BaseAgentProvider(abc.ABC):
    """Provider-agnostic contract for invoking an agent.

    ``invoke`` checks the descriptor's credentials before delegating to ``_invoke``; subclasses implement ``_invoke`` and
    never override ``invoke``.
    """

    def invoke(self, request: AgentRequest) -> AgentResponse:
        """Return PROVIDER_ERROR with the canonical diagnostic when no credential is usable, else run ``_invoke``."""
        started = time.monotonic()

        missing = self._credential_preflight()
        if missing is not None:
            return provider_error_response(duration_ms=elapsed_ms(started), detail=missing)

        return self._invoke(request)

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
    executable, and the capability flags describe current behavior, not planned features.
    """

    token: str
    credential_envs: tuple[str, ...]
    requires_model: bool
    supports_tool_policy: bool
    supports_os_sandbox: bool
    build: Callable[[], BaseAgentProvider]
    binary: str | None = None
