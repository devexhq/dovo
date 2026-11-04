"""The exit a command ends with, derived from its result's error_code."""

from __future__ import annotations

from typing import Protocol

import typer

from dovo.common.error_codes import ErrorCode


class CommandOutcome(Protocol):
    """A command result: its error_code is None exactly when the command succeeded."""

    @property
    def error_code(self) -> ErrorCode | None:
        """The published code of the command's failure, or None when it succeeded."""
        ...


def exit_for(result: CommandOutcome) -> None:
    """Return when result carries no error_code; otherwise end the command with the exit its error_code declares."""
    if result.error_code is not None:
        raise typer.Exit(code=result.error_code.exit_code)
