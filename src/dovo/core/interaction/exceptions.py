"""The interaction package's domain error."""

from dovo.common.exceptions import DomainError


class InteractionError(DomainError):
    """Business failure resolving a human-input prompt."""
