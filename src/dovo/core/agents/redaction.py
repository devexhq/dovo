"""Invocation-time masking of agent responses at the public adapter boundary."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from dovo.common.redact import SecretRedactor
from dovo.core.agents.models import AgentResponse


def build_response_redactor(credential_envs: Sequence[str], env_file_dir: Path) -> SecretRedactor:
    """Build this invocation's redactor from the live environment, the provider's credential envs, and ``env_file_dir``/.env."""
    return SecretRedactor.from_environment(credential_envs=credential_envs, env_file_dir=env_file_dir)


def redact_agent_response(response: AgentResponse, redactor: SecretRedactor) -> AgentResponse:
    """Return a copy of ``response`` with every errors entry, raw_text, and summary masked and all other fields unchanged."""
    return response.model_copy(
        update={
            "errors": [redactor.redact_text(error) for error in response.errors],
            "raw_text": None if response.raw_text is None else redactor.redact_text(response.raw_text),
            "summary": None if response.summary is None else redactor.redact_text(response.summary),
        }
    )
