"""Tier 2 presentation contract tests for ConfigValidateFormatter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dovo.cli.ui.formatters.config import (
    ConfigValidateFormatter,
    ConfigValidationView,
)
from dovo.common.models import Remediation
from dovo.core.config.models import DovoConfig, ProjectConfig
from dovo.core.config.validate import (
    ConfigValidationResult,
    ConfigValidationStatus,
)
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
    assert_transform_derives_expected_view,
)

CONFIG_PATH = Path("/workspace/.dovo/config.json")


VALID_CONFIG = DovoConfig(version=1, project=ProjectConfig(name="valid-proj", initialized_at=None))

VALID_CASE = FormatterCase(
    data=ConfigValidationResult(
        status=ConfigValidationStatus.VALID,
        config_path=CONFIG_PATH,
        config=VALID_CONFIG,
        warnings=[],
        errors=[],
        remediations=[],
    ),
    view=ConfigValidationView(
        status=ConfigValidationStatus.VALID,
        config_path=CONFIG_PATH,
        status_label="valid",
        raw=None,
        config=VALID_CONFIG,
        errors=[],
        warnings=[],
        remediations=[],
    ),
    render_expectations=[CONFIG_PATH.as_posix(), "valid"],
)

VALID_WITH_WARNINGS_CASE = FormatterCase(
    data=ConfigValidationResult(
        status=ConfigValidationStatus.VALID,
        config_path=CONFIG_PATH,
        config=VALID_CONFIG,
        warnings=[
            "default_agent 'copilot' requires agents.copilot.model but it is missing (CONFIG_WARN_AGENT_MODEL_MISSING)."
        ],
        errors=[],
        remediations=[Remediation(message="Set agents.copilot.model in .dovo/config.json")],
    ),
    view=ConfigValidationView(
        status=ConfigValidationStatus.VALID,
        config_path=CONFIG_PATH,
        status_label="valid with warnings",
        raw=None,
        config=VALID_CONFIG,
        errors=[],
        warnings=[
            "default_agent 'copilot' requires agents.copilot.model but it is missing (CONFIG_WARN_AGENT_MODEL_MISSING)."
        ],
        remediations=[Remediation(message="Set agents.copilot.model in .dovo/config.json")],
    ),
    render_expectations=[
        CONFIG_PATH.as_posix(),
        "valid with warnings",
        "default_agent 'copilot' requires agents.copilot.model but it is missing (CONFIG_WARN_AGENT_MODEL_MISSING).",
        "Set agents.copilot.model in .dovo/config.json",
    ],
)

INVALID_CASE = FormatterCase(
    data=ConfigValidationResult(
        status=ConfigValidationStatus.INVALID,
        config_path=CONFIG_PATH,
        errors=["semantic failure (CONFIG_ERROR)."],
        warnings=["warning message (CONFIG_WARN)."],
        remediations=[Remediation(message="Fix the reported configuration error")],
    ),
    view=ConfigValidationView(
        status=ConfigValidationStatus.INVALID,
        config_path=CONFIG_PATH,
        status_label="invalid",
        raw=None,
        config=None,
        errors=["semantic failure (CONFIG_ERROR)."],
        warnings=["warning message (CONFIG_WARN)."],
        remediations=[Remediation(message="Fix the reported configuration error")],
    ),
    render_expectations=[
        "semantic failure (CONFIG_ERROR).",
        "Fix the reported configuration error",
        "warning message (CONFIG_WARN).",
    ],
)

INVALID_WITHOUT_WARNINGS_CASE = FormatterCase(
    data=INVALID_CASE.data.model_copy(update={"warnings": []}),
    view=INVALID_CASE.view.model_copy(update={"warnings": []}),
    render_expectations=["semantic failure (CONFIG_ERROR).", "Fix the reported configuration error"],
)

VALIDATION_CASES = [
    pytest.param(VALID_CASE, id="valid_config"),
    pytest.param(VALID_WITH_WARNINGS_CASE, id="valid_with_warnings"),
    pytest.param(INVALID_CASE, id="invalid_with_warnings"),
    pytest.param(INVALID_WITHOUT_WARNINGS_CASE, id="invalid_without_warnings"),
]

VALIDATION_PAYLOAD_CASES = [
    pytest.param(
        VALID_CASE,
        {
            "status": "valid",
            "config_path": "/workspace/.dovo/config.json",
            "status_label": "valid",
            "raw": None,
            "config": {
                "version": 1,
                "project": {"name": "valid-proj", "initialized_at": None},
                "ignore_global_root_error": False,
                "worktree": {"base_ref": "HEAD", "max_active_worktrees": 3, "default_timeout_seconds": 900},
                "default_agent": "copilot",
                "agents": {},
                "history": {
                    "save_attempt_logs": True,
                    "save_agent_payloads": True,
                    "save_final_diff": True,
                    "max_sessions": 1000,
                },
                "doctor": {
                    "check_git": True,
                    "check_paths_writable": True,
                    "check_config_schema": True,
                    "check_stale_worktrees": True,
                    "check_required_binaries": True,
                },
                "prune": {
                    "remove_stale_worktrees": True,
                    "remove_orphaned_worktrees": True,
                    "remove_expired_artifacts": False,
                    "artifact_ttl_days": 30,
                },
                "telemetry": {"enabled": False},
                "concurrency": {"lock_timeout_seconds": 30.0},
                "environment": {"sensitive_variables": []},
            },
            "errors": [],
            "warnings": [],
            "remediations": [],
        },
        id="valid_payload",
    ),
    pytest.param(
        INVALID_CASE,
        {
            "status": "invalid",
            "config_path": "/workspace/.dovo/config.json",
            "status_label": "invalid",
            "raw": None,
            "config": None,
            "errors": ["semantic failure (CONFIG_ERROR)."],
            "warnings": ["warning message (CONFIG_WARN)."],
            "remediations": [{"message": "Fix the reported configuration error", "command": None}],
        },
        id="invalid_payload",
    ),
]


class ConfigValidateFormatterTests:
    """Presentation contract tests for ConfigValidateFormatter."""

    @pytest.mark.parametrize("case", VALIDATION_CASES)
    def test_transform_derives_expected_view(
        self, case: FormatterCase[ConfigValidationResult, ConfigValidationView]
    ) -> None:
        assert_transform_derives_expected_view(ConfigValidateFormatter, case.data, case.view)

    @pytest.mark.parametrize(("case", "expected_payload"), VALIDATION_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[ConfigValidationResult, ConfigValidationView],
        expected_payload: dict[str, Any],
    ) -> None:
        assert_json_payload_matches_published_shape(ConfigValidateFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", VALIDATION_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[ConfigValidationResult, ConfigValidationView]
    ) -> None:
        assert_rich_render_shows_every_view_value(ConfigValidateFormatter, case.data, case.render_expectations)
