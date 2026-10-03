"""Shared fixtures for CLI command integration tests across every command domain."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.dispatcher import ui_dispatcher


@pytest.fixture(autouse=True)
def _restore_output_format(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restore ui_dispatcher's process-global output format after the test, undoing any set_output_format call."""
    monkeypatch.setattr(ui_dispatcher, "_output_format", ui_dispatcher.output_format)


@pytest.fixture
def dispatch_spy(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Capture every DTO a command handler passes to ui_dispatcher.dispatch during a CLI invocation.

    Calls through to the real dispatcher, so exit code and rendered stdout still behave normally.
    """
    captured: list[Any] = []
    original_dispatch = ui_dispatcher.dispatch

    def _spy_dispatch(data: Any, **kwargs: Any) -> None:
        captured.append(data)
        original_dispatch(data, **kwargs)

    monkeypatch.setattr(ui_dispatcher, "dispatch", _spy_dispatch)
    return captured
