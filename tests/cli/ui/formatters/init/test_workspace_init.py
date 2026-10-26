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
from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord
from dovo.common.error_codes import ErrorCode
from dovo.common.models import Remediation
from dovo.core.bootstrap.models import (
    BootstrapOutcome,
    BootstrapResult,
    InitFailureMode,
    WorkspaceInitResult,
)
from dovo.core.catalog.models import SeedResult
from dovo.core.config.generator import ConfigGenerationResult
from dovo.core.project.models import ProjectIdentity, ProjectIdentityOutcome
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
    assert_transform_derives_expected_view,
)

ROOT = Path("/workspace/my-repo")
DOVO = ROOT / ".dovo"
CONFIG_PATH = DOVO / "config.json"

BASELINE_IDENTITY = ProjectIdentity(id="test-project", created_at=datetime(2026, 1, 1, tzinfo=UTC))
PRESERVE_WARNING = (
    "Project identity already exists (id=test-project); preserving existing identity. "
    "Rerun with --id <new-id> --force to replace it."
)


def make_init_view(**overrides: object) -> WorkspaceInitView:
    """Construct a WorkspaceInitView with baseline initialized defaults."""
    defaults: dict[str, object] = {
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
        "seeded_files": [".dovo/catalog/blueprints/test.yml"],
        "skipped_seed_files": [],
        "overwritten_seed_files": [],
        "failure_mode": None,
        "errors": [],
        "warnings": [],
        "remediations": [],
        "diagnostics": [],
    }
    return WorkspaceInitView.model_validate({**defaults, **overrides})


INITIALIZED = FormatterCase(
    data=WorkspaceInitResult(
        bootstrap_result=BootstrapResult(
            root_path=DOVO,
            root_created=True,
            dirs_created=[DOVO / "sessions"],
        ),
        project_identity=BASELINE_IDENTITY,
        identity_path=DOVO / "project.json",
        identity_outcome=ProjectIdentityOutcome.CREATED,
        config_result=ConfigGenerationResult(
            config_path=CONFIG_PATH,
            created=True,
        ),
        seed_result=SeedResult(
            created_files=[DOVO / "catalog" / "blueprints" / "test.yml"],
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
        ".dovo/catalog/blueprints/test.yml",
        "test-project",
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
        project_identity=BASELINE_IDENTITY,
        identity_path=DOVO / "project.json",
        identity_outcome=ProjectIdentityOutcome.CREATED,
        config_result=ConfigGenerationResult(
            config_path=CONFIG_PATH,
            repaired=True,
            inserted_keys=["telemetry.enabled"],
        ),
        seed_result=SeedResult(
            skipped_existing_files=[DOVO / "catalog" / "blueprints" / "fix-tests.yml"],
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
        skipped_seed_files=[".dovo/catalog/blueprints/fix-tests.yml"],
    ),
    render_expectations=[
        ".dovo",
        ".dovo/config.json",
        ".dovo/sessions",
        "telemetry.enabled",
        ".dovo/catalog/blueprints/fix-tests.yml",
    ],
)

ALREADY_INITIALIZED_OVERWRITTEN = FormatterCase(
    data=WorkspaceInitResult(
        bootstrap_result=BootstrapResult(
            root_path=DOVO,
            outcome=BootstrapOutcome.ALREADY_INITIALIZED,
            dirs_created=[],
        ),
        project_identity=BASELINE_IDENTITY,
        identity_path=DOVO / "project.json",
        identity_outcome=ProjectIdentityOutcome.CREATED,
        config_result=ConfigGenerationResult(
            config_path=CONFIG_PATH,
            overwritten=True,
        ),
        seed_result=SeedResult(
            overwritten_files=[DOVO / "catalog" / "blueprints" / "x.yml"],
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
        overwritten_seed_files=[".dovo/catalog/blueprints/x.yml"],
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
        project_identity=BASELINE_IDENTITY,
        identity_path=DOVO / "project.json",
        identity_outcome=ProjectIdentityOutcome.CREATED,
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
        project_identity=BASELINE_IDENTITY,
        identity_path=DOVO / "project.json",
        identity_outcome=ProjectIdentityOutcome.CREATED,
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

IDENTITY_PRESERVED = FormatterCase(
    data=WorkspaceInitResult(
        bootstrap_result=BootstrapResult(
            root_path=DOVO,
            outcome=BootstrapOutcome.ALREADY_INITIALIZED,
            dirs_created=[],
        ),
        project_identity=BASELINE_IDENTITY,
        identity_path=DOVO / "project.json",
        identity_outcome=ProjectIdentityOutcome.PRESERVED,
        config_result=ConfigGenerationResult(
            config_path=CONFIG_PATH,
            overwritten=True,
        ),
        seed_result=SeedResult(),
        warnings=[PRESERVE_WARNING, "repair ignored when overwrite=True"],
    ),
    view=make_init_view(
        bootstrap_outcome=BootstrapOutcome.ALREADY_INITIALIZED,
        dirs_created=[],
        project_id="test-project",
        identity_path_relative=".dovo/project.json",
        identity_preserved=True,
        config_created=False,
        config_overwritten=True,
        seeded_files=[],
        warnings=[PRESERVE_WARNING, "repair ignored when overwrite=True"],
    ),
    render_expectations=[".dovo", "test-project", ".dovo/project.json", PRESERVE_WARNING],
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
        remediations=[Remediation(message="Run 'git init' before running 'dovo init'.", command="git init")],
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
        remediations=[Remediation(message="Run 'git init' before running 'dovo init'.", command="git init")],
    ),
    render_expectations=[
        "The current directory is not a valid Git repository.",
        "Fix:",
        "- Run 'git init' before running 'dovo init'.",
    ],
)

BOOTSTRAP_FAILURE = FormatterCase(
    data=WorkspaceInitResult(
        bootstrap_result=BootstrapResult(
            root_path=DOVO,
            errors=["path conflict: .dovo is a file"],
        ),
        errors=["path conflict: .dovo is a file"],
        remediations=[Remediation(message="Resolve the path conflict above, then rerun 'dovo init'")],
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
        remediations=[Remediation(message="Resolve the path conflict above, then rerun 'dovo init'")],
    ),
    render_expectations=[
        "path conflict: .dovo is a file",
        "Fix:",
        "- Resolve the path conflict above, then rerun 'dovo init'",
    ],
)

IDENTITY_FAILURE_ERROR = (
    "Unable to read project identity at '/workspace/my-repo/.dovo/project.json': denied (PROJECT_IDENTITY_UNREADABLE)."
)
IDENTITY_FAILURE_RECORD = DiagnosticRecord(
    event_id="exc_3f9a1c07b2de",
    log_path=Path("/home/you/.dovo/logs/2026-10-19/exc_3f9a1c07b2de.json"),
    level=DiagnosticLevel.ERROR,
)

IDENTITY_FAILURE = FormatterCase(
    data=WorkspaceInitResult(
        bootstrap_result=BootstrapResult(root_path=DOVO),
        identity_path=DOVO / "project.json",
        error_code=ErrorCode.PROJECT_IDENTITY_UNREADABLE,
        errors=[IDENTITY_FAILURE_ERROR],
        remediations=[Remediation(message="Resolve the path conflict above, then rerun 'dovo init'")],
        failure_mode=InitFailureMode.BOOTSTRAP,
        diagnostics=[IDENTITY_FAILURE_RECORD],
    ),
    view=make_init_view(
        ok=False,
        bootstrap_outcome=BootstrapOutcome.ALREADY_INITIALIZED,
        dirs_created=[],
        identity_path_relative=".dovo/project.json",
        config_created=False,
        config_path_relative=None,
        seeded_files=[],
        failure_mode=InitFailureMode.BOOTSTRAP,
        errors=[IDENTITY_FAILURE_ERROR],
        remediations=[Remediation(message="Resolve the path conflict above, then rerun 'dovo init'")],
        diagnostics=[IDENTITY_FAILURE_RECORD],
    ),
    render_expectations=[
        "PROJECT_IDENTITY_UNREADABLE",
        "exc_3f9a1c07b2de",
        "Fix:",
        "- Resolve the path conflict above, then rerun 'dovo init'",
    ],
)

CONFIG_GENERATION_FAILURE = FormatterCase(
    data=WorkspaceInitResult(
        bootstrap_result=BootstrapResult(root_path=DOVO),
        config_result=ConfigGenerationResult(
            config_path=CONFIG_PATH,
            errors=["CONFIG_WRITE_FAILED: permission denied"],
            remediations=[Remediation(message="Check file permissions for '.dovo/config.json'.")],
        ),
        errors=["CONFIG_WRITE_FAILED: permission denied"],
        remediations=[Remediation(message="Check file permissions for '.dovo/config.json'.")],
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
        remediations=[Remediation(message="Check file permissions for '.dovo/config.json'.")],
    ),
    render_expectations=[
        "CONFIG_WRITE_FAILED: permission denied",
        "Fix:",
        "- Check file permissions for '.dovo/config.json'.",
    ],
)

INIT_CASES = [
    pytest.param(INITIALIZED, id="initialized"),
    pytest.param(REPAIRED, id="repaired"),
    pytest.param(ALREADY_INITIALIZED_OVERWRITTEN, id="already_initialized_overwritten"),
    pytest.param(CONFIG_SKIPPED_EXISTING, id="config_skipped_existing"),
    pytest.param(NO_CONFIG_PATH, id="no_config_path"),
    pytest.param(IDENTITY_PRESERVED, id="identity_preserved"),
    pytest.param(SEEDING_ERROR, id="seeding_error"),
    pytest.param(PREFLIGHT_FAILURE, id="preflight_failure"),
    pytest.param(BOOTSTRAP_FAILURE, id="bootstrap_failure"),
    pytest.param(CONFIG_GENERATION_FAILURE, id="config_generation_failure"),
    pytest.param(IDENTITY_FAILURE, id="identity_failure"),
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
            "seeded_files": [".dovo/catalog/blueprints/test.yml"],
            "skipped_seed_files": [],
            "overwritten_seed_files": [],
            "failure_mode": None,
            "errors": [],
            "warnings": [],
            "remediations": [],
            "diagnostics": [],
        },
        id="initialized_payload",
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
            "remediations": [{"message": "Run 'git init' before running 'dovo init'.", "command": "git init"}],
            "diagnostics": [],
        },
        id="preflight_failure_payload",
    ),
    pytest.param(
        IDENTITY_FAILURE,
        {
            "ok": False,
            "root_path": "/workspace/my-repo/.dovo",
            "root_path_relative": ".dovo",
            "bootstrap_outcome": "already_initialized",
            "dirs_created": [],
            "project_id": None,
            "identity_path_relative": ".dovo/project.json",
            "identity_preserved": False,
            "gitignore_path_relative": ".dovo/.gitignore",
            "gitignore_tracked_entries": list(DOVO_GITIGNORE_TRACKED_ENTRIES),
            "config_created": False,
            "config_overwritten": False,
            "config_repaired": False,
            "config_skipped_existing": False,
            "config_path_relative": None,
            "inserted_keys": [],
            "seeded_files": [],
            "skipped_seed_files": [],
            "overwritten_seed_files": [],
            "failure_mode": "bootstrap",
            "errors": [IDENTITY_FAILURE_ERROR],
            "warnings": [],
            "remediations": [{"message": "Resolve the path conflict above, then rerun 'dovo init'", "command": None}],
            "diagnostics": [
                {
                    "event_id": "exc_3f9a1c07b2de",
                    "log_path": "/home/you/.dovo/logs/2026-10-19/exc_3f9a1c07b2de.json",
                }
            ],
        },
        id="identity_failure_payload",
    ),
]


class WorkspaceInitFormatterTests:
    """Tier 2 presentation contract tests for WorkspaceInitFormatter."""

    @pytest.mark.parametrize("case", INIT_CASES)
    def test_transform_derives_expected_view(self, case: FormatterCase[WorkspaceInitResult, WorkspaceInitView]) -> None:
        assert_transform_derives_expected_view(WorkspaceInitFormatter, case.data, case.view)

    @pytest.mark.parametrize(("case", "expected_payload"), INIT_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[WorkspaceInitResult, WorkspaceInitView],
        expected_payload: dict[str, Any],
    ) -> None:
        assert_json_payload_matches_published_shape(WorkspaceInitFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", INIT_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[WorkspaceInitResult, WorkspaceInitView]
    ) -> None:
        assert_rich_render_shows_every_view_value(WorkspaceInitFormatter, case.data, case.render_expectations)
