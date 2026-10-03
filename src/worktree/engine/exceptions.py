"""Exceptions for the blueprint execution engine."""

from pathlib import Path

from worktree.core.inputs import InputResolveResult
from worktree.engine.models import EngineResumeStatus


class EngineError(Exception):
    """Base class for engine errors."""


class EngineRuntimeError(EngineError):
    """Raised when Engine cannot start a run."""


class EngineInputError(EngineError):
    """Raised when Engine cannot resolve blueprint inputs."""

    def __init__(self, result: InputResolveResult) -> None:
        self.result = result
        if result.errors:
            message = result.errors[0]
        elif result.missing:
            message = f"Missing required input '{result.missing[0]}'."
        else:
            message = "Failed to resolve blueprint inputs."
        super().__init__(message)


class EngineResumeError(EngineError):
    """Raised when Engine cannot start a resume."""

    def __init__(self, status: EngineResumeStatus, message: str) -> None:
        self.status = status
        super().__init__(message)


class EngineSnapshotMissingError(EngineError):
    """Raised when a manifest-referenced session snapshot definition file is missing from disk."""

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(f"Snapshot definition file is missing: '{path}'.")
