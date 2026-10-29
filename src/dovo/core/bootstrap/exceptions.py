"""The bootstrap package's domain error."""

from dovo.common.exceptions import DomainError


class BootstrapError(DomainError):
    """Business failure creating or validating the `.dovo/` directory tree or its bootstrap metadata."""
