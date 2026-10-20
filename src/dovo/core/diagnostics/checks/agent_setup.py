"""Diagnostic check validating active agent provider credentials and model configuration."""

from __future__ import annotations

from dovo.core.agents.base import ProviderSpec
from dovo.core.agents.credentials import resolve_credential
from dovo.core.agents.registry import PROVIDERS
from dovo.core.config import agent_settings
from dovo.core.config.models import DEFAULT_AGENT, AgentProvider, AgentSettings
from dovo.core.diagnostics.models import CheckCategory, CheckStatus, DiagnosticCheckResult, DiagnosticsContext


class AgentSetupCheck:
    """Diagnostic check validating the active agent provider's credential and model configuration."""

    check_id: str = "agent.setup"
    name: str = "Agent Setup Check"
    category: CheckCategory = CheckCategory.AGENT

    def execute(self, context: DiagnosticsContext) -> DiagnosticCheckResult:
        """Validate the active provider's required credential, if any, and its configured model entry."""
        provider = context.config.default_agent if context.config is not None else DEFAULT_AGENT
        settings = agent_settings(context.config) if context.config is not None else AgentSettings()
        spec = PROVIDERS.get(provider)
        label = None if spec is None or spec.auth_label is None else spec.auth_label()

        if spec is not None and _credential_missing(spec, label):
            return _key_missing_result(self.check_id, self.name, self.category, provider, spec)

        model = settings.model
        if model is None:
            return _no_model_result(self.check_id, self.name, self.category, provider, label)

        message, details = _with_auth_label(
            label,
            f"Agent provider '{provider}' is configured with model '{model}'",
            {"provider": provider, "model": model},
        )

        return _ok_result(self.check_id, self.name, self.category, message, details)


def _credential_missing(spec: ProviderSpec, label: str | None) -> bool:
    """Return True when the spec declares credentials and neither one nor a labelled authentication satisfies them."""
    return bool(spec.credential_envs) and resolve_credential(spec.credential_envs) is None and label is None


def _with_auth_label(label: str | None, sentence: str, details: dict[str, str]) -> tuple[str, dict[str, str]]:
    """Return the sentence with the authentication label appended, and the label recorded in details, when one is given."""
    if label is None:
        return f"{sentence}.", details

    return f"{sentence} (authentication: {label}).", {**details, "auth": label}


def _key_missing_result(
    check_id: str, name: str, category: CheckCategory, provider: AgentProvider, spec: ProviderSpec
) -> DiagnosticCheckResult:
    """Build the FAILED DOCTOR_AGENT_KEY_MISSING result naming the missing environment variables."""
    env_names = spec.credential_envs
    joined_names = " or ".join(env_names)
    message = f"Agent provider '{provider}' is missing required credential '{joined_names}'."
    return DiagnosticCheckResult(
        check_id=check_id,
        name=name,
        category=category,
        status=CheckStatus.FAILED,
        message=message,
        details={"provider": provider, "missing_env_var": joined_names, "credential_envs": list(env_names)},
        error_code="DOCTOR_AGENT_KEY_MISSING",
        errors=[message],
        warnings=[],
        fixes=[] if spec.auth_fix is None else [spec.auth_fix],
    )


def _no_model_result(
    check_id: str, name: str, category: CheckCategory, provider: AgentProvider, label: str | None
) -> DiagnosticCheckResult:
    """Build the WARNING DOCTOR_AGENT_NO_MODEL result for a provider configured with no model."""
    message, details = _with_auth_label(
        label, f"Agent provider '{provider}' has no model configured", {"provider": provider}
    )

    return DiagnosticCheckResult(
        check_id=check_id,
        name=name,
        category=category,
        status=CheckStatus.WARNING,
        message=message,
        details=details,
        error_code="DOCTOR_AGENT_NO_MODEL",
        errors=[],
        warnings=[message],
        fixes=[],
    )


def _ok_result(
    check_id: str, name: str, category: CheckCategory, message: str, details: dict[str, str]
) -> DiagnosticCheckResult:
    """Build the OK result carrying the configured provider and model."""
    return DiagnosticCheckResult(
        check_id=check_id,
        name=name,
        category=category,
        status=CheckStatus.OK,
        message=message,
        details=details,
        error_code=None,
        errors=[],
        warnings=[],
        fixes=[],
    )
