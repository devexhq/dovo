"""The project identity package's domain error."""

from dovo.common.exceptions import DomainError


class ProjectIdentityError(DomainError):
    """Business failure reading, writing or provisioning `.dovo/project.json`."""
