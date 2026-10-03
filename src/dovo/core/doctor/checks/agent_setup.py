"""Diagnostic check validating active agent provider credentials and model configuration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from dovo.core.agents.copilot import resolve_copilot_token
from dovo.core.config.models import AgentConfig, AgentProvider
from dovo.core.doctor.models import CheckCategory, CheckStatus, DiagnosticCheckResult, DoctorContext

PROVIDER_CREDENTIAL_RESOLVERS: Final[dict[AgentProvider, tuple[Callable[[], str | None], str]]] = {
    "copilot": (resolve_copilot_token, "GH_TOKEN or GITHUB_TOKEN"),
}


class AgentSetupCheck:
    """Diagnostic check validating the active agent provider's credential and model configuration."""

    check_id: str = "agent.setup"
    name: str = "Agent Setup Check"
    category: CheckCategory = CheckCategory.AGENT

    def execute(self, context: DoctorContext) -> DiagnosticCheckResult:
        """Validate the active provider's required credential, if any, and its configured model."""
        agent_config = context.config.agent if context.config is not None else AgentConfig()

        missing_env = _missing_credential_env(agent_config.provider)
        if missing_env is not None:
            return _key_missing_result(self.check_id, self.name, self.category, agent_config.provider, missing_env)

        if agent_config.model is None:
            return _no_model_result(self.check_id, self.name, self.category, agent_config.provider)

        return _ok_result(self.check_id, self.name, self.category, agent_config.provider, agent_config.model)


def _missing_credential_env(provider: AgentProvider) -> str | None:
    """Return the missing required environment variable name for provider, or None when satisfied or unrequired."""
    resolver_entry = PROVIDER_CREDENTIAL_RESOLVERS.get(provider)
    if resolver_entry is None:
        return None

    resolver, env_name = resolver_entry
    if resolver() is None:
        return env_name

    return None


def _key_missing_result(
    check_id: str, name: str, category: CheckCategory, provider: AgentProvider, env_name: str
) -> DiagnosticCheckResult:
    """Build the FAILED DOCTOR_AGENT_KEY_MISSING result naming the missing environment variable."""
    message = f"Agent provider '{provider}' is missing required credential '{env_name}'."
    return DiagnosticCheckResult(
        check_id=check_id,
        name=name,
        category=category,
        status=CheckStatus.FAILED,
        message=message,
        details={"provider": provider, "missing_env_var": env_name},
        duration_ms=0.0,
        error_code="DOCTOR_AGENT_KEY_MISSING",
        errors=[message],
        warnings=[],
        fixes=[],
    )


def _no_model_result(
    check_id: str, name: str, category: CheckCategory, provider: AgentProvider
) -> DiagnosticCheckResult:
    """Build the WARNING DOCTOR_AGENT_NO_MODEL result for a provider configured with no model."""
    message = f"Agent provider '{provider}' has no model configured."
    return DiagnosticCheckResult(
        check_id=check_id,
        name=name,
        category=category,
        status=CheckStatus.WARNING,
        message=message,
        details={"provider": provider},
        duration_ms=0.0,
        error_code="DOCTOR_AGENT_NO_MODEL",
        errors=[],
        warnings=[message],
        fixes=[],
    )


def _ok_result(
    check_id: str, name: str, category: CheckCategory, provider: AgentProvider, model: str
) -> DiagnosticCheckResult:
    """Build the OK result carrying the configured provider and model."""
    return DiagnosticCheckResult(
        check_id=check_id,
        name=name,
        category=category,
        status=CheckStatus.OK,
        message=f"Agent provider '{provider}' is configured with model '{model}'.",
        details={"provider": provider, "model": model},
        duration_ms=0.0,
        error_code=None,
        errors=[],
        warnings=[],
        fixes=[],
    )
