"""Diagnostic check validating active agent provider credentials and model configuration."""

from __future__ import annotations

from dovo.core.agents.credentials import resolve_credential
from dovo.core.agents.registry import PROVIDERS
from dovo.core.config.models import AgentConfig, AgentProvider
from dovo.core.diagnostics.models import CheckCategory, CheckStatus, DiagnosticCheckResult, DiagnosticsContext


class AgentSetupCheck:
    """Diagnostic check validating the active agent provider's credential and model configuration."""

    check_id: str = "agent.setup"
    name: str = "Agent Setup Check"
    category: CheckCategory = CheckCategory.AGENT

    def execute(self, context: DiagnosticsContext) -> DiagnosticCheckResult:
        """Validate the active provider's required credential, if any, and its configured model entry."""
        agent_config = context.config.agent if context.config is not None else AgentConfig()

        missing_env = _missing_credential_env(agent_config.provider)
        if missing_env is not None:
            return _key_missing_result(self.check_id, self.name, self.category, agent_config.provider, missing_env)

        model = agent_config.active_model
        if model is None:
            return _no_model_result(self.check_id, self.name, self.category, agent_config.provider)

        return _ok_result(self.check_id, self.name, self.category, agent_config.provider, model)


def _missing_credential_env(provider: str) -> str | None:
    """Return the ' or '-joined credential names when the registry declares credentials and none resolves, else None."""
    spec = PROVIDERS.get(provider)
    if spec is None or not spec.credential_envs:
        return None

    if resolve_credential(spec.credential_envs) is None:
        return " or ".join(spec.credential_envs)

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
