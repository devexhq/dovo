"""Tier 2 presentation contract tests for ConfigSetFormatter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dovo.cli.ui.formatters.config import ConfigSetFormatter, ConfigSetView
from dovo.common.models import Remediation
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
        key="agents.copilot.model",
        value="qwen2.5-coder",
        errors=[],
        warnings=[],
        remediations=[],
    ),
    view=ConfigSetView(
        status=ConfigSetStatus.OK,
        config_path=CONFIG_PATH,
        key="agents.copilot.model",
        value="qwen2.5-coder",
        value_str="qwen2.5-coder",
        value_type="str",
        errors=[],
        warnings=[],
        remediations=[],
    ),
    render_expectations=["agents.copilot.model", "qwen2.5-coder", "str"],
)

SUCCESS_BOOL_CASE = FormatterCase(
    data=ConfigSetResult(
        status=ConfigSetStatus.OK,
        config_path=CONFIG_PATH,
        key="telemetry.enabled",
        value=True,
        errors=[],
        warnings=[],
        remediations=[],
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
        remediations=[],
    ),
    render_expectations=["telemetry.enabled", "true", "bool"],
)

SUCCESS_INT_CASE = FormatterCase(
    data=ConfigSetResult(
        status=ConfigSetStatus.OK,
        config_path=CONFIG_PATH,
        key="worktree.max_active_worktrees",
        value=5,
        errors=[],
        warnings=[],
        remediations=[],
    ),
    view=ConfigSetView(
        status=ConfigSetStatus.OK,
        config_path=CONFIG_PATH,
        key="worktree.max_active_worktrees",
        value=5,
        value_str="5",
        value_type="int",
        errors=[],
        warnings=[],
        remediations=[],
    ),
    render_expectations=["worktree.max_active_worktrees", "5", "int"],
)

SUCCESS_DICT_CASE = FormatterCase(
    data=ConfigSetResult(
        status=ConfigSetStatus.OK,
        config_path=CONFIG_PATH,
        key="project",
        value={"name": "my-app"},
        errors=[],
        warnings=[],
        remediations=[],
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
        remediations=[],
    ),
    render_expectations=["project", '{"name": "my-app"}', "dict"],
)

ERROR_SCHEMA_INVALID_CASE = FormatterCase(
    data=ConfigSetResult(
        status=ConfigSetStatus.SCHEMA_INVALID,
        config_path=CONFIG_PATH,
        key="agent.invalid_key",
        value=None,
        errors=["Config schema validation failed: extra property not allowed"],
        warnings=[],
        remediations=[Remediation(message="Run 'dovo config validate' for details", command="dovo config validate")],
    ),
    view=ConfigSetView(
        status=ConfigSetStatus.SCHEMA_INVALID,
        config_path=CONFIG_PATH,
        key="agent.invalid_key",
        value=None,
        value_str="None",
        value_type="NoneType",
        errors=["Config schema validation failed: extra property not allowed"],
        warnings=[],
        remediations=[Remediation(message="Run 'dovo config validate' for details", command="dovo config validate")],
    ),
    render_expectations=[
        "Config schema validation failed: extra property not allowed",
        "Fix:",
        "- Run 'dovo config validate' for details",
    ],
)

TYPE_COLLISION_ERROR = "Cannot set 'project.name.first'. 'project.name' is already defined as a scalar value."

ERROR_TYPE_COLLISION_CASE = FormatterCase(
    data=ConfigSetResult(
        status=ConfigSetStatus.TYPE_COLLISION,
        config_path=CONFIG_PATH,
        key="project.name.first",
        value="demo",
        error_code="CONFIG_TYPE_CONFLICT",
        errors=[TYPE_COLLISION_ERROR],
    ),
    view=ConfigSetView(
        status=ConfigSetStatus.TYPE_COLLISION,
        config_path=CONFIG_PATH,
        key="project.name.first",
        value="demo",
        value_str="demo",
        value_type="str",
        error_code="CONFIG_TYPE_CONFLICT",
        errors=[TYPE_COLLISION_ERROR],
    ),
    render_expectations=[TYPE_COLLISION_ERROR],
)

SET_CASES = [
    pytest.param(SUCCESS_STR_CASE, id="success_str"),
    pytest.param(SUCCESS_BOOL_CASE, id="success_bool"),
    pytest.param(SUCCESS_INT_CASE, id="success_int"),
    pytest.param(SUCCESS_DICT_CASE, id="success_dict"),
    pytest.param(ERROR_SCHEMA_INVALID_CASE, id="error_schema_invalid"),
    pytest.param(ERROR_TYPE_COLLISION_CASE, id="error_type_collision"),
]

SET_PAYLOAD_CASES = [
    pytest.param(
        SUCCESS_STR_CASE,
        {
            "status": "ok",
            "config_path": "/workspace/.dovo/config.json",
            "key": "agents.copilot.model",
            "value": "qwen2.5-coder",
            "value_str": "qwen2.5-coder",
            "value_type": "str",
            "error_code": None,
            "errors": [],
            "warnings": [],
            "remediations": [],
            "diagnostics": [],
        },
        id="success_str_payload",
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
            "error_code": None,
            "errors": ["Config schema validation failed: extra property not allowed"],
            "warnings": [],
            "remediations": [{"message": "Run 'dovo config validate' for details", "command": "dovo config validate"}],
            "diagnostics": [],
        },
        id="error_schema_invalid_payload",
    ),
    pytest.param(
        ERROR_TYPE_COLLISION_CASE,
        {
            "status": "type_collision",
            "config_path": "/workspace/.dovo/config.json",
            "key": "project.name.first",
            "value": "demo",
            "value_str": "demo",
            "value_type": "str",
            "error_code": "CONFIG_TYPE_CONFLICT",
            "errors": [TYPE_COLLISION_ERROR],
            "warnings": [],
            "remediations": [],
            "diagnostics": [],
        },
        id="error_type_collision_payload",
    ),
]


class ConfigSetFormatterTests:
    """Presentation contract tests for ConfigSetFormatter."""

    @pytest.mark.parametrize("case", SET_CASES)
    def test_transform_derives_expected_view(self, case: FormatterCase[ConfigSetResult, ConfigSetView]) -> None:
        assert_transform_derives_expected_view(ConfigSetFormatter, case.data, case.view)

    @pytest.mark.parametrize(("case", "expected_payload"), SET_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[ConfigSetResult, ConfigSetView],
        expected_payload: dict[str, Any],
    ) -> None:
        assert_json_payload_matches_published_shape(ConfigSetFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", SET_CASES)
    def test_rich_render_shows_every_view_value(self, case: FormatterCase[ConfigSetResult, ConfigSetView]) -> None:
        assert_rich_render_shows_every_view_value(ConfigSetFormatter, case.data, case.render_expectations)
