"""Invariant: every registered JSON event type declares a stable dotted name of its own.

The registered event types are the models in FORMATTER_GROUPS; each is written under the name its formatter declares,
never one derived from a class, module or command path.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import ClassVar, Final, Never

import pytest
from rich.console import RenderableType
from rich.text import Text

from dovo.cli.ui.events import MessageEvent
from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.catalog.common import CatalogFormatter
from dovo.cli.ui.formatters.registry import FORMATTER_GROUPS
from dovo.core.catalog.models import CatalogItemType

EVENT_NAME: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z_]*(\.[a-z][a-z_]*)*$")

type RegisteredFormatter = tuple[type, ComponentFormatter[Never]]


def _registered_formatters() -> list[RegisteredFormatter]:
    """Return (model, formatter) for every event type registered in FORMATTER_GROUPS."""
    pairs: list[RegisteredFormatter] = []
    for group in FORMATTER_GROUPS.values():
        pairs.extend(group(None).items())
    return sorted(pairs, key=lambda pair: pair[0].__name__)


def declared_names(formatter: ComponentFormatter[Never]) -> tuple[str, ...] | None:
    """Return every name formatter declares, or None when it declares none."""
    try:
        return formatter.declared_event_types()
    except AttributeError:
        return None


def name_violations(formatters: list[ComponentFormatter[Never]]) -> list[str]:
    """Report a formatter that declares no name, a name outside the grammar, and a name declared more than once."""
    violations: list[str] = []
    counts: Counter[str] = Counter()
    for formatter in formatters:
        label = type(formatter).__name__
        names = declared_names(formatter)
        if names is None:
            violations.append(f"{label} declares no event_type")
            continue
        counts.update(names)
        violations.extend(
            f"{label} declares {name!r}, which breaks the event name grammar"
            for name in names
            if not EVENT_NAME.match(name)
        )
    violations.extend(f"{name!r} is declared {count} times" for name, count in sorted(counts.items()) if count > 1)
    return violations


REGISTERED: Final[list[RegisteredFormatter]] = _registered_formatters()


class _UnnamedFormatter(ComponentFormatter[MessageEvent]):
    """A formatter that declares no event name."""

    def to_rich(self, data: MessageEvent) -> RenderableType:
        """Render nothing."""
        return Text("")


class _CamelCaseFormatter(_UnnamedFormatter):
    """A formatter whose name breaks the grammar."""

    event_type: ClassVar[str] = "MessageEvent"


class _MessageFormatter(_UnnamedFormatter):
    """A formatter that declares `message`."""

    event_type: ClassVar[str] = "message"


class _OtherMessageFormatter(_UnnamedFormatter):
    """A second formatter that also declares `message`."""

    event_type: ClassVar[str] = "message"


class EventNameLintTests:
    """Every registered event type declares a unique name matching the grammar."""

    def test_registry_lists_event_types(self) -> None:
        """Find at least the run events and the command results."""
        assert len(REGISTERED) >= 40

    def test_every_registered_event_type_declares_a_unique_valid_name(self) -> None:
        """Refuse a missing name, a name outside the grammar and a name shared by two event types."""
        assert name_violations([formatter for _, formatter in REGISTERED]) == []

    def test_every_catalog_formatter_names_every_item_type(self) -> None:
        """Require a catalog formatter to declare a name for blueprints and for steps."""
        catalog = [formatter for _, formatter in REGISTERED if isinstance(formatter, CatalogFormatter)]

        assert len(catalog) == 5
        for formatter in catalog:
            assert set(formatter.event_types) == set(CatalogItemType), type(formatter).__name__

    @pytest.mark.parametrize(
        ("formatters", "expected"),
        [
            pytest.param([_UnnamedFormatter()], ["_UnnamedFormatter declares no event_type"], id="no_name"),
            pytest.param(
                [_CamelCaseFormatter()],
                ["_CamelCaseFormatter declares 'MessageEvent', which breaks the event name grammar"],
                id="bad_grammar",
            ),
            pytest.param(
                [_MessageFormatter(), _OtherMessageFormatter()], ["'message' is declared 2 times"], id="duplicate"
            ),
        ],
    )
    def test_lint_rejects_planted_violations(
        self, formatters: list[ComponentFormatter[Never]], expected: list[str]
    ) -> None:
        """Report each planted violation with a message naming the offender."""
        assert name_violations(formatters) == expected
