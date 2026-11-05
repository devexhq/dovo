"""Invariant: every command-result payload starts with the six-field header, built from one view, never by hand.

A command result is a BaseResult model with a formatter in FORMATTER_GROUPS; its formatter declares the view it
dumps as the second ComponentFormatter type argument.
"""

from __future__ import annotations

from enum import StrEnum
from types import NoneType, UnionType
from typing import Final, Never, Union, get_args, get_origin

import pytest
from pydantic import BaseModel, JsonValue
from rich.console import RenderableType
from rich.text import Text

from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.header import HEADER_FIELDS, ResultHeaderView
from dovo.cli.ui.formatters.registry import FORMATTER_GROUPS
from dovo.cli.ui.formatters.status.status_view import StatusView
from dovo.cli.ui.formatters.worktree.worktree_views import WorktreePruneView
from dovo.common.models import BaseResult

# The only views with a top-level status, and the values each admits (#1309, Binding values).
STATUS_VALUES: Final[dict[type[BaseModel], frozenset[str]]] = {
    StatusView: frozenset({"ok", "degraded", "uninitialized"}),
    WorktreePruneView: frozenset({"pruned", "nothing_to_prune", "partial_failure"}),
}

type CommandFormatter = tuple[type[BaseResult], type[ComponentFormatter[Never]]]


def _command_formatters() -> list[CommandFormatter]:
    """Return (result model, formatter class) for every BaseResult model registered in FORMATTER_GROUPS."""
    pairs: list[CommandFormatter] = []
    for group in FORMATTER_GROUPS.values():
        for model_cls, formatter in group(None).items():
            if issubclass(model_cls, BaseResult):
                pairs.append((model_cls, type(formatter)))
    return sorted(pairs, key=lambda pair: pair[0].__name__)


def _declared_view(formatter_cls: type) -> type | None:
    """Return the view type formatter_cls declares as its ComponentFormatter[T, V] argument, or None."""
    for klass in formatter_cls.__mro__:
        for base in getattr(klass, "__orig_bases__", ()):
            if get_origin(base) is ComponentFormatter:
                args = get_args(base)
                return args[1] if len(args) == 2 else None
    return None


def header_view_violations(formatter_cls: type[ComponentFormatter[Never]]) -> list[str]:
    """Report a formatter whose declared view does not inherit the header unchanged and first."""
    name = formatter_cls.__name__
    view = _declared_view(formatter_cls)
    if not isinstance(view, type) or not issubclass(view, ResultHeaderView):
        return [f"{name} declares view {view!r}, which does not subclass ResultHeaderView"]
    fields = view.model_fields
    violations: list[str] = []
    if tuple(fields)[: len(HEADER_FIELDS)] != HEADER_FIELDS:
        violations.append(f"{name}: {view.__name__} does not start with {HEADER_FIELDS}")
    for field_name in HEADER_FIELDS:
        header_field = ResultHeaderView.model_fields[field_name]
        if fields[field_name].annotation != header_field.annotation or fields[field_name].exclude:
            violations.append(f"{name}: {view.__name__} redeclares header field {field_name!r}")
    return violations


def hand_built_payload_violations(formatter_cls: type[ComponentFormatter[Never]]) -> list[str]:
    """Report a formatter that overrides to_json_serializable instead of dumping its view."""
    if formatter_cls.to_json_serializable is not ComponentFormatter.to_json_serializable:
        return [f"{formatter_cls.__name__} builds its JSON payload by hand in to_json_serializable"]
    return []


def _enum_of(annotation: object) -> type[StrEnum] | None:
    """Return the StrEnum an annotation such as `E` or `E | None` admits, or None."""
    if get_origin(annotation) in (Union, UnionType):
        members = [arg for arg in get_args(annotation) if arg is not NoneType]
        annotation = members[0] if len(members) == 1 else None
    return annotation if isinstance(annotation, type) and issubclass(annotation, StrEnum) else None


def status_field_violations(view: type[BaseModel]) -> list[str]:
    """Report a top-level status field outside STATUS_VALUES, or one admitting other values than its bound set."""
    field = view.model_fields.get("status")
    if field is None:
        return []
    if view not in STATUS_VALUES:
        return [f"{view.__name__} declares a status field; only {sorted(v.__name__ for v in STATUS_VALUES)} may"]
    enum = _enum_of(field.annotation)
    admitted = frozenset(member.value for member in enum) if enum is not None else frozenset()
    if admitted != STATUS_VALUES[view]:
        return [f"{view.__name__}.status admits {sorted(admitted)}, expected {sorted(STATUS_VALUES[view])}"]
    return []


def own_ok_violations(model_cls: type[BaseResult]) -> list[str]:
    """Report a result class, or a base between it and BaseResult, that defines its own ok."""
    return [
        f"{klass.__name__} defines its own ok; BaseResult.ok is the only one"
        for klass in model_cls.__mro__
        if klass is not BaseResult and issubclass(klass, BaseResult) and "ok" in vars(klass)
    ]


COMMAND_FORMATTERS: Final[list[CommandFormatter]] = _command_formatters()
_IDS: Final[list[str]] = [model_cls.__name__ for model_cls, _ in COMMAND_FORMATTERS]


class _PlantedResult(BaseResult):
    """A result that defines its own ok."""

    @property
    def ok(self) -> bool:
        """Return True whatever error_code says."""
        return True


class _HeaderlessView(BaseModel):
    """A view that leaves the header out."""

    name: str


class _StrayStatusView(ResultHeaderView):
    """A header view that declares a status of its own."""

    status: str


class _HandBuiltFormatter(ComponentFormatter[_PlantedResult, ResultHeaderView]):
    """A formatter that builds its payload by hand."""

    def to_json_serializable(self, data: _PlantedResult) -> dict[str, JsonValue]:
        """Return a payload without the header."""
        return {"name": "planted"}

    def to_rich(self, data: _PlantedResult) -> RenderableType:
        """Render nothing."""
        return Text("")


class _HeaderlessFormatter(ComponentFormatter[_PlantedResult, _HeaderlessView]):
    """A formatter whose view lacks the header."""

    def to_rich(self, data: _PlantedResult) -> RenderableType:
        """Render nothing."""
        return Text("")


class ResultHeaderLintTests:
    """Every command formatter dumps a header view, and the status and ok rules hold on every command result."""

    def test_registry_lists_command_formatters(self) -> None:
        """Find at least the worktree, catalog, config, session and status command formatters."""
        assert len(COMMAND_FORMATTERS) >= 25

    @pytest.mark.parametrize(("model_cls", "formatter_cls"), COMMAND_FORMATTERS, ids=_IDS)
    def test_every_command_formatter_declares_a_header_view(
        self, model_cls: type[BaseResult], formatter_cls: type[ComponentFormatter[Never]]
    ) -> None:
        """Require the declared view to subclass ResultHeaderView and start with the six header fields unchanged."""
        assert header_view_violations(formatter_cls) == []

    @pytest.mark.parametrize(("model_cls", "formatter_cls"), COMMAND_FORMATTERS, ids=_IDS)
    def test_no_command_formatter_builds_its_payload_by_hand(
        self, model_cls: type[BaseResult], formatter_cls: type[ComponentFormatter[Never]]
    ) -> None:
        """Refuse a command formatter that overrides to_json_serializable."""
        assert hand_built_payload_violations(formatter_cls) == []

    @pytest.mark.parametrize(("model_cls", "formatter_cls"), COMMAND_FORMATTERS, ids=_IDS)
    def test_status_field_only_on_status_and_prune(
        self, model_cls: type[BaseResult], formatter_cls: type[ComponentFormatter[Never]]
    ) -> None:
        """Allow a top-level status only on the status and prune views, each admitting exactly its bound values."""
        view = _declared_view(formatter_cls)
        assert isinstance(view, type) and issubclass(view, BaseModel)
        assert status_field_violations(view) == []

    @pytest.mark.parametrize(("model_cls", "formatter_cls"), COMMAND_FORMATTERS, ids=_IDS)
    def test_no_command_result_defines_its_own_ok(
        self, model_cls: type[BaseResult], formatter_cls: type[ComponentFormatter[Never]]
    ) -> None:
        """Refuse a dispatched result that overrides BaseResult.ok."""
        assert own_ok_violations(model_cls) == []

    def test_status_views_are_registered(self) -> None:
        """Require each view allowed a status to be the declared view of a command formatter."""
        declared = {_declared_view(formatter_cls) for _, formatter_cls in COMMAND_FORMATTERS}
        assert set(STATUS_VALUES) <= declared

    @pytest.mark.parametrize(
        ("violations", "expected"),
        [
            pytest.param(
                hand_built_payload_violations(_HandBuiltFormatter),
                ["_HandBuiltFormatter builds its JSON payload by hand in to_json_serializable"],
                id="hand_built_payload",
            ),
            pytest.param(
                header_view_violations(_HeaderlessFormatter),
                [f"_HeaderlessFormatter declares view {_HeaderlessView!r}, which does not subclass ResultHeaderView"],
                id="view_without_header",
            ),
            pytest.param(
                status_field_violations(_StrayStatusView),
                ["_StrayStatusView declares a status field; only ['StatusView', 'WorktreePruneView'] may"],
                id="stray_status",
            ),
            pytest.param(
                own_ok_violations(_PlantedResult),
                ["_PlantedResult defines its own ok; BaseResult.ok is the only one"],
                id="own_ok",
            ),
        ],
    )
    def test_lint_rejects_planted_violations(self, violations: list[str], expected: list[str]) -> None:
        """Report each planted violation with a message naming the offender."""
        assert violations == expected
