"""Exceptions for unified-diff parsing and validation."""


class MalformedDiffHeader(Exception):
    """Raised when a ``diff --git`` header cannot be parsed."""
