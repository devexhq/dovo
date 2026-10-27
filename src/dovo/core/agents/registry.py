"""Registry of agent provider descriptors.

``PROVIDERS`` is the single set of provider tokens; a ``default_agent`` or ``--agent`` value outside it is unsupported.
"""

from __future__ import annotations

from dovo.core.agents.base import ProviderSpec
from dovo.core.agents.claude import CLAUDE_PROVIDER_SPEC
from dovo.core.agents.copilot import COPILOT_PROVIDER_SPEC

PROVIDERS: dict[str, ProviderSpec] = {spec.token: spec for spec in (CLAUDE_PROVIDER_SPEC, COPILOT_PROVIDER_SPEC)}


def unsupported_provider_message(token: str) -> str:
    """Return the unsupported-provider message naming token and the sorted registered tokens."""
    return f"Unsupported agent provider '{token}'. Supported: {', '.join(sorted(PROVIDERS))}."
