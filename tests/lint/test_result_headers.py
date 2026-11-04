"""Invariant: every command result's JSON payload starts with the shared header, built by one path, with a status only where allowed."""

from __future__ import annotations

import typing
from enum import StrEnum
from typing import Final

import pytest
from pydantic import BaseModel

from dovo.cli.ui.formatters.base import ComponentFormatter, ResultView
from dovo.cli.ui.formatters.registry import FORMATTER_GROUPS
from dovo.common.models import RESULT_HEADER_FIELDS, BaseResult
from dovo.core.status import WorkspaceHealth
from dovo.core.worktree import WorktreePruneStatus

STATUS_VALUES: Final[dict[str, type[StrEnum]]] = {
    "DovoStatusResult": WorkspaceHealth,
    "WorktreePruneResult": WorktreePruneStatus,
}


def _command_formatters() -> list[tuple[type[BaseResult], ComponentFormatter[object]]]:
    """Return every registered formatter of a command result with its result class."""
    return [
        (model, formatter)
        for group in FORMATTER_GROUPS.values()
        for model, formatter in group(None).items()
        if issubclass(model, BaseResult)
    ]


def _payload_class(result_cls: type[BaseResult], formatter: ComponentFormatter[object]) -> type[BaseModel]:
    """Return the class whose model_dump is the formatter's JSON payload: its view, or the result itself."""
    args = typing.get_args(type(formatter).__orig_bases__[0])  # ty: ignore[unresolved-attribute] # generic bases are set by the subscript
    return result_cls if len(args) < 2 or args[1] is BaseModel else args[1]


COMMAND_FORMATTERS: Final = _command_formatters()
IDS: Final = [model.__name__ for model, _ in COMMAND_FORMATTERS]


class ResultHeaderTests:
    @pytest.mark.parametrize(("result_cls", "formatter"), COMMAND_FORMATTERS, ids=IDS)
    def test_payload_starts_with_the_header(
        self, result_cls: type[BaseResult], formatter: ComponentFormatter[object]
    ) -> None:
        payload_cls = _payload_class(result_cls, formatter)

        assert issubclass(payload_cls, ResultView | BaseResult), f"{payload_cls.__name__} does not carry the header"
        if issubclass(payload_cls, ResultView):
            assert tuple(payload_cls.model_fields)[: len(RESULT_HEADER_FIELDS)] == RESULT_HEADER_FIELDS

    @pytest.mark.parametrize(("result_cls", "formatter"), COMMAND_FORMATTERS, ids=IDS)
    def test_payload_is_not_built_by_hand(
        self, result_cls: type[BaseResult], formatter: ComponentFormatter[object]
    ) -> None:
        assert type(formatter).to_json_serializable is ComponentFormatter.to_json_serializable

    @pytest.mark.parametrize(("result_cls", "formatter"), COMMAND_FORMATTERS, ids=IDS)
    def test_status_exists_only_where_allowed(
        self, result_cls: type[BaseResult], formatter: ComponentFormatter[object]
    ) -> None:
        payload_cls = _payload_class(result_cls, formatter)

        for cls in {result_cls, payload_cls}:
            field = cls.model_fields.get("status")
            if cls is result_cls or issubclass(cls, ResultView):
                allowed = STATUS_VALUES.get(result_cls.__name__)
                assert (field is None) == (allowed is None), f"{cls.__name__}.status is not allowed"
                assert field is None or field.annotation is allowed, f"{cls.__name__}.status has the wrong values"

    def test_status_values_are_the_binding_ones(self) -> None:
        assert {member.value for member in WorkspaceHealth} == {"ok", "degraded", "uninitialized"}
        assert {member.value for member in WorktreePruneStatus} == {"pruned", "nothing_to_prune", "partial_failure"}
