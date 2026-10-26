"""Contract tests for the lazily imported formatter groups."""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from dovo.cli.ui.dispatcher import UiDispatcher
from dovo.cli.ui.events import WarningEvent
from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.common import DispatcherProtocol
from dovo.cli.ui.formatters.registry import FORMATTER_GROUPS, register_formatters_for
from dovo.common.models import OutputFormatOptions


class RecordingDispatcher(DispatcherProtocol):
    """Dispatcher double that records each registered model type."""

    def __init__(self) -> None:
        self.registered: list[type[BaseModel]] = []

    def register(self, model_cls: type[BaseModel], formatter: ComponentFormatter[object]) -> object:
        self.registered.append(model_cls)
        return formatter


GROUP_MODELS = [(package, model) for package, group in FORMATTER_GROUPS.items() for model in group(None)]


class FormatterRegistryTests:
    @pytest.mark.parametrize(("package", "model"), GROUP_MODELS, ids=[model.__name__ for _, model in GROUP_MODELS])
    def test_each_model_registers_its_own_group(self, package: str, model: type[BaseModel]) -> None:
        dispatcher = RecordingDispatcher()

        register_formatters_for(dispatcher, model)

        assert model.__module__.startswith(f"{package}.") or model.__module__ == package
        assert model in dispatcher.registered
        assert sorted(cls.__name__ for cls in dispatcher.registered) == sorted(
            cls.__name__ for cls in FORMATTER_GROUPS[package](None)
        )

    def test_a_type_outside_every_group_registers_nothing(self) -> None:
        dispatcher = RecordingDispatcher()

        register_formatters_for(dispatcher, int)

        assert dispatcher.registered == []

    def test_dispatcher_loads_the_group_of_a_model_on_first_dispatch(self, capsys: pytest.CaptureFixture[str]) -> None:
        UiDispatcher().dispatch(WarningEvent(message="careful"), OutputFormatOptions.JSON)

        assert json.loads(capsys.readouterr().out)["event_type"] == "WarningEvent"
