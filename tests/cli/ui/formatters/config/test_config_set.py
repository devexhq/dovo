"""Tier 2 presentation contract tests for ConfigSetFormatter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dovo.cli.ui.formatters.config import ConfigSetFormatter, ConfigSetView
from dovo.core.config.mutate import ConfigSetResult, ConfigSetStatus
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
    assert_transform_derives_expected_view,
)

CONFIG_PATH = Path("/workspace/.dovo/config.json")

SUCCESS_STR_CASE = FormatterCase(
    data=ConfigSetResult(
        status=ConfigSetStatus.OK,
        config_path=CONFIG_PATH,
        key="agent.model",
        value="qwen2.5-coder",
        errors=[],
        warnings=[],
        fixes=[],
    ),
    view=ConfigSetView(
        status=ConfigSetStatus.OK,
        config_path=CONFIG_PATH,
        key="agent.model",
        value="qwen2.5-coder",
        value_str="qwen2.5-coder",
        value_type="str",
        errors=[],
        warnings=[],
        fixes=[],
    ),
    render_expectations=["agent.model", "qwen2.5-coder", "str"],
)

SUCCESS_BOOL_CASE = FormatterCase(
    data=ConfigSetResult(
        status=ConfigSetStatus.OK,
        config_path=CONFIG_PATH,
        key="telemetry.enabled",
        value=True,
        errors=[],
        warnings=[],
        fixes=[],
    ),
    view=ConfigSetView(
        status=ConfigSetStatus.OK,
        config_path=CONFIG_PATH,
        key="telemetry.enabled",
        value=True,
        value_str="true",
        value_type="bool",
        errors=[],
        warnings=[],
        fixes=[],
    ),
    render_expectations=["telemetry.enabled", "true", "bool"],
)

SUCCESS_INT_CASE = FormatterCase(
    data=ConfigSetResult(
        status=ConfigSetStatus.OK,
        config_path=CONFIG_PATH,
        key="sandbox.max_active_sandboxes",
        value=5,
        errors=[],
        warnings=[],
        fixes=[],
    ),
    view=ConfigSetView(
        status=ConfigSetStatus.OK,
        config_path=CONFIG_PATH,
        key="sandbox.max_active_sandboxes",
        value=5,
        value_str="5",
        value_type="int",
        errors=[],
        warnings=[],
        fixes=[],
    ),
    render_expectations=["sandbox.max_active_sandboxes", "5", "int"],
)

SUCCESS_DICT_CASE = FormatterCase(
    data=ConfigSetResult(
        status=ConfigSetStatus.OK,
        config_path=CONFIG_PATH,
        key="project",
        value={"name": "my-app"},
        errors=[],
        warnings=[],
        fixes=[],
    ),
    view=ConfigSetView(
        status=ConfigSetStatus.OK,
        config_path=CONFIG_PATH,
        key="project",
        value={"name": "my-app"},
        value_str='{"name": "my-app"}',
        value_type="dict",
        errors=[],
        warnings=[],
        fixes=[],
    ),
    render_expectations=["project", '{"name": "my-app"}', "dict"],
)

ERROR_SCHEMA_INVALID_CASE = FormatterCase(
    data=ConfigSetResult(
        status=ConfigSetStatus.SCHEMA_INVALID,
        config_path=CONFIG_PATH,
        key="agent.invalid_key",
        value=None,
        errors=["Config schema validation failed (CONFIG_SCHEMA_INVALID): extra property not allowed"],
        warnings=[],
        fixes=["Run `dovo config validate` for details"],
    ),
    view=ConfigSetView(
        status=ConfigSetStatus.SCHEMA_INVALID,
        config_path=CONFIG_PATH,
        key="agent.invalid_key",
        value=None,
        value_str="None",
        value_type="NoneType",
        errors=["Config schema validation failed (CONFIG_SCHEMA_INVALID): extra property not allowed"],
        warnings=[],
        fixes=["Run `dovo config validate` for details"],
    ),
    render_expectations=[
        "Config schema validation failed (CONFIG_SCHEMA_INVALID): extra property not allowed",
        "Run `dovo config validate` for details",
    ],
)

SET_CASES = [
    pytest.param(SUCCESS_STR_CASE, id="success_str"),
    pytest.param(SUCCESS_BOOL_CASE, id="success_bool"),
    pytest.param(SUCCESS_INT_CASE, id="success_int"),
    pytest.param(SUCCESS_DICT_CASE, id="success_dict"),
    pytest.param(ERROR_SCHEMA_INVALID_CASE, id="error_schema_invalid"),
]

SET_PAYLOAD_CASES = [
    pytest.param(
        SUCCESS_STR_CASE,
        {
            "status": "ok",
            "config_path": "/workspace/.dovo/config.json",
            "key": "agent.model",
            "value": "qwen2.5-coder",
            "value_str": "qwen2.5-coder",
            "value_type": "str",
            "errors": [],
            "warnings": [],
            "fixes": [],
        },
        id="success_str_payload",
    ),
    pytest.param(
        SUCCESS_BOOL_CASE,
        {
            "status": "ok",
            "config_path": "/workspace/.dovo/config.json",
            "key": "telemetry.enabled",
            "value": True,
            "value_str": "true",
            "value_type": "bool",
            "errors": [],
            "warnings": [],
            "fixes": [],
        },
        id="success_bool_payload",
    ),
    pytest.param(
        ERROR_SCHEMA_INVALID_CASE,
        {
            "status": "schema_invalid",
            "config_path": "/workspace/.dovo/config.json",
            "key": "agent.invalid_key",
            "value": None,
            "value_str": "None",
            "value_type": "NoneType",
            "errors": ["Config schema validation failed (CONFIG_SCHEMA_INVALID): extra property not allowed"],
            "warnings": [],
            "fixes": ["Run `dovo config validate` for details"],
        },
        id="error_schema_invalid_payload",
    ),
]


class ConfigSetFormatterTests:
    """Presentation contract tests for ConfigSetFormatter."""

    @pytest.mark.parametrize("case", SET_CASES)
    def test_transform_derives_expected_view(self, case: FormatterCase[ConfigSetResult, ConfigSetView]) -> None:
        """Verify transform derives the exact ConfigSetView model representation."""
        assert_transform_derives_expected_view(ConfigSetFormatter, case.data, case.view)

    @pytest.mark.parametrize(("case", "expected_payload"), SET_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[ConfigSetResult, ConfigSetView],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the exact published wire-format literal dict."""
        assert_json_payload_matches_published_shape(ConfigSetFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", SET_CASES)
    def test_rich_render_shows_every_view_value(self, case: FormatterCase[ConfigSetResult, ConfigSetView]) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(ConfigSetFormatter, case.data, case.render_expectations)
