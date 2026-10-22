"""The first CLI word of the running invocation, bound by the CLI root callback."""

from __future__ import annotations

from contextvars import ContextVar, Token

_CLI_COMMAND: ContextVar[str | None] = ContextVar("dovo_cli_command", default=None)


def bind_cli_command(command: str | None) -> Token[str | None]:
    """Bind the running invocation's first CLI word and return the token that resets it."""
    return _CLI_COMMAND.set(command)


def reset_cli_command(token: Token[str | None]) -> None:
    """Restore the binding that was active before bind_cli_command returned token."""
    _CLI_COMMAND.reset(token)


def current_cli_command() -> str | None:
    """Return the bound first CLI word, or None outside the CLI."""
    return _CLI_COMMAND.get()
