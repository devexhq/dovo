"""Tier 2 presentation contract tests for ConfigUnsetFormatter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dovo.cli.ui.formatters.config.config_unset import ConfigUnsetFormatter
from dovo.cli.ui.formatters.config.config_views import ConfigUnsetView
from dovo.common.error_codes import ErrorCode
from dovo.common.models import Remediation
from dovo.core.config.mutate import ConfigUnsetResult
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

_CONFIG_PATH = Path("/workspace/.dovo/config.json")

OK_EXISTED = FormatterCase(
    data=ConfigUnsetResult(
        config_path=_CONFIG_PATH,
        key="agents.copilot.model",
        existed=True,
        previous_value="qwen2.5-coder",
    ),
    view=ConfigUnsetView(
        ok=True,
        config_path=_CONFIG_PATH,
        key="agents.copilot.model",
        existed=True,
        previous_value="qwen2.5-coder",
    ),
    render_expectations=["agents.copilot.model"],
)

SCHEMA_INVALID = FormatterCase(
    data=ConfigUnsetResult(
        error_code=ErrorCode.CONFIG_SCHEMA_INVALID,
        config_path=_CONFIG_PATH,
        key="project",
        existed=True,
        previous_value={"name": "demo-workspace", "initialized_at": None},
        errors=["Config schema validation failed:\n- (root): 'project' is a required property"],
        remediations=[
            Remediation(message="Run 'dovo config validate' for details", command="dovo config validate"),
            Remediation(
                message="Run 'dovo init --repair' to insert missing keys without overwriting values",
                command="dovo init --repair",
            ),
        ],
    ),
    view=ConfigUnsetView(
        ok=False,
        error_code=ErrorCode.CONFIG_SCHEMA_INVALID,
        errors=["Config schema validation failed:\n- (root): 'project' is a required property"],
        remediations=[
            Remediation(message="Run 'dovo config validate' for details", command="dovo config validate"),
            Remediation(
                message="Run 'dovo init --repair' to insert missing keys without overwriting values",
                command="dovo init --repair",
            ),
        ],
        config_path=_CONFIG_PATH,
        key="project",
        existed=True,
        previous_value={"name": "demo-workspace", "initialized_at": None},
    ),
    render_expectations=["'project' is a required property"],
)

CONFIG_UNSET_CASES = [
    pytest.param(OK_EXISTED, id="ok"),
    pytest.param(SCHEMA_INVALID, id="error"),
]

CONFIG_UNSET_PAYLOAD_CASES = [
    pytest.param(
        OK_EXISTED,
        {
            "ok": True,
            "error_code": None,
            "errors": [],
            "warnings": [],
            "remediations": [],
            "diagnostics": [],
            "config_path": "/workspace/.dovo/config.json",
            "key": "agents.copilot.model",
            "existed": True,
            "previous_value": "qwen2.5-coder",
        },
        id="ok_existed",
    ),
    pytest.param(
        SCHEMA_INVALID,
        {
            "ok": False,
            "error_code": "CONFIG_SCHEMA_INVALID",
            "errors": ["Config schema validation failed:\n- (root): 'project' is a required property"],
            "warnings": [],
            "remediations": [
                {"message": "Run 'dovo config validate' for details", "command": "dovo config validate"},
                {
                    "message": "Run 'dovo init --repair' to insert missing keys without overwriting values",
                    "command": "dovo init --repair",
                },
            ],
            "diagnostics": [],
            "config_path": "/workspace/.dovo/config.json",
            "key": "project",
            "existed": True,
            "previous_value": {"name": "demo-workspace", "initialized_at": None},
        },
        id="schema_invalid",
    ),
]


class ConfigUnsetFormatterTests:
    """Tier 2 presentation contract tests for ConfigUnsetFormatter."""

    @pytest.mark.parametrize(("case", "expected_payload"), CONFIG_UNSET_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[ConfigUnsetResult, ConfigUnsetView],
        expected_payload: dict[str, Any],
    ) -> None:
        """Match the exact published wire-format literal dict; the view adds the header and no derived fields."""
        assert_json_payload_matches_published_shape(ConfigUnsetFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", CONFIG_UNSET_CASES)
    def test_rich_render_shows_every_view_value(self, case: FormatterCase[ConfigUnsetResult, ConfigUnsetView]) -> None:
        """Show the key or error message from the view in the rendered output."""
        assert_rich_render_shows_every_view_value(ConfigUnsetFormatter, case.data, case.render_expectations)
