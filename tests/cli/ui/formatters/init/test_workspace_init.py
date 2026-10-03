"""Tier 2 presentation contract tests for WorkspaceInitFormatter."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from dovo.cli.ui.formatters.init import (
    WorkspaceInitFormatter,
    WorkspaceInitView,
)
from dovo.common.constants import DOVO_GITIGNORE_TRACKED_ENTRIES
from dovo.core.bootstrap.models import (
    BootstrapOutcome,
    BootstrapResult,
    InitFailureMode,
    WorkspaceInitResult,
)
from dovo.core.catalog.models import SeedResult
from dovo.core.config.generator import ConfigGenerationResult
from dovo.core.project.models import ProjectIdentity, ProjectIdentityProvisionResult, ProjectIdentityProvisionStatus
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
    assert_transform_derives_expected_view,
)

ROOT = Path("/workspace/my-repo")
DOVO = ROOT / ".dovo"
CONFIG_PATH = DOVO / "config.json"

BASELINE_IDENTITY_RESULT = ProjectIdentityProvisionResult(
    status=ProjectIdentityProvisionStatus.CREATED,
    path=DOVO / "project.json",
    identity=ProjectIdentity(id="test-project", created_at=datetime(2026, 1, 1, tzinfo=UTC)),
)


def make_init_view(**overrides: Any) -> WorkspaceInitView:
    """Helper to construct a WorkspaceInitView with baseline initialized defaults."""
    defaults: dict[str, Any] = {
        "ok": True,
        "root_path": DOVO,
        "root_path_relative": ".dovo",
        "bootstrap_outcome": BootstrapOutcome.INITIALIZED,
        "dirs_created": [".dovo/sessions"],
        "project_id": None,
        "identity_path_relative": None,
        "identity_preserved": False,
        "gitignore_path_relative": ".dovo/.gitignore",
        "gitignore_tracked_entries": list(DOVO_GITIGNORE_TRACKED_ENTRIES),
        "config_created": True,
        "config_overwritten": False,
        "config_repaired": False,
        "config_skipped_existing": False,
        "config_path_relative": ".dovo/config.json",
        "inserted_keys": [],
        "seeded_files": [".dovo/workflows/test.yml"],
        "skipped_seed_files": [],
        "overwritten_seed_files": [],
        "failure_mode": None,
        "errors": [],
        "warnings": [],
        "fixes": [],
    }
    defaults.update(overrides)
    return WorkspaceInitView(**defaults)


INITIALIZED = FormatterCase(
    data=WorkspaceInitResult(
        bootstrap_result=BootstrapResult(
            root_path=DOVO,
            root_created=True,
            dirs_created=[DOVO / "sessions"],
        ),
        identity_result=BASELINE_IDENTITY_RESULT,
        config_result=ConfigGenerationResult(
            config_path=CONFIG_PATH,
            created=True,
        ),
        seed_result=SeedResult(
            created_files=[DOVO / "workflows" / "test.yml"],
        ),
    ),
    view=make_init_view(
        project_id="test-project",
        identity_path_relative=".dovo/project.json",
    ),
    render_expectations=[
        ".dovo",
        ".dovo/config.json",
        ".dovo/sessions",
        ".dovo/workflows/test.yml",
    ],
)

INITIALIZED_WITH_IDENTITY = FormatterCase(
    data=WorkspaceInitResult(
        bootstrap_result=BootstrapResult(
            root_path=DOVO,
            root_created=True,
            dirs_created=[DOVO / ".meta"],
            gitignore_created=True,
        ),
        identity_result=ProjectIdentityProvisionResult(
            status=ProjectIdentityProvisionStatus.CREATED,
            path=DOVO / "project.json",
            identity=ProjectIdentity(id="brave-otter", created_at=datetime(2026, 1, 1, tzinfo=UTC)),
        ),
        config_result=ConfigGenerationResult(
            config_path=CONFIG_PATH,
            created=True,
        ),
        seed_result=SeedResult(
            created_files=[DOVO / "workflows" / "test.yml"],
        ),
    ),
    view=make_init_view(
        dirs_created=[".dovo/.meta"],
        project_id="brave-otter",
        identity_path_relative=".dovo/project.json",
        identity_preserved=False,
    ),
    render_expectations=[
        ".dovo",
        ".dovo/config.json",
        ".dovo/.meta",
        ".dovo/workflows/test.yml",
        "brave-otter",
        ".dovo/project.json",
        "catalog/",
    ],
)

REPAIRED = FormatterCase(
    data=WorkspaceInitResult(
        bootstrap_result=BootstrapResult(
            root_path=DOVO,
            repaired=True,
            dirs_created=[DOVO / "sessions"],
        ),
        identity_result=BASELINE_IDENTITY_RESULT,
        config_result=ConfigGenerationResult(
            config_path=CONFIG_PATH,
            repaired=True,
            inserted_keys=["telemetry.enabled"],
        ),
        seed_result=SeedResult(
            skipped_existing_files=[DOVO / "workflows" / "fix-tests.yml"],
        ),
    ),
    view=make_init_view(
        bootstrap_outcome=BootstrapOutcome.REPAIRED,
        project_id="test-project",
        identity_path_relative=".dovo/project.json",
        config_created=False,
        config_repaired=True,
        inserted_keys=["telemetry.enabled"],
        seeded_files=[],
        skipped_seed_files=[".dovo/workflows/fix-tests.yml"],
    ),
    render_expectations=[
        ".dovo",
        ".dovo/config.json",
        ".dovo/sessions",
        "telemetry.enabled",
        ".dovo/workflows/fix-tests.yml",
    ],
)

ALREADY_INITIALIZED_OVERWRITTEN = FormatterCase(
    data=WorkspaceInitResult(
        bootstrap_result=BootstrapResult(
            root_path=DOVO,
            outcome=BootstrapOutcome.ALREADY_INITIALIZED,
            dirs_created=[],
        ),
        identity_result=BASELINE_IDENTITY_RESULT,
        config_result=ConfigGenerationResult(
            config_path=CONFIG_PATH,
            overwritten=True,
        ),
        seed_result=SeedResult(
            overwritten_files=[DOVO / "workflows" / "x.yml"],
        ),
    ),
    view=make_init_view(
        bootstrap_outcome=BootstrapOutcome.ALREADY_INITIALIZED,
        dirs_created=[],
        project_id="test-project",
        identity_path_relative=".dovo/project.json",
        config_created=False,
        config_overwritten=True,
        seeded_files=[],
        overwritten_seed_files=[".dovo/workflows/x.yml"],
    ),
    render_expectations=[".dovo", ".dovo/config.json"],
)

CONFIG_SKIPPED_EXISTING = FormatterCase(
    data=WorkspaceInitResult(
        bootstrap_result=BootstrapResult(
            root_path=DOVO,
            outcome=BootstrapOutcome.INITIALIZED,
            dirs_created=[],
        ),
        identity_result=BASELINE_IDENTITY_RESULT,
        config_result=ConfigGenerationResult(
            config_path=CONFIG_PATH,
            skipped_existing=True,
        ),
        seed_result=SeedResult(),
    ),
    view=make_init_view(
        dirs_created=[],
        project_id="test-project",
        identity_path_relative=".dovo/project.json",
        config_created=False,
        config_skipped_existing=True,
        seeded_files=[],
    ),
    render_expectations=[".dovo", ".dovo/config.json"],
)

NO_CONFIG_PATH = FormatterCase(
    data=WorkspaceInitResult(
        bootstrap_result=BootstrapResult(
            root_path=DOVO,
            outcome=BootstrapOutcome.INITIALIZED,
            dirs_created=[],
        ),
        identity_result=BASELINE_IDENTITY_RESULT,
        config_result=ConfigGenerationResult(config_path=None),
        seed_result=SeedResult(),
    ),
    view=make_init_view(
        dirs_created=[],
        project_id="test-project",
        identity_path_relative=".dovo/project.json",
        config_created=False,
        config_path_relative=None,
        seeded_files=[],
    ),
    render_expectations=[".dovo"],
)

SEEDING_ERROR = FormatterCase(
    data=WorkspaceInitResult(
        bootstrap_result=BootstrapResult(root_path=DOVO),
        config_result=ConfigGenerationResult(
            config_path=CONFIG_PATH,
            created=True,
        ),
        seed_result=SeedResult(errors=["could not seed"]),
    ),
    view=make_init_view(
        ok=False,
        bootstrap_outcome=BootstrapOutcome.ALREADY_INITIALIZED,
        dirs_created=[],
        seeded_files=[],
        errors=["could not seed"],
    ),
    render_expectations=[".dovo", ".dovo/config.json", "could not seed"],
)

PREFLIGHT_FAILURE = FormatterCase(
    data=WorkspaceInitResult(
        errors=["The current directory is not a valid Git repository."],
        fixes=["Run 'git init' before running 'dovo init'."],
        failure_mode=InitFailureMode.PREFLIGHT,
    ),
    view=make_init_view(
        ok=False,
        root_path=None,
        root_path_relative=None,
        bootstrap_outcome=None,
        dirs_created=[],
        gitignore_path_relative=None,
        gitignore_tracked_entries=[],
        config_created=False,
        config_path_relative=None,
        seeded_files=[],
        failure_mode=InitFailureMode.PREFLIGHT,
        errors=["The current directory is not a valid Git repository."],
        fixes=["Run 'git init' before running 'dovo init'."],
    ),
    render_expectations=[
        "The current directory is not a valid Git repository.",
        "Run 'git init' before running 'dovo init'.",
    ],
)

BOOTSTRAP_FAILURE = FormatterCase(
    data=WorkspaceInitResult(
        bootstrap_result=BootstrapResult(
            root_path=DOVO,
            errors=["path conflict: .dovo is a file"],
            fixes=["Remove the conflicting file."],
        ),
        errors=["path conflict: .dovo is a file"],
        fixes=["Remove the conflicting file."],
        failure_mode=InitFailureMode.BOOTSTRAP,
    ),
    view=make_init_view(
        ok=False,
        bootstrap_outcome=BootstrapOutcome.FAILED,
        dirs_created=[],
        config_created=False,
        config_path_relative=None,
        seeded_files=[],
        failure_mode=InitFailureMode.BOOTSTRAP,
        errors=["path conflict: .dovo is a file"],
        fixes=["Remove the conflicting file."],
    ),
    render_expectations=[
        "path conflict: .dovo is a file",
        "Remove the conflicting file.",
    ],
)

CONFIG_GENERATION_FAILURE = FormatterCase(
    data=WorkspaceInitResult(
        bootstrap_result=BootstrapResult(root_path=DOVO),
        config_result=ConfigGenerationResult(
            config_path=CONFIG_PATH,
            errors=["CONFIG_WRITE_FAILED: permission denied"],
            fixes=["Check file permissions for .dovo/config.json."],
        ),
        errors=["CONFIG_WRITE_FAILED: permission denied"],
        fixes=["Check file permissions for .dovo/config.json."],
        failure_mode=InitFailureMode.CONFIG_GENERATION,
    ),
    view=make_init_view(
        ok=False,
        bootstrap_outcome=BootstrapOutcome.ALREADY_INITIALIZED,
        dirs_created=[],
        config_created=False,
        config_path_relative=".dovo/config.json",
        seeded_files=[],
        failure_mode=InitFailureMode.CONFIG_GENERATION,
        errors=["CONFIG_WRITE_FAILED: permission denied"],
        fixes=["Check file permissions for .dovo/config.json."],
    ),
    render_expectations=[
        "CONFIG_WRITE_FAILED: permission denied",
        "Check file permissions for .dovo/config.json.",
    ],
)

INIT_CASES = [
    pytest.param(INITIALIZED, id="initialized"),
    pytest.param(INITIALIZED_WITH_IDENTITY, id="initialized_with_identity"),
    pytest.param(REPAIRED, id="repaired"),
    pytest.param(ALREADY_INITIALIZED_OVERWRITTEN, id="already_initialized_overwritten"),
    pytest.param(CONFIG_SKIPPED_EXISTING, id="config_skipped_existing"),
    pytest.param(NO_CONFIG_PATH, id="no_config_path"),
    pytest.param(SEEDING_ERROR, id="seeding_error"),
    pytest.param(PREFLIGHT_FAILURE, id="preflight_failure"),
    pytest.param(BOOTSTRAP_FAILURE, id="bootstrap_failure"),
    pytest.param(CONFIG_GENERATION_FAILURE, id="config_generation_failure"),
]

INIT_PAYLOAD_CASES = [
    pytest.param(
        INITIALIZED,
        {
            "ok": True,
            "root_path": "/workspace/my-repo/.dovo",
            "root_path_relative": ".dovo",
            "bootstrap_outcome": "initialized",
            "dirs_created": [".dovo/sessions"],
            "project_id": "test-project",
            "identity_path_relative": ".dovo/project.json",
            "identity_preserved": False,
            "gitignore_path_relative": ".dovo/.gitignore",
            "gitignore_tracked_entries": list(DOVO_GITIGNORE_TRACKED_ENTRIES),
            "config_created": True,
            "config_overwritten": False,
            "config_repaired": False,
            "config_skipped_existing": False,
            "config_path_relative": ".dovo/config.json",
            "inserted_keys": [],
            "seeded_files": [".dovo/workflows/test.yml"],
            "skipped_seed_files": [],
            "overwritten_seed_files": [],
            "failure_mode": None,
            "errors": [],
            "warnings": [],
            "fixes": [],
        },
        id="initialized_payload",
    ),
    pytest.param(
        INITIALIZED_WITH_IDENTITY,
        {
            "ok": True,
            "root_path": "/workspace/my-repo/.dovo",
            "root_path_relative": ".dovo",
            "bootstrap_outcome": "initialized",
            "dirs_created": [".dovo/.meta"],
            "project_id": "brave-otter",
            "identity_path_relative": ".dovo/project.json",
            "identity_preserved": False,
            "gitignore_path_relative": ".dovo/.gitignore",
            "gitignore_tracked_entries": list(DOVO_GITIGNORE_TRACKED_ENTRIES),
            "config_created": True,
            "config_overwritten": False,
            "config_repaired": False,
            "config_skipped_existing": False,
            "config_path_relative": ".dovo/config.json",
            "inserted_keys": [],
            "seeded_files": [".dovo/workflows/test.yml"],
            "skipped_seed_files": [],
            "overwritten_seed_files": [],
            "failure_mode": None,
            "errors": [],
            "warnings": [],
            "fixes": [],
        },
        id="initialized_with_identity_payload",
    ),
    pytest.param(
        REPAIRED,
        {
            "ok": True,
            "root_path": "/workspace/my-repo/.dovo",
            "root_path_relative": ".dovo",
            "bootstrap_outcome": "repaired",
            "dirs_created": [".dovo/sessions"],
            "project_id": "test-project",
            "identity_path_relative": ".dovo/project.json",
            "identity_preserved": False,
            "gitignore_path_relative": ".dovo/.gitignore",
            "gitignore_tracked_entries": list(DOVO_GITIGNORE_TRACKED_ENTRIES),
            "config_created": False,
            "config_overwritten": False,
            "config_repaired": True,
            "config_skipped_existing": False,
            "config_path_relative": ".dovo/config.json",
            "inserted_keys": ["telemetry.enabled"],
            "seeded_files": [],
            "skipped_seed_files": [".dovo/workflows/fix-tests.yml"],
            "overwritten_seed_files": [],
            "failure_mode": None,
            "errors": [],
            "warnings": [],
            "fixes": [],
        },
        id="repaired_payload",
    ),
    pytest.param(
        PREFLIGHT_FAILURE,
        {
            "ok": False,
            "root_path": None,
            "root_path_relative": None,
            "bootstrap_outcome": None,
            "dirs_created": [],
            "project_id": None,
            "identity_path_relative": None,
            "identity_preserved": False,
            "gitignore_path_relative": None,
            "gitignore_tracked_entries": [],
            "config_created": False,
            "config_overwritten": False,
            "config_repaired": False,
            "config_skipped_existing": False,
            "config_path_relative": None,
            "inserted_keys": [],
            "seeded_files": [],
            "skipped_seed_files": [],
            "overwritten_seed_files": [],
            "failure_mode": "preflight",
            "errors": ["The current directory is not a valid Git repository."],
            "warnings": [],
            "fixes": ["Run 'git init' before running 'dovo init'."],
        },
        id="preflight_failure_payload",
    ),
]


class WorkspaceInitFormatterTests:
    """Tier 2 presentation contract tests for WorkspaceInitFormatter."""

    @pytest.mark.parametrize("case", INIT_CASES)
    def test_transform_derives_expected_view(self, case: FormatterCase[WorkspaceInitResult, WorkspaceInitView]) -> None:
        """Verify transform derives the exact WorkspaceInitView model representation."""
        assert_transform_derives_expected_view(WorkspaceInitFormatter, case.data, case.view)

    @pytest.mark.parametrize(("case", "expected_payload"), INIT_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[WorkspaceInitResult, WorkspaceInitView],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the exact published wire-format literal dict."""
        assert_json_payload_matches_published_shape(WorkspaceInitFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", INIT_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[WorkspaceInitResult, WorkspaceInitView]
    ) -> None:
        """Verify that non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(WorkspaceInitFormatter, case.data, case.render_expectations)
