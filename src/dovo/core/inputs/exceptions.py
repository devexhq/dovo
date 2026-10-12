"""Exceptions raised while rendering template placeholders."""

from __future__ import annotations


class NamespaceResolutionError(Exception):
    """A namespace provider could not render a referenced placeholder; the message is user-facing."""
