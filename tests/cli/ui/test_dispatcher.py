"""Tier 2 tests for the JSON line UiDispatcher writes for each event."""

from __future__ import annotations

import json

import pytest

from dovo.cli.ui.dispatcher import UiDispatcher
from dovo.cli.ui.events import ErrorEvent, MessageEvent
from dovo.common.error_codes import ErrorCode
from dovo.common.models import OutputFormatOptions


class UiDispatcherJsonTests:
    def test_json_event_type_uses_declared_name(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Write an ErrorEvent under its declared name `error`, and a model without one under its class name."""
        dispatcher = UiDispatcher(output_format=OutputFormatOptions.JSON)

        dispatcher.dispatch(ErrorEvent(title="Fatal Error", error_code=ErrorCode.INTERNAL_ERROR, errors=["boom"]))
        dispatcher.dispatch(MessageEvent(message="hello"))

        lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert [line["event_type"] for line in lines] == ["error", "MessageEvent"]
        assert lines[0]["payload"]["error_code"] == "INTERNAL_ERROR"
