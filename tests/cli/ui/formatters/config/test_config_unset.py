"""Tier 2 presentation contract tests for ConfigUnsetFormatter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dovo.cli.ui.formatters.config.config_unset import ConfigUnsetFormatter
from dovo.core.config.mutate import ConfigUnsetResult, ConfigUnsetStatus
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

_CONFIG_PATH = Path("/workspace/.dovo/config.json")

OK_EXISTED = FormatterCase(
    data=ConfigUnsetResult(
        status=ConfigUnsetStatus.OK,
        config_path=_CONFIG_PATH,
        key="agent.model",
        existed=True,
        previous_value="qwen2.5-coder",
    ),
    view=ConfigUnsetResult(
        status=ConfigUnsetStatus.OK,
        config_path=_CONFIG_PATH,
        key="agent.model",
        existed=True,
        previous_value="qwen2.5-coder",
    ),
    render_expectations=["agent.model"],
)

OK_NOT_EXISTED = FormatterCase(
    data=ConfigUnsetResult(
        status=ConfigUnsetStatus.OK,
        config_path=_CONFIG_PATH,
        key="telemetry.nonexistent",
        existed=False,
        previous_value=None,
    ),
    view=ConfigUnsetResult(
        status=ConfigUnsetStatus.OK,
        config_path=_CONFIG_PATH,
        key="telemetry.nonexistent",
        existed=False,
        previous_value=None,
    ),
    render_expectations=["telemetry.nonexistent"],
)

SCHEMA_INVALID = FormatterCase(
    data=ConfigUnsetResult(
        status=ConfigUnsetStatus.SCHEMA_INVALID,
        config_path=_CONFIG_PATH,
        key="project",
        existed=True,
        previous_value={"name": "demo-workspace", "initialized_at": None},
        errors=["Config schema validation failed (CONFIG_SCHEMA_INVALID):\n- (root): 'project' is a required property"],
        fixes=[
            "Run `dovo config validate` for details",
            "Or `dovo init --repair` to insert missing keys without overwriting values",
        ],
    ),
    view=ConfigUnsetResult(
        status=ConfigUnsetStatus.SCHEMA_INVALID,
        config_path=_CONFIG_PATH,
        key="project",
        existed=True,
        previous_value={"name": "demo-workspace", "initialized_at": None},
        errors=["Config schema validation failed (CONFIG_SCHEMA_INVALID):\n- (root): 'project' is a required property"],
        fixes=[
            "Run `dovo config validate` for details",
            "Or `dovo init --repair` to insert missing keys without overwriting values",
        ],
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
            "status": "ok",
            "config_path": "/workspace/.dovo/config.json",
            "key": "agent.model",
            "existed": True,
            "previous_value": "qwen2.5-coder",
            "warnings": [],
            "errors": [],
            "error_code": None,
            "fixes": [],
        },
        id="ok_existed",
    ),
    pytest.param(
        OK_NOT_EXISTED,
        {
            "status": "ok",
            "config_path": "/workspace/.dovo/config.json",
            "key": "telemetry.nonexistent",
            "existed": False,
            "previous_value": None,
            "warnings": [],
            "errors": [],
            "error_code": None,
            "fixes": [],
        },
        id="ok_not_existed",
    ),
    pytest.param(
        SCHEMA_INVALID,
        {
            "status": "schema_invalid",
            "config_path": "/workspace/.dovo/config.json",
            "key": "project",
            "existed": True,
            "previous_value": {"name": "demo-workspace", "initialized_at": None},
            "warnings": [],
            "errors": [
                "Config schema validation failed (CONFIG_SCHEMA_INVALID):\n- (root): 'project' is a required property"
            ],
            "error_code": None,
            "fixes": [
                "Run `dovo config validate` for details",
                "Or `dovo init --repair` to insert missing keys without overwriting values",
            ],
        },
        id="schema_invalid",
    ),
]


class ConfigUnsetFormatterTests:
    """Tier 2 presentation contract tests for ConfigUnsetFormatter."""

    @pytest.mark.parametrize(("case", "expected_payload"), CONFIG_UNSET_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[ConfigUnsetResult, ConfigUnsetResult],
        expected_payload: dict[str, Any],
    ) -> None:
        """[tier-2/presentation] ConfigUnsetFormatter.to_json_serializable: matches the exact published wire-format literal dict, identity view (no derived fields)."""
        assert_json_payload_matches_published_shape(ConfigUnsetFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", CONFIG_UNSET_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[ConfigUnsetResult, ConfigUnsetResult]
    ) -> None:
        """[tier-2/presentation] ConfigUnsetFormatter.to_rich: semantic view values (key or error message) reach the rendered output."""
        assert_rich_render_shows_every_view_value(ConfigUnsetFormatter, case.data, case.render_expectations)
