"""Shared environment credential lookup and canonical missing-credential diagnostics.

Leaf module: imports nothing from ``dovo.core.agents`` so ``base.py`` can import it without a cycle.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Protocol


class _CredentialDescriptor(Protocol):
    """Structural view of ``ProviderSpec`` so this leaf module need not import ``base.py``."""

    @property
    def token(self) -> str:
        """Return the provider token named in diagnostics."""
        ...

    @property
    def credential_envs(self) -> tuple[str, ...]:
        """Return the credential environment variable names, in precedence order."""
        ...


def resolve_credential(envs: Sequence[str]) -> str | None:
    """Return the first non-blank value among ``envs``, stripped, or None; reads ``os.environ`` at call time."""
    for name in envs:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value.strip()

    return None


def missing_credential_error(spec: _CredentialDescriptor) -> str:
    """Return the unprefixed missing-credential diagnostic naming ``spec.credential_envs`` in order; raises ValueError when empty."""
    names = spec.credential_envs
    if not names:
        raise ValueError(f"provider '{spec.token}' declares no credential_envs")

    missing_names = " or ".join(names)
    export_commands = " or ".join(f"export {name}=..." for name in names)

    return f"missing {missing_names}. Fix: {export_commands}"
