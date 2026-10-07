"""Exceptions raised by common filesystem services."""

from pathlib import Path


class InvalidGlobalRootError(ValueError):
    """Raised when the global Dovo root is itself a Git repository."""


class WorkspaceNotInitializedError(Exception):
    """Raised when a command needs a workspace but no valid project identity exists."""

    def __init__(self, identity_path: Path, *, identity_present: bool) -> None:
        """Build the two-line WORKSPACE_NOT_INITIALIZED message, adding the --force hint when the identity file exists."""
        message = (
            f"Workspace is not initialized: no valid project identity at '{identity_path}' (WORKSPACE_NOT_INITIALIZED).\n"
            "Fix: Run `dovo init` to initialize this workspace."
        )
        if identity_present:
            message += "\nOr run `dovo init --id <project-id> --force` to replace the unusable identity."

        super().__init__(message)
