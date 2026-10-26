"""Contract tests for the lazily imported formatter groups."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, JsonValue

from dovo.cli.ui.dispatcher import UiDispatcher
from dovo.cli.ui.events import MessageEvent, WarningEvent
from dovo.cli.ui.formatters.events import WarningFormatter
from dovo.cli.ui.formatters.registry import FORMATTER_GROUPS, formatters_for
from dovo.common.models import OutputFormatOptions


class ShoutingWarningFormatter(WarningFormatter):
    """Warning formatter override that upper-cases the message in JSON."""

    def to_json_serializable(self, data: WarningEvent) -> dict[str, JsonValue]:
        return {"message": data.message.upper()}


GROUP_MODELS = [(package, model) for package, group in FORMATTER_GROUPS.items() for model in group(None)]


class FormatterRegistryTests:
    @pytest.mark.parametrize(("package", "model"), GROUP_MODELS, ids=[model.__name__ for _, model in GROUP_MODELS])
    def test_each_model_resolves_to_its_own_group(self, package: str, model: type[BaseModel]) -> None:
        table = formatters_for(model)

        assert model.__module__.startswith(f"{package}.") or model.__module__ == package
        assert sorted(cls.__name__ for cls in table) == sorted(cls.__name__ for cls in FORMATTER_GROUPS[package](None))

    def test_a_type_outside_every_group_has_no_formatters(self) -> None:
        assert formatters_for(int) == {}

    def test_dispatcher_loads_the_group_of_a_model_on_first_dispatch(self, capsys: pytest.CaptureFixture[str]) -> None:
        UiDispatcher().dispatch(WarningEvent(message="careful"), OutputFormatOptions.JSON)

        assert capsys.readouterr().out == '{"event_type": "WarningEvent", "payload": {"message": "careful"}}\n'

    def test_loading_a_group_keeps_an_explicitly_registered_formatter(self, capsys: pytest.CaptureFixture[str]) -> None:
        dispatcher = UiDispatcher()
        dispatcher.register(WarningEvent, ShoutingWarningFormatter())

        dispatcher.dispatch(MessageEvent(message="hi"), OutputFormatOptions.JSON)
        dispatcher.dispatch(WarningEvent(message="careful"), OutputFormatOptions.JSON)

        assert capsys.readouterr().out.splitlines()[-1] == (
            '{"event_type": "WarningEvent", "payload": {"message": "CAREFUL"}}'
        )
