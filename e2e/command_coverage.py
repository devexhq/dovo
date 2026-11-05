"""Command coverage for the e2e suite: which commands the binary offers, which scenarios ran, and which are known gaps."""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

ZSH_REPLY_PREFIX = "_arguments '*: :(("
ZSH_REPLY_SUFFIX = "))'"
ZSH_ENTRY = re.compile(r'"((?:[^"]|"")*)"(?::"(?:[^"]|"")*")?')
ROOT_OPTIONS_WITH_VALUE = frozenset({"-p", "--path"})


@dataclass(frozen=True)
class CommandTree:
    """The binary's command tree: each command path mapped to the names of its subcommands."""

    children: dict[tuple[str, ...], tuple[str, ...]]

    def leaves(self) -> frozenset[str]:
        """Return every command path with no subcommands, space-joined, excluding the root."""
        return frozenset(" ".join(path) for path, names in self.children.items() if path and not names)

    def resolve(self, argv: Iterable[str]) -> str | None:
        """Return the command an argv runs, or None when it stops at a group or names no command."""
        path: tuple[str, ...] = ()
        tokens = iter(argv)
        for token in tokens:
            names = self.children.get(path, ())
            if not names:
                break
            if token in ROOT_OPTIONS_WITH_VALUE and not path:
                next(tokens, None)
            elif token in names:
                path = (*path, token)
        if path and not self.children.get(path, ()):
            return " ".join(path)
        return None


def parse_completion(output: str) -> tuple[str, ...]:
    """Return the names a zsh completion reply offers, or none for a `_files` or empty reply."""
    reply = output.strip()
    if not reply.startswith(ZSH_REPLY_PREFIX) or not reply.endswith(ZSH_REPLY_SUFFIX):
        return ()
    body = reply.removeprefix(ZSH_REPLY_PREFIX).removesuffix(ZSH_REPLY_SUFFIX)
    return tuple(_unescape(match.group(1)) for match in ZSH_ENTRY.finditer(body))


def _unescape(name: str) -> str:
    """Undo the quoting Typer applies to a zsh completion name."""
    return name.replace('""', '"').replace("''", "'").replace("\\\\:", ":").replace("\\$", "$").replace("\\`", "`")


def read_command_tree(complete: Callable[[tuple[str, ...]], str]) -> CommandTree:
    """Walk the command tree breadth-first, asking `complete` for each path's zsh completion reply."""
    children: dict[tuple[str, ...], tuple[str, ...]] = {}
    pending: deque[tuple[str, ...]] = deque([()])
    while pending:
        path = pending.popleft()
        names = parse_completion(complete(path))
        children[path] = names
        pending.extend((*path, name) for name in names)
    return CommandTree(children=children)


def read_gap_list(text: str) -> frozenset[str]:
    """Return the command paths a gap list names, ignoring blank lines and `#` comments."""
    lines = (line.split("#", 1)[0].strip() for line in text.splitlines())
    return frozenset(" ".join(line.split()) for line in lines if line)


@dataclass(frozen=True)
class CoverageReport:
    """Where the commands the binary offers, the commands scenarios ran and the gap list disagree."""

    uncovered: tuple[str, ...]
    stale_gaps: tuple[str, ...]
    unknown_gaps: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """True when every offered command is covered or listed, and every listed command is offered and uncovered."""
        return not (self.uncovered or self.stale_gaps or self.unknown_gaps)

    def lines(self, gap_file: str) -> list[str]:
        """Return the report as terminal lines, one group per disagreement, each command named."""
        if self.ok:
            return ["every command the binary offers has a scenario or is listed in " + gap_file]
        groups = (
            (self.uncovered, f"commands with no scenario (add a scenario, or a line in {gap_file}):"),
            (self.stale_gaps, f"commands in {gap_file} that now have a scenario (remove their lines):"),
            (self.unknown_gaps, f"commands in {gap_file} that the binary does not offer (remove their lines):"),
        )
        lines: list[str] = []
        for commands, heading in groups:
            if commands:
                lines.append(heading)
                lines.extend(f"  {command}" for command in commands)
        return lines


def compare(offered: frozenset[str], covered: frozenset[str], gaps: frozenset[str]) -> CoverageReport:
    """Compare the offered commands with the covered ones and the gap list."""
    return CoverageReport(
        uncovered=tuple(sorted(offered - covered - gaps)),
        stale_gaps=tuple(sorted(gaps & covered)),
        unknown_gaps=tuple(sorted(gaps - offered)),
    )


@dataclass
class CommandRecorder:
    """The argv of every binary invocation made while a scenario body is running."""

    active: bool = False
    invocations: list[tuple[str, ...]] = field(default_factory=list)

    def record(self, args: Iterable[str]) -> None:
        """Keep the argv when a scenario body is running; ignore it otherwise."""
        if self.active:
            self.invocations.append(tuple(args))

    def covered(self, tree: CommandTree) -> frozenset[str]:
        """Return the commands the recorded invocations ran."""
        return frozenset(command for args in self.invocations if (command := tree.resolve(args)) is not None)
