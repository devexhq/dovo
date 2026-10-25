"""The agents package's domain error."""

from dovo.common.exceptions import DomainError


class AgentError(DomainError):
    """Business failure preparing or running an agent attempt."""
