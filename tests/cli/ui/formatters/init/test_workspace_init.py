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
from dovo.core.bootstrap.models import BootstrapOutcome, BootstrapSummary, WorkspaceInitResult
from dovo.core.catalog.models import SeedSummary
from dovo.core.config.models import ConfigGenerationSummary
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
RERUN_REMEDIATION = Remediation(message="Resolve the path conflict above, then rerun 'dovo init'")


def make_bootstrap(
    outcome: BootstrapOutcome = BootstrapOutcome.ALREADY_INITIALIZED,
    *,
    dirs_created: tuple[Path, ...] = (),
) -> BootstrapSummary:
    """Construct a BootstrapSummary under DOVO with the given outcome and created directories."""
    return BootstrapSummary(
        root_path=DOVO,
        outcome=outcome,
        root_created=outcome == BootstrapOutcome.INITIALIZED,
        dirs_created=dirs_created,
        dirs_existing=(),
        repaired=outcome == BootstrapOutcome.REPAIRED,
        gitignore_created=outcome == BootstrapOutcome.INITIALIZED,
    )


def make_config(
    *,
    created: bool = False,
    skipped_existing: bool = False,
    repaired: bool = False,
    overwritten: bool = False,
    inserted_keys: tuple[str, ...] = (),
) -> ConfigGenerationSummary:
    """Construct a ConfigGenerationSummary for CONFIG_PATH with the given flags."""
    return ConfigGenerationSummary(
        config_path=CONFIG_PATH,
        created=created,
        skipped_existing=skipped_existing,
        repaired=repaired,
        overwritten=overwritten,
        inserted_keys=inserted_keys,
        warnings=(),
    )


def make_seed(
    *, created: tuple[Path, ...] = (), skipped: tuple[Path, ...] = (), overwritten: tuple[Path, ...] = ()
) -> SeedSummary:
    """Construct a SeedSummary with the given created, skipped and overwritten files."""
    return SeedSummary(created_files=created, skipped_existing_files=skipped, overwritten_files=overwritten)


def make_init_view(**overrides: object) -> WorkspaceInitView:
    """Construct a WorkspaceInitView with baseline initialized defaults."""
    defaults: dict[str, object] = {
        "ok": True,
        "root_path": DOVO,
        "root_path_relative": ".dovo",
        "bootstrap_outcome": BootstrapOutcome.INITIALIZED,
        "dirs_created": [".dovo/.meta"],
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
        "error_code": None,
        "errors": [],
        "warnings": [],
        "remediations": [],
        "diagnostics": [],
    }
    return WorkspaceInitView.model_validate({**defaults, **overrides})


def make_failure_view(**overrides: object) -> WorkspaceInitView:
    """Construct a failed WorkspaceInitView whose steps after bootstrap and identity did not run."""
    defaults: dict[str, object] = {
        "ok": False,
        "bootstrap_outcome": BootstrapOutcome.ALREADY_INITIALIZED,
        "dirs_created": [],
        "config_created": False,
        "config_path_relative": None,
        "seeded_files": [],
    }
    return make_init_view(**{**defaults, **overrides})


INITIALIZED = FormatterCase(
    data=WorkspaceInitResult(
        bootstrap=make_bootstrap(BootstrapOutcome.INITIALIZED, dirs_created=(DOVO / ".meta",)),
        project_identity=BASELINE_IDENTITY,
        identity_path=DOVO / "project.json",
        identity_outcome=ProjectIdentityOutcome.CREATED,
        config_generation=make_config(created=True),
        seed=make_seed(created=(DOVO / "catalog" / "blueprints" / "test.yml",)),
    ),
    view=make_init_view(),
    render_expectations=[
        "Initialized Dovo at .dovo",
        ".dovo/config.json",
        ".dovo/.meta",
        ".dovo/catalog/blueprints/test.yml",
        "test-project",
        ".dovo/project.json",
        "catalog/",
        "Next:",
    ],
)

REPAIRED = FormatterCase(
    data=WorkspaceInitResult(
        bootstrap=make_bootstrap(BootstrapOutcome.REPAIRED, dirs_created=(DOVO / ".meta",)),
        project_identity=BASELINE_IDENTITY,
        identity_path=DOVO / "project.json",
        identity_outcome=ProjectIdentityOutcome.CREATED,
        config_generation=make_config(repaired=True, inserted_keys=("telemetry.enabled",)),
        seed=make_seed(skipped=(DOVO / "catalog" / "blueprints" / "fix-tests.yml",)),
    ),
    view=make_init_view(
        bootstrap_outcome=BootstrapOutcome.REPAIRED,
        config_created=False,
        config_repaired=True,
        inserted_keys=["telemetry.enabled"],
        seeded_files=[],
        skipped_seed_files=[".dovo/catalog/blueprints/fix-tests.yml"],
    ),
    render_expectations=[
        "Dovo structure repaired at .dovo",
        ".dovo/config.json",
        ".dovo/.meta",
        "telemetry.enabled",
        ".dovo/catalog/blueprints/fix-tests.yml",
    ],
)

ALREADY_INITIALIZED = FormatterCase(
    data=WorkspaceInitResult(
        bootstrap=make_bootstrap(),
        project_identity=BASELINE_IDENTITY,
        identity_path=DOVO / "project.json",
        identity_outcome=ProjectIdentityOutcome.CREATED,
        config_generation=make_config(overwritten=True),
        seed=make_seed(overwritten=(DOVO / "catalog" / "blueprints" / "x.yml",)),
    ),
    view=make_init_view(
        bootstrap_outcome=BootstrapOutcome.ALREADY_INITIALIZED,
        dirs_created=[],
        config_created=False,
        config_overwritten=True,
        seeded_files=[],
        overwritten_seed_files=[".dovo/catalog/blueprints/x.yml"],
    ),
    render_expectations=["Dovo already initialized at .dovo", ".dovo/config.json", "Refreshed starter blueprints"],
)

CONFIG_SKIPPED_EXISTING = FormatterCase(
    data=WorkspaceInitResult(
        bootstrap=make_bootstrap(),
        project_identity=BASELINE_IDENTITY,
        identity_path=DOVO / "project.json",
        identity_outcome=ProjectIdentityOutcome.CREATED,
        config_generation=make_config(skipped_existing=True),
        seed=make_seed(),
    ),
    view=make_init_view(
        bootstrap_outcome=BootstrapOutcome.ALREADY_INITIALIZED,
        dirs_created=[],
        config_created=False,
        config_skipped_existing=True,
        seeded_files=[],
    ),
    render_expectations=[".dovo", "Config exists: ./.dovo/config.json", "Starter blueprints already present"],
)

WARNINGS = FormatterCase(
    data=WorkspaceInitResult(
        bootstrap=make_bootstrap(),
        project_identity=BASELINE_IDENTITY,
        identity_path=DOVO / "project.json",
        identity_outcome=ProjectIdentityOutcome.PRESERVED,
        config_generation=make_config(overwritten=True),
        seed=make_seed(),
        warnings=[PRESERVE_WARNING, "repair ignored when overwrite=True"],
    ),
    view=make_init_view(
        bootstrap_outcome=BootstrapOutcome.ALREADY_INITIALIZED,
        dirs_created=[],
        identity_preserved=True,
        config_created=False,
        config_overwritten=True,
        seeded_files=[],
        warnings=[PRESERVE_WARNING, "repair ignored when overwrite=True"],
    ),
    render_expectations=[".dovo", "test-project", ".dovo/project.json", PRESERVE_WARNING],
)

PREFLIGHT_ERROR = "The current directory is not a valid Git repository."

PREFLIGHT_FAILURE = FormatterCase(
    data=WorkspaceInitResult(error_code=ErrorCode.BOOTSTRAP_REPO_NOT_FOUND, errors=[PREFLIGHT_ERROR]),
    view=make_failure_view(
        root_path=None,
        root_path_relative=None,
        bootstrap_outcome=None,
        project_id=None,
        identity_path_relative=None,
        gitignore_path_relative=None,
        gitignore_tracked_entries=[],
        error_code=ErrorCode.BOOTSTRAP_REPO_NOT_FOUND,
        errors=[PREFLIGHT_ERROR],
    ),
    render_expectations=["Initialization Failed!", PREFLIGHT_ERROR],
)

BOOTSTRAP_ERROR = "/workspace/my-repo/.dovo exists but is a file, not a directory."

BOOTSTRAP_FAILURE = FormatterCase(
    data=WorkspaceInitResult(
        error_code=ErrorCode.BOOTSTRAP_PATH_INVALID,
        errors=[BOOTSTRAP_ERROR],
        remediations=[RERUN_REMEDIATION],
    ),
    view=make_failure_view(
        root_path=None,
        root_path_relative=None,
        bootstrap_outcome=None,
        project_id=None,
        identity_path_relative=None,
        gitignore_path_relative=None,
        gitignore_tracked_entries=[],
        error_code=ErrorCode.BOOTSTRAP_PATH_INVALID,
        errors=[BOOTSTRAP_ERROR],
        remediations=[RERUN_REMEDIATION],
    ),
    render_expectations=[
        "Initialization Failed!",
        BOOTSTRAP_ERROR,
        "Fix:",
        "- Resolve the path conflict above, then rerun 'dovo init'",
    ],
)

IDENTITY_FAILURE_ERROR = "Unable to read project identity at '/workspace/my-repo/.dovo/project.json': denied."
IDENTITY_FAILURE_RECORD = DiagnosticRecord(
    event_id="exc_3f9a1c07b2de",
    log_path=Path("/home/you/.dovo/logs/2026-10-19/exc_3f9a1c07b2de.json"),
    level=DiagnosticLevel.ERROR,
)

IDENTITY_FAILURE = FormatterCase(
    data=WorkspaceInitResult(
        bootstrap=make_bootstrap(),
        identity_path=DOVO / "project.json",
        error_code=ErrorCode.PROJECT_IDENTITY_UNREADABLE,
        errors=[IDENTITY_FAILURE_ERROR],
        diagnostics=[IDENTITY_FAILURE_RECORD],
    ),
    view=make_failure_view(
        project_id=None,
        error_code=ErrorCode.PROJECT_IDENTITY_UNREADABLE,
        errors=[IDENTITY_FAILURE_ERROR],
        diagnostics=[IDENTITY_FAILURE_RECORD],
    ),
    render_expectations=[
        "Dovo already initialized at .dovo",
        "Initialization Failed!",
        "Unable to read project identity at",
        "exc_3f9a1c07b2de",
    ],
)

CONFIG_ERROR = "Config path '/workspace/my-repo/.dovo/config.json' is a directory."

CONFIG_FAILURE = FormatterCase(
    data=WorkspaceInitResult(
        bootstrap=make_bootstrap(),
        project_identity=BASELINE_IDENTITY,
        identity_path=DOVO / "project.json",
        identity_outcome=ProjectIdentityOutcome.CREATED,
        error_code=ErrorCode.CONFIG_PATH_INVALID,
        errors=[CONFIG_ERROR],
    ),
    view=make_failure_view(error_code=ErrorCode.CONFIG_PATH_INVALID, errors=[CONFIG_ERROR]),
    render_expectations=[
        "Dovo already initialized at .dovo",
        "test-project",
        ".dovo/project.json",
        "Initialization Failed!",
        CONFIG_ERROR,
    ],
)

SEED_ERROR = (
    "Could not seed the starter catalog templates:\n"
    "/workspace/my-repo/.dovo/catalog/blueprints/dovo/fix-tests.yml exists as a directory, not a file."
)

SEED_FAILURE = FormatterCase(
    data=WorkspaceInitResult(
        bootstrap=make_bootstrap(),
        project_identity=BASELINE_IDENTITY,
        identity_path=DOVO / "project.json",
        identity_outcome=ProjectIdentityOutcome.CREATED,
        config_generation=make_config(created=True),
        error_code=ErrorCode.CATALOG_SEED_FAILED,
        errors=[SEED_ERROR],
    ),
    view=make_failure_view(
        config_created=True,
        config_path_relative=".dovo/config.json",
        error_code=ErrorCode.CATALOG_SEED_FAILED,
        errors=[SEED_ERROR],
    ),
    render_expectations=[
        "Generated config: ./.dovo/config.json",
        "Initialization Failed!",
        "Could not seed the starter catalog templates:",
        "fix-tests.yml exists as a directory, not a file.",
    ],
)

INIT_CASES = [
    pytest.param(INITIALIZED, id="initialized"),
    pytest.param(REPAIRED, id="repaired"),
    pytest.param(ALREADY_INITIALIZED, id="already-initialized"),
    pytest.param(CONFIG_SKIPPED_EXISTING, id="config-skipped-existing"),
    pytest.param(PREFLIGHT_FAILURE, id="preflight"),
    pytest.param(BOOTSTRAP_FAILURE, id="bootstrap-failure"),
    pytest.param(IDENTITY_FAILURE, id="identity-failure-with-diagnostics"),
    pytest.param(CONFIG_FAILURE, id="config-failure"),
    pytest.param(SEED_FAILURE, id="seed-failure"),
    pytest.param(WARNINGS, id="warnings"),
]

INIT_PAYLOAD_CASES = [
    pytest.param(
        INITIALIZED,
        {
            "ok": True,
            "root_path": "/workspace/my-repo/.dovo",
            "root_path_relative": ".dovo",
            "bootstrap_outcome": "initialized",
            "dirs_created": [".dovo/.meta"],
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
            "error_code": None,
            "errors": [],
            "warnings": [],
            "remediations": [],
            "diagnostics": [],
        },
        id="initialized",
    ),
    pytest.param(
        BOOTSTRAP_FAILURE,
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
            "error_code": "BOOTSTRAP_PATH_INVALID",
            "errors": [BOOTSTRAP_ERROR],
            "warnings": [],
            "remediations": [{"message": "Resolve the path conflict above, then rerun 'dovo init'", "command": None}],
            "diagnostics": [],
        },
        id="bootstrap-failure",
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
            "error_code": "PROJECT_IDENTITY_UNREADABLE",
            "errors": [IDENTITY_FAILURE_ERROR],
            "warnings": [],
            "remediations": [],
            "diagnostics": [
                {
                    "event_id": "exc_3f9a1c07b2de",
                    "log_path": "/home/you/.dovo/logs/2026-10-19/exc_3f9a1c07b2de.json",
                }
            ],
        },
        id="identity-failure-with-diagnostics",
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
