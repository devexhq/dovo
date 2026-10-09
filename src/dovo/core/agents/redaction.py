"""Invocation-time masking of agent responses at the public adapter boundary."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from dovo.common.redact import SecretRedactor, credential_secrets, load_env_file_secrets, suffix_secrets
from dovo.core.agents.models import AgentResponse


def build_response_redactor(
    credential_envs: Sequence[str], env_file_dir: Path, extra_secrets: Sequence[tuple[str, str]] = ()
) -> SecretRedactor:
    """Build this invocation's redactor from the live environment, the provider's credential envs, ``env_file_dir``/.env, and ``extra_secrets``."""
    return SecretRedactor(
        [
            *credential_secrets(os.environ, credential_envs),
            *suffix_secrets(os.environ),
            *load_env_file_secrets(env_file_dir, credential_envs),
            *extra_secrets,
        ]
    )


def redact_agent_response(response: AgentResponse, redactor: SecretRedactor) -> AgentResponse:
    """Return a copy of ``response`` with every errors entry, raw_text, and summary masked and all other fields unchanged."""
    return response.model_copy(
        update={
            "errors": [redactor.redact_text(error) for error in response.errors],
            "raw_text": None if response.raw_text is None else redactor.redact_text(response.raw_text),
            "summary": None if response.summary is None else redactor.redact_text(response.summary),
        }
    )
