"""The artifacts package's domain error."""

from dovo.common.exceptions import DomainError


class ArtifactError(DomainError):
    """Business failure listing, downloading or pruning session artifacts."""
