"""Shared agent provider base class and provider descriptor."""

from __future__ import annotations

import abc
from collections.abc import Callable
from dataclasses import dataclass

from dovo.core.agents.models import AgentRequest, AgentResponse, ProviderKind


class BaseAgentProvider(abc.ABC):
    """Provider-agnostic contract for requesting a fix from an agent."""

    @abc.abstractmethod
    def propose_fix(self, request: AgentRequest) -> AgentResponse:
        """Propose a fix for the failure described in ``request``."""
        raise NotImplementedError


@dataclass(frozen=True)
class ProviderSpec:
    """Static descriptor of one agent provider and its implemented capabilities.

    ``credential_envs`` are alternatives (any one suffices; empty means none required), ``binary`` is set only for a fixed
    executable, and the capability flags describe current behavior, not planned features.
    """

    token: str
    kind: ProviderKind
    credential_envs: tuple[str, ...]
    requires_model: bool
    supports_tool_policy: bool
    supports_os_sandbox: bool
    build: Callable[[], BaseAgentProvider]
    binary: str | None = None
