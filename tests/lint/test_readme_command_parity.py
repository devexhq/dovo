"""Parity invariant: the command surface in README.md matches the commands registered in `cli.py`."""

from __future__ import annotations

import re
from typing import Final

import typer
from typer.core import TyperGroup

from dovo.cli.cli import app
from tests.lint.astlib import REPO_ROOT

README_PATH: Final = REPO_ROOT / "README.md"
SURFACE_HEADING: Final = "## Current command surface"
COMMAND_SPAN: Final = re.compile(r"`dovo ([^`]+)`")
COMMAND_WORD: Final = re.compile(r"[a-z][a-z-]*")


def _usage_paths(usage: str) -> set[tuple[str, ...]]:
    """Return the command paths named by one README usage string."""
    paths: set[tuple[str, ...]] = set()
    words: list[str] = []
    for token in usage.split():
        # `[show <id>]` opens an optional subcommand; `[session-id]` is a closed optional argument.
        opens_optional = token.startswith("[") and not token.endswith("]")
        word = token.removeprefix("[") if opens_optional else token
        if not COMMAND_WORD.fullmatch(word):
            break
        if opens_optional:
            paths.add(tuple(words))
        words.append(word)
    paths.add(tuple(words))
    return paths


def _documented_paths() -> set[tuple[str, ...]]:
    """Return the command paths listed under the README command-surface heading."""
    readme = README_PATH.read_text(encoding="utf-8")
    surface = readme.split(SURFACE_HEADING, 1)[1].split("\n## ", 1)[0]
    return {path for usage in COMMAND_SPAN.findall(surface) for path in _usage_paths(usage)}


def _visible_children(group: TyperGroup) -> dict[str, object]:
    """Return a group's registered subcommands that are not hidden."""
    return {name: child for name, child in group.commands.items() if not getattr(child, "hidden", False)}


def _registered_paths() -> set[tuple[str, ...]]:
    """Return the path of every visible leaf command, plus each group that is itself invokable."""
    paths: set[tuple[str, ...]] = set()
    pending: list[tuple[object, tuple[str, ...]]] = [(typer.main.get_command(app), ())]
    while pending:
        command, prefix = pending.pop()
        if not (isinstance(command, TyperGroup) and command.commands):
            paths.add(prefix)
            continue
        if command.invoke_without_command or command.callback is not None:
            paths.add(prefix)
        pending.extend((child, (*prefix, name)) for name, child in _visible_children(command).items())
    paths.discard(())
    return paths


class ReadmeCommandParityTests:
    """README must list exactly the commands the CLI registers."""

    def test_every_documented_command_is_registered(self) -> None:
        """No README command path is missing from the CLI app."""
        registered = _registered_paths()
        registered_prefixes = {path[:depth] for path in registered for depth in range(1, len(path) + 1)}

        assert sorted(_documented_paths() - registered_prefixes) == []

    def test_every_registered_command_is_documented(self) -> None:
        """No registered leaf command is absent from the README."""
        documented = _documented_paths()

        undocumented = {
            path for path in _registered_paths() if not any(path[: len(doc)] == doc for doc in documented if doc)
        }

        assert sorted(undocumented) == []
