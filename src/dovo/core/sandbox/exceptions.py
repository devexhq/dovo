"""Domain exceptions for sandbox lifecycle and patch integration."""

from __future__ import annotations


class SandboxError(RuntimeError):
    """Base exception for all sandbox domain operations."""


class SandboxConfigError(SandboxError):
    """Raised when sandbox configuration is missing or invalid."""


class SandboxCapacityError(SandboxError):
    """Raised when active sandbox count reaches configured capacity limit."""
