"""Exceptions for the blueprint execution engine."""

from dovo.common.exceptions import DomainError


class EngineError(DomainError):
    """The engine package's business failure: an ErrorCode, a message and remediations."""
