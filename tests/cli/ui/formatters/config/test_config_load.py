"""Tier 2 presentation contract tests for ConfigLoadFormatter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dovo.cli.ui.formatters.config.config_load import ConfigLoadFormatter
from dovo.core.config.loader import ConfigLoadResult, ConfigLoadStatus
from dovo.core.config.models import DovoConfig, ProjectConfig
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

_CONFIG_PATH = Path("/workspace/.dovo/config.json")

VALID_CONFIG = FormatterCase(
    data=ConfigLoadResult(
        status=ConfigLoadStatus.OK,
        config_path=_CONFIG_PATH,
        config=DovoConfig(version=1, project=ProjectConfig(name="test-project")),
    ),
    view=ConfigLoadResult(
        status=ConfigLoadStatus.OK,
        config_path=_CONFIG_PATH,
        config=DovoConfig(version=1, project=ProjectConfig(name="test-project")),
    ),
    render_expectations=["test-project", _CONFIG_PATH.as_posix()],
)

NOT_FOUND = FormatterCase(
    data=ConfigLoadResult(
        status=ConfigLoadStatus.NOT_FOUND,
        config_path=_CONFIG_PATH,
        errors=["Configuration file not found at '/workspace/.dovo/config.json' (CONFIG_NOT_FOUND)."],
        fixes=["Run `dovo init` to initialize Dovo"],
    ),
    view=ConfigLoadResult(
        status=ConfigLoadStatus.NOT_FOUND,
        config_path=_CONFIG_PATH,
        errors=["Configuration file not found at '/workspace/.dovo/config.json' (CONFIG_NOT_FOUND)."],
        fixes=["Run `dovo init` to initialize Dovo"],
    ),
    render_expectations=[
        "Configuration file not found at '/workspace/.dovo/config.json' (CONFIG_NOT_FOUND).",
        "Run `dovo init` to initialize Dovo",
    ],
)

CONFIG_LOAD_CASES = [
    pytest.param(VALID_CONFIG, id="valid_config"),
    pytest.param(NOT_FOUND, id="not_found"),
]

CONFIG_LOAD_PAYLOAD_CASES = [
    pytest.param(
        VALID_CONFIG,
        {
            "status": "ok",
            "config_path": "/workspace/.dovo/config.json",
            "raw": None,
            "config": {
                "version": 1,
                "project": {
                    "name": "test-project",
                    "initialized_at": None,
                },
                "ignore_global_root_error": False,
                "sandbox": {
                    "base_ref": "HEAD",
                    "max_active_sandboxes": 3,
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
                    "remove_orphaned_sandboxes": True,
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
            "warnings": [],
            "errors": [],
            "fixes": [],
            "error_code": None,
        },
        id="valid_config",
    ),
    pytest.param(
        NOT_FOUND,
        {
            "status": "not_found",
            "config_path": "/workspace/.dovo/config.json",
            "raw": None,
            "config": None,
            "warnings": [],
            "errors": ["Configuration file not found at '/workspace/.dovo/config.json' (CONFIG_NOT_FOUND)."],
            "fixes": ["Run `dovo init` to initialize Dovo"],
            "error_code": None,
        },
        id="not_found",
    ),
]


class ConfigLoadFormatterTests:
    """Tier 2 presentation contract tests for ConfigLoadFormatter."""

    @pytest.mark.parametrize(("case", "expected_payload"), CONFIG_LOAD_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[ConfigLoadResult, ConfigLoadResult],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the exact published wire-format literal dict."""
        assert_json_payload_matches_published_shape(ConfigLoadFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", CONFIG_LOAD_CASES)
    def test_rich_render_shows_every_view_value(self, case: FormatterCase[ConfigLoadResult, ConfigLoadResult]) -> None:
        """Verify non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(ConfigLoadFormatter, case.data, case.render_expectations)
