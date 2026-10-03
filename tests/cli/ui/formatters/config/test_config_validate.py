"""Tier 2 presentation contract tests for ConfigValidateFormatter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dovo.cli.ui.formatters.config import (
    ConfigValidateFormatter,
    ConfigValidationView,
)
from dovo.core.config.models import (
    AgentConfig,
    ConcurrencyConfig,
    DoctorConfig,
    DovoConfig,
    HistoryConfig,
    ProjectConfig,
    PruneConfig,
    TelemetryConfig,
    WorktreeConfig,
)
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


def _make_config(name: str = "valid-proj") -> DovoConfig:
    return DovoConfig(
        version=1,
        project=ProjectConfig(name=name, initialized_at=None),
        worktree=WorktreeConfig(
            base_ref="HEAD",
            max_active_worktrees=3,
            default_timeout_seconds=900,
        ),
        agent=AgentConfig(
            provider="local",
            model=None,
            endpoint=None,
            temperature=0.2,
            max_tokens=4096,
        ),
        history=HistoryConfig(
            save_attempt_logs=True,
            save_agent_payloads=True,
            save_final_diff=True,
            max_sessions=1000,
        ),
        doctor=DoctorConfig(
            check_git=True,
            check_paths_writable=True,
            check_config_schema=True,
            check_stale_worktrees=True,
            check_required_binaries=True,
        ),
        prune=PruneConfig(
            remove_stale_worktrees=True,
            remove_orphaned_worktrees=True,
            remove_expired_artifacts=False,
            artifact_ttl_days=30,
        ),
        telemetry=TelemetryConfig(enabled=False),
        concurrency=ConcurrencyConfig(lock_timeout_seconds=30.0),
    )


VALID_CONFIG = _make_config("valid-proj")

VALID_CASE = FormatterCase(
    data=ConfigValidationResult(
        status=ConfigValidationStatus.VALID,
        config_path=CONFIG_PATH,
        config=VALID_CONFIG,
        warnings=[],
        errors=[],
        fixes=[],
    ),
    view=ConfigValidationView(
        status=ConfigValidationStatus.VALID,
        config_path=CONFIG_PATH,
        status_label="valid",
        raw=None,
        config=VALID_CONFIG,
        errors=[],
        warnings=[],
        fixes=[],
    ),
    render_expectations=[CONFIG_PATH.as_posix(), "valid"],
)

VALID_WITH_WARNINGS_CASE = FormatterCase(
    data=ConfigValidationResult(
        status=ConfigValidationStatus.VALID,
        config_path=CONFIG_PATH,
        config=VALID_CONFIG,
        warnings=["agent.provider is not 'local' but agent.model is missing (CONFIG_WARN_AGENT_MODEL_MISSING)."],
        errors=[],
        fixes=["Set agent.model in .dovo/config.json"],
    ),
    view=ConfigValidationView(
        status=ConfigValidationStatus.VALID,
        config_path=CONFIG_PATH,
        status_label="valid with warnings",
        raw=None,
        config=VALID_CONFIG,
        errors=[],
        warnings=["agent.provider is not 'local' but agent.model is missing (CONFIG_WARN_AGENT_MODEL_MISSING)."],
        fixes=["Set agent.model in .dovo/config.json"],
    ),
    render_expectations=[
        CONFIG_PATH.as_posix(),
        "valid with warnings",
        "agent.provider is not 'local' but agent.model is missing (CONFIG_WARN_AGENT_MODEL_MISSING).",
        "Set agent.model in .dovo/config.json",
    ],
)

INVALID_CASE = FormatterCase(
    data=ConfigValidationResult(
        status=ConfigValidationStatus.INVALID,
        config_path=CONFIG_PATH,
        errors=["semantic failure (CONFIG_ERROR)."],
        warnings=[],
        fixes=["Fix the reported configuration error"],
    ),
    view=ConfigValidationView(
        status=ConfigValidationStatus.INVALID,
        config_path=CONFIG_PATH,
        status_label="invalid",
        raw=None,
        config=None,
        errors=["semantic failure (CONFIG_ERROR)."],
        warnings=[],
        fixes=["Fix the reported configuration error"],
    ),
    render_expectations=[
        "semantic failure (CONFIG_ERROR).",
        "Fix the reported configuration error",
    ],
)

INVALID_WITH_WARNINGS_CASE = FormatterCase(
    data=ConfigValidationResult(
        status=ConfigValidationStatus.INVALID,
        config_path=CONFIG_PATH,
        errors=["semantic failure (CONFIG_ERROR)."],
        warnings=["warning message (CONFIG_WARN)."],
        fixes=[],
    ),
    view=ConfigValidationView(
        status=ConfigValidationStatus.INVALID,
        config_path=CONFIG_PATH,
        status_label="invalid",
        raw=None,
        config=None,
        errors=["semantic failure (CONFIG_ERROR)."],
        warnings=["warning message (CONFIG_WARN)."],
        fixes=[],
    ),
    render_expectations=[
        "semantic failure (CONFIG_ERROR).",
        "warning message (CONFIG_WARN).",
    ],
)

VALIDATION_CASES = [
    pytest.param(VALID_CASE, id="valid_config"),
    pytest.param(VALID_WITH_WARNINGS_CASE, id="valid_with_warnings"),
    pytest.param(INVALID_CASE, id="invalid_config"),
    pytest.param(INVALID_WITH_WARNINGS_CASE, id="invalid_with_warnings"),
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
                "project": {
                    "name": "valid-proj",
                    "initialized_at": None,
                },
                "ignore_global_root_error": False,
                "worktree": {
                    "base_ref": "HEAD",
                    "max_active_worktrees": 3,
                    "default_timeout_seconds": 900,
                },
                "agent": {
                    "provider": "local",
                    "model": None,
                    "endpoint": None,
                    "temperature": 0.2,
                    "max_tokens": 4096,
                },
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
                "telemetry": {
                    "enabled": False,
                },
                "concurrency": {
                    "lock_timeout_seconds": 30.0,
                },
            },
            "errors": [],
            "warnings": [],
            "fixes": [],
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
            "warnings": [],
            "fixes": ["Fix the reported configuration error"],
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
        """Verify transform derives the exact ConfigValidationView model representation."""
        assert_transform_derives_expected_view(ConfigValidateFormatter, case.data, case.view)

    @pytest.mark.parametrize(("case", "expected_payload"), VALIDATION_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[ConfigValidationResult, ConfigValidationView],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the exact published wire-format literal dict."""
        assert_json_payload_matches_published_shape(ConfigValidateFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", VALIDATION_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[ConfigValidationResult, ConfigValidationView]
    ) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(ConfigValidateFormatter, case.data, case.render_expectations)
