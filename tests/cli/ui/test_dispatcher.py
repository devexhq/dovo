"""Tier 2 tests for the JSON line UiDispatcher writes for each event."""

from __future__ import annotations

import json

import pytest

from dovo.cli.ui.dispatcher import UiDispatcher
from dovo.cli.ui.events import ErrorEvent, MessageEvent
from dovo.common.error_codes import ErrorCode
from dovo.common.models import OutputFormatOptions
from dovo.core.catalog.models import CatalogItemType, CatalogListResult


class UiDispatcherJsonTests:
    def test_json_line_is_a_versioned_envelope_under_the_declared_name(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Write exactly `version`, `event_type` and `payload`, in that order, with version 1 and the declared name."""
        dispatcher = UiDispatcher(output_format=OutputFormatOptions.JSON)

        dispatcher.dispatch(MessageEvent(message="hello"))

        assert capsys.readouterr().out == (
            '{"version": 1, "event_type": "message", "payload": {"message": "hello", "style": null}}\n'
        )

    def test_error_event_is_written_as_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Write an ErrorEvent under the declared name `error`."""
        dispatcher = UiDispatcher(output_format=OutputFormatOptions.JSON)

        dispatcher.dispatch(ErrorEvent(title="Fatal Error", error_code=ErrorCode.INTERNAL_ERROR, errors=["boom"]))

        line = json.loads(capsys.readouterr().out)
        assert (line["version"], line["event_type"]) == (1, "error")
        assert line["payload"]["error_code"] == "INTERNAL_ERROR"

    @pytest.mark.parametrize(
        ("item_type", "event_type"),
        [(CatalogItemType.BLUEPRINT, "blueprint.list"), (CatalogItemType.STEP, "step.list")],
    )
    def test_catalog_result_is_named_by_its_item_type(
        self, capsys: pytest.CaptureFixture[str], item_type: CatalogItemType, event_type: str
    ) -> None:
        """Write one catalog result class as `blueprint.*` or `step.*` according to its item_type."""
        dispatcher = UiDispatcher(output_format=OutputFormatOptions.JSON)

        dispatcher.dispatch(CatalogListResult(item_type=item_type))

        assert json.loads(capsys.readouterr().out)["event_type"] == event_type
