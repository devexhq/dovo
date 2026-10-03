"""Tier 2 presentation contracts for DovoStatusFormatter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dovo.cli.ui.formatters.status import (
    DovoStatusFormatter,
    StatusHealth,
    StatusView,
)
from dovo.core.config.loader import ConfigLoadStatus
from dovo.core.config.models import AgentConfig, DovoConfig, ProjectConfig
from dovo.core.status.models import (
    CatalogStatusInfo,
    ConfigStatusInfo,
    DatabaseStatusInfo,
    DovoStatusResult,
    GitStatusInfo,
    SandboxStatusInfo,
)
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
    assert_transform_derives_expected_view,
)

ROOT = Path("/workspace/my-repo")
CONFIG_PATH = ROOT / ".dovo" / "config.json"


def _make_status_result(
    *,
    root_dir: Path | None = None,
    is_initialized: bool = True,
    git: GitStatusInfo | None = None,
    config: ConfigStatusInfo | None = None,
    catalog: CatalogStatusInfo | None = None,
    database: DatabaseStatusInfo | None = None,
    sandboxes: SandboxStatusInfo | None = None,
    warnings: list[str] | None = None,
    fixes: list[str] | None = None,
    errors: list[str] | None = None,
) -> DovoStatusResult:
    """Helper to construct a valid DovoStatusResult with test defaults."""
    base = root_dir or Path("/workspace/my-repo")
    return DovoStatusResult(
        root_dir=base,
        is_initialized=is_initialized,
        git=git
        or GitStatusInfo(
            is_git_repo=True,
            branch="feature/status-cmd",
            is_dirty=False,
            uncommitted_files=0,
        ),
        config=config
        or ConfigStatusInfo(
            status=ConfigLoadStatus.OK,
            config_path=base / ".dovo" / "config.json",
            is_valid=True,
            config=DovoConfig(
                version=1,
                project=ProjectConfig(name="worktree-cli"),
                agent=AgentConfig(model="gemini-2.5-flash"),
            ),
        ),
        catalog=catalog
        or CatalogStatusInfo(
            exists=True,
            catalog_dir=base / ".dovo" / "catalog",
            total_items=2,
            workflows_count=1,
            tasks_count=1,
            steps_count=0,
            invalid_items=0,
            item_names=["deploy", "lint"],
        ),
        database=database
        or DatabaseStatusInfo(
            exists=True,
            db_path=base / ".dovo" / "data.db",
            is_accessible=True,
            total_runs=1,
        ),
        sandboxes=sandboxes
        or SandboxStatusInfo(
            active_sandboxes=1,
            total_sandboxes=1,
            max_active_sandboxes=5,
        ),
        warnings=warnings or [],
        fixes=fixes or [],
        errors=errors or [],
    )


def _make_status_view(**overrides: Any) -> StatusView:
    """Helper to construct a StatusView with healthy baseline defaults."""
    defaults: dict[str, Any] = {
        "health": StatusHealth.OK,
        "root_dir": ROOT,
        "project_name": "worktree-cli",
        "config_status": ConfigLoadStatus.OK,
        "config_path_relative": ".dovo/config.json",
        "git_branch": "feature/status-cmd",
        "git_is_dirty": False,
        "uncommitted_files": 0,
        "agent_model": "gemini-2.5-flash",
        "active_sandboxes": 1,
        "max_active_sandboxes": 5,
        "valid_catalog_items": 2,
        "total_catalog_items": 2,
        "total_runs": 1,
        "errors": [],
        "warnings": [],
        "remediations": [],
    }
    defaults.update(overrides)
    return StatusView(**defaults)


HEALTHY = FormatterCase(
    data=_make_status_result(root_dir=ROOT),
    view=_make_status_view(),
    render_expectations=["worktree-cli", "feature/status-cmd", "gemini-2.5-flash", "1", "5", "2", "2"],
)

UNINITIALIZED_NON_GIT = FormatterCase(
    data=_make_status_result(
        root_dir=ROOT,
        is_initialized=False,
        git=GitStatusInfo(is_git_repo=False, branch="none", is_dirty=False, uncommitted_files=0),
        config=ConfigStatusInfo(
            status=ConfigLoadStatus.NOT_FOUND,
            config_path=CONFIG_PATH,
            is_valid=False,
            config=None,
        ),
        warnings=["Dovo workspace is not initialized. Run 'dovo init' to configure."],
        fixes=[
            "Run 'dovo init' to initialize Dovo in this repository.",
            "Run 'git init' or navigate to a Git repository.",
        ],
    ),
    view=_make_status_view(
        health=StatusHealth.UNINITIALIZED,
        project_name=None,
        config_status=ConfigLoadStatus.NOT_FOUND,
        git_branch=None,
        agent_model=None,
        active_sandboxes=None,
        max_active_sandboxes=None,
        valid_catalog_items=None,
        total_catalog_items=None,
        warnings=["Dovo workspace is not initialized. Run 'dovo init' to configure."],
        remediations=[
            "Run 'dovo init' to initialize Dovo in this repository.",
            "Run 'git init' or navigate to a Git repository.",
        ],
    ),
    render_expectations=[
        "Dovo workspace is not initialized. Run 'dovo init' to configure.",
        "Run 'dovo init' to initialize Dovo in this repository.",
        "Run 'git init' or navigate to a Git repository.",
    ],
)

DEGRADED_SCHEMA_INVALID = FormatterCase(
    data=_make_status_result(
        root_dir=ROOT,
        config=ConfigStatusInfo(
            status=ConfigLoadStatus.SCHEMA_INVALID,
            config_path=CONFIG_PATH,
            is_valid=False,
            config=None,
            errors=["Invalid value at 'agent.model': expected string"],
        ),
        warnings=["Invalid value: expected string"],
        fixes=["Run 'dovo config validate' to inspect schema errors or 'dovo init --repair' to insert missing keys."],
    ),
    view=_make_status_view(
        health=StatusHealth.DEGRADED,
        project_name=None,
        config_status=ConfigLoadStatus.SCHEMA_INVALID,
        agent_model=None,
        active_sandboxes=None,
        max_active_sandboxes=None,
        valid_catalog_items=None,
        total_catalog_items=None,
        warnings=["Invalid value: expected string"],
        remediations=[
            "Run 'dovo config validate' to inspect schema errors or 'dovo init --repair' to insert missing keys."
        ],
    ),
    render_expectations=[
        "feature/status-cmd",
        "Invalid value: expected string",
        "Run 'dovo config validate' to inspect schema errors or 'dovo init --repair' to insert missing keys.",
    ],
)

DEGRADED_MALFORMED_JSON = FormatterCase(
    data=_make_status_result(
        root_dir=ROOT,
        config=ConfigStatusInfo(
            status=ConfigLoadStatus.MALFORMED_JSON,
            config_path=CONFIG_PATH,
            is_valid=False,
            config=None,
            errors=["Malformed config.json: Expecting property name"],
        ),
        warnings=["Malformed config.json"],
        fixes=["Repair JSON syntax in .dovo/config.json or restore from backup."],
    ),
    view=_make_status_view(
        health=StatusHealth.DEGRADED,
        project_name=None,
        config_status=ConfigLoadStatus.MALFORMED_JSON,
        agent_model=None,
        active_sandboxes=None,
        max_active_sandboxes=None,
        valid_catalog_items=None,
        total_catalog_items=None,
        warnings=["Malformed config.json"],
        remediations=["Repair JSON syntax in .dovo/config.json or restore from backup."],
    ),
    render_expectations=[
        "feature/status-cmd",
        "Malformed config.json",
        "Repair JSON syntax in .dovo/config.json or restore from backup.",
    ],
)

DEGRADED_ROOT_NOT_OBJECT = FormatterCase(
    data=_make_status_result(
        root_dir=ROOT,
        config=ConfigStatusInfo(
            status=ConfigLoadStatus.ROOT_NOT_OBJECT,
            config_path=CONFIG_PATH,
            is_valid=False,
            config=None,
            errors=["Malformed config.json: root must be an object"],
        ),
        warnings=["Malformed config.json: root must be an object"],
        fixes=["Ensure .dovo/config.json contains a JSON object root."],
    ),
    view=_make_status_view(
        health=StatusHealth.DEGRADED,
        project_name=None,
        config_status=ConfigLoadStatus.ROOT_NOT_OBJECT,
        agent_model=None,
        active_sandboxes=None,
        max_active_sandboxes=None,
        valid_catalog_items=None,
        total_catalog_items=None,
        warnings=["Malformed config.json: root must be an object"],
        remediations=["Ensure .dovo/config.json contains a JSON object root."],
    ),
    render_expectations=[
        "feature/status-cmd",
        "Malformed config.json: root must be an object",
        "Ensure .dovo/config.json contains a JSON object root.",
    ],
)

DEGRADED_PATH_IS_DIRECTORY = FormatterCase(
    data=_make_status_result(
        root_dir=ROOT,
        config=ConfigStatusInfo(
            status=ConfigLoadStatus.PATH_IS_DIRECTORY,
            config_path=CONFIG_PATH,
            is_valid=False,
            config=None,
            errors=["Config path is a directory, not a file"],
        ),
        warnings=["Config path is a directory, not a file"],
        fixes=["Remove directory at .dovo/config.json and run 'dovo init'."],
    ),
    view=_make_status_view(
        health=StatusHealth.DEGRADED,
        project_name=None,
        config_status=ConfigLoadStatus.PATH_IS_DIRECTORY,
        agent_model=None,
        active_sandboxes=None,
        max_active_sandboxes=None,
        valid_catalog_items=None,
        total_catalog_items=None,
        warnings=["Config path is a directory, not a file"],
        remediations=["Remove directory at .dovo/config.json and run 'dovo init'."],
    ),
    render_expectations=[
        "feature/status-cmd",
        "Config path is a directory, not a file",
        "Remove directory at .dovo/config.json and run 'dovo init'.",
    ],
)

DEGRADED_UNREADABLE = FormatterCase(
    data=_make_status_result(
        root_dir=ROOT,
        config=ConfigStatusInfo(
            status=ConfigLoadStatus.UNREADABLE,
            config_path=CONFIG_PATH,
            is_valid=False,
            config=None,
            errors=["Unable to read config.json: Permission denied"],
        ),
        warnings=["Unable to read config.json: Permission denied"],
        fixes=["Check file permissions for .dovo/config.json."],
    ),
    view=_make_status_view(
        health=StatusHealth.DEGRADED,
        project_name=None,
        config_status=ConfigLoadStatus.UNREADABLE,
        agent_model=None,
        active_sandboxes=None,
        max_active_sandboxes=None,
        valid_catalog_items=None,
        total_catalog_items=None,
        warnings=["Unable to read config.json: Permission denied"],
        remediations=["Check file permissions for .dovo/config.json."],
    ),
    render_expectations=[
        "feature/status-cmd",
        "Unable to read config.json: Permission denied",
        "Check file permissions for .dovo/config.json.",
    ],
)

DIRTY_BRANCH = FormatterCase(
    data=_make_status_result(
        root_dir=ROOT,
        git=GitStatusInfo(is_git_repo=True, branch="feature/dirty-branch", is_dirty=True, uncommitted_files=3),
    ),
    view=_make_status_view(
        git_branch="feature/dirty-branch",
        git_is_dirty=True,
        uncommitted_files=3,
    ),
    render_expectations=["worktree-cli", "feature/dirty-branch", "gemini-2.5-flash", "1", "5", "2", "2"],
)

NOT_A_GIT_REPO = FormatterCase(
    data=_make_status_result(
        root_dir=ROOT,
        git=GitStatusInfo(is_git_repo=False, branch="none", is_dirty=False, uncommitted_files=0),
        fixes=["Run 'git init' or navigate to a Git repository."],
    ),
    view=_make_status_view(
        health=StatusHealth.DEGRADED,
        git_branch=None,
        remediations=["Run 'git init' or navigate to a Git repository."],
    ),
    render_expectations=[
        "worktree-cli",
        "gemini-2.5-flash",
        "1",
        "5",
        "2",
        "2",
        "Run 'git init' or navigate to a Git repository.",
    ],
)

UNNAMED_PROJECT = FormatterCase(
    data=_make_status_result(
        root_dir=ROOT,
        config=ConfigStatusInfo(
            status=ConfigLoadStatus.OK,
            config_path=CONFIG_PATH,
            is_valid=True,
            config=DovoConfig(
                version=1,
                project=ProjectConfig(name=""),
                agent=AgentConfig(model="gemini-2.5-flash"),
            ),
        ),
    ),
    view=_make_status_view(
        project_name=None,
    ),
    render_expectations=["feature/status-cmd", "gemini-2.5-flash", "1", "5", "2", "2"],
)

AGENT_MODEL_UNSET = FormatterCase(
    data=_make_status_result(
        root_dir=ROOT,
        config=ConfigStatusInfo(
            status=ConfigLoadStatus.OK,
            config_path=CONFIG_PATH,
            is_valid=True,
            config=DovoConfig(
                version=1,
                project=ProjectConfig(name="worktree-cli"),
                agent=AgentConfig(model=None),
            ),
        ),
    ),
    view=_make_status_view(
        agent_model=None,
    ),
    render_expectations=["worktree-cli", "feature/status-cmd", "1", "5", "2", "2"],
)

EMPTY_CATALOG = FormatterCase(
    data=_make_status_result(
        root_dir=ROOT,
        catalog=CatalogStatusInfo(
            exists=False,
            catalog_dir=ROOT / ".dovo" / "catalog",
            total_items=0,
            workflows_count=0,
            tasks_count=0,
            steps_count=0,
            invalid_items=0,
            item_names=[],
        ),
    ),
    view=_make_status_view(
        valid_catalog_items=0,
        total_catalog_items=0,
    ),
    render_expectations=["worktree-cli", "feature/status-cmd", "gemini-2.5-flash", "1", "5", "0", "0"],
)

INVALID_CATALOG_ITEMS = FormatterCase(
    data=_make_status_result(
        root_dir=ROOT,
        catalog=CatalogStatusInfo(
            exists=True,
            catalog_dir=ROOT / ".dovo" / "catalog",
            total_items=5,
            workflows_count=2,
            tasks_count=2,
            steps_count=1,
            invalid_items=2,
            item_names=["w1", "w2", "t1", "t2", "s1"],
        ),
    ),
    view=_make_status_view(
        health=StatusHealth.DEGRADED,
        valid_catalog_items=3,
        total_catalog_items=5,
    ),
    render_expectations=["worktree-cli", "feature/status-cmd", "gemini-2.5-flash", "1", "5", "3", "5"],
)

WITH_WARNINGS_AND_FIXES = FormatterCase(
    data=_make_status_result(
        root_dir=ROOT,
        warnings=["max_active_sandboxes (10) is unusually high."],
        fixes=["Reduce max_active_sandboxes in .dovo/config.json."],
    ),
    view=_make_status_view(
        warnings=["max_active_sandboxes (10) is unusually high."],
        remediations=["Reduce max_active_sandboxes in .dovo/config.json."],
    ),
    render_expectations=[
        "worktree-cli",
        "feature/status-cmd",
        "gemini-2.5-flash",
        "1",
        "5",
        "2",
        "2",
        "max_active_sandboxes (10) is unusually high.",
        "Reduce max_active_sandboxes in .dovo/config.json.",
    ],
)

DEGRADED_RAW_CONFIG_PROJECT = FormatterCase(
    data=_make_status_result(
        root_dir=ROOT,
        config=ConfigStatusInfo(
            status=ConfigLoadStatus.SCHEMA_INVALID,
            config_path=CONFIG_PATH,
            is_valid=False,
            raw={"project": {"name": "raw-project"}},
            config=None,
            errors=["Invalid schema"],
        ),
        warnings=["Invalid schema"],
        fixes=["Fix schema errors"],
    ),
    view=_make_status_view(
        health=StatusHealth.DEGRADED,
        project_name="raw-project",
        config_status=ConfigLoadStatus.SCHEMA_INVALID,
        agent_model=None,
        active_sandboxes=None,
        max_active_sandboxes=None,
        valid_catalog_items=None,
        total_catalog_items=None,
        warnings=["Invalid schema"],
        remediations=["Fix schema errors"],
    ),
    render_expectations=["raw-project", "feature/status-cmd", "Invalid schema", "Fix schema errors"],
)

STATUS_CASES = [
    pytest.param(HEALTHY, id="healthy_workspace"),
    pytest.param(UNINITIALIZED_NON_GIT, id="uninitialized_non_git"),
    pytest.param(DEGRADED_SCHEMA_INVALID, id="degraded_schema_invalid"),
    pytest.param(DEGRADED_MALFORMED_JSON, id="degraded_malformed_json"),
    pytest.param(DEGRADED_ROOT_NOT_OBJECT, id="degraded_root_not_object"),
    pytest.param(DEGRADED_PATH_IS_DIRECTORY, id="degraded_path_is_directory"),
    pytest.param(DEGRADED_UNREADABLE, id="degraded_unreadable"),
    pytest.param(DIRTY_BRANCH, id="dirty_branch"),
    pytest.param(NOT_A_GIT_REPO, id="not_a_git_repo"),
    pytest.param(UNNAMED_PROJECT, id="unnamed_project"),
    pytest.param(AGENT_MODEL_UNSET, id="agent_model_unset"),
    pytest.param(EMPTY_CATALOG, id="empty_catalog"),
    pytest.param(INVALID_CATALOG_ITEMS, id="invalid_catalog_items"),
    pytest.param(WITH_WARNINGS_AND_FIXES, id="with_warnings_and_fixes"),
    pytest.param(DEGRADED_RAW_CONFIG_PROJECT, id="degraded_raw_config_project"),
]

STATUS_PAYLOAD_CASES = [
    pytest.param(
        HEALTHY,
        {
            "health": "ok",
            "root_dir": "/workspace/my-repo",
            "project_name": "worktree-cli",
            "config_status": "ok",
            "config_path_relative": ".dovo/config.json",
            "git_branch": "feature/status-cmd",
            "git_is_dirty": False,
            "uncommitted_files": 0,
            "agent_model": "gemini-2.5-flash",
            "active_sandboxes": 1,
            "max_active_sandboxes": 5,
            "valid_catalog_items": 2,
            "total_catalog_items": 2,
            "total_runs": 1,
            "errors": [],
            "warnings": [],
            "remediations": [],
        },
        id="healthy_workspace",
    ),
    pytest.param(
        UNINITIALIZED_NON_GIT,
        {
            "health": "uninitialized",
            "root_dir": "/workspace/my-repo",
            "project_name": None,
            "config_status": "not_found",
            "config_path_relative": ".dovo/config.json",
            "git_branch": None,
            "git_is_dirty": False,
            "uncommitted_files": 0,
            "agent_model": None,
            "active_sandboxes": None,
            "max_active_sandboxes": None,
            "valid_catalog_items": None,
            "total_catalog_items": None,
            "total_runs": 1,
            "errors": [],
            "warnings": ["Dovo workspace is not initialized. Run 'dovo init' to configure."],
            "remediations": [
                "Run 'dovo init' to initialize Dovo in this repository.",
                "Run 'git init' or navigate to a Git repository.",
            ],
        },
        id="uninitialized_non_git",
    ),
]


class DovoStatusFormatterTests:
    """Presentation contract tests for DovoStatusFormatter."""

    @pytest.mark.parametrize("case", STATUS_CASES)
    def test_transform_derives_expected_view(self, case: FormatterCase[DovoStatusResult, StatusView]) -> None:
        """Verify transform derives the exact StatusView model representation."""
        assert_transform_derives_expected_view(DovoStatusFormatter, case.data, case.view)

    @pytest.mark.parametrize(("case", "expected_payload"), STATUS_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[DovoStatusResult, StatusView],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the exact published wire-format literal dict."""
        assert_json_payload_matches_published_shape(DovoStatusFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", STATUS_CASES)
    def test_rich_render_shows_every_view_value(self, case: FormatterCase[DovoStatusResult, StatusView]) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(DovoStatusFormatter, case.data, case.render_expectations)
