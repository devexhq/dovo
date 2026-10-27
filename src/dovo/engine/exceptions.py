"""Exceptions for the blueprint execution engine."""

from pathlib import Path

from dovo.common.error_codes import ErrorCode
from dovo.common.exceptions import DomainError
from dovo.core.inputs import InputResolveResult


class EngineError(DomainError):
    """The engine package's business failure: an ErrorCode, a message and remediations."""


class EngineInputError(EngineError):
    """Raised when Engine cannot resolve blueprint inputs; message is the user-facing input error."""

    def __init__(self, result: InputResolveResult, message: str) -> None:
        self.result = result
        super().__init__(ErrorCode.BLUEPRINT_INPUT_INVALID, message)


class EngineResumeError(EngineError):
    """Raised when Engine cannot start a resume."""


class EngineSnapshotMissingError(EngineError):
    """Raised when a manifest-referenced session snapshot definition file is missing from disk."""

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(ErrorCode.SESSION_SNAPSHOT_MISSING, f"Snapshot definition file is missing: '{path}'.")
