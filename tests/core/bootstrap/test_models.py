"""Contract tests for WorkspaceInitResult's ok and its success invariant."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from dovo.common.error_codes import ErrorCode
from dovo.core.bootstrap.models import BootstrapOutcome, BootstrapSummary, WorkspaceInitResult
from dovo.core.catalog.models import SeedSummary
from dovo.core.config.models import ConfigGenerationSummary
from dovo.core.project.models import ProjectIdentity

_BOOTSTRAP = BootstrapSummary(
    root_path=Path("/repo/.dovo"),
    outcome=BootstrapOutcome.INITIALIZED,
    root_created=True,
    dirs_created=(Path("/repo/.dovo/.meta"),),
    dirs_existing=(),
    repaired=False,
    gitignore_created=True,
)
_IDENTITY = ProjectIdentity(id="demo-project", created_at=datetime(2026, 1, 2, tzinfo=UTC))
_CONFIG = ConfigGenerationSummary(
    config_path=Path("/repo/.dovo/config.json"),
    created=True,
    skipped_existing=False,
    repaired=False,
    overwritten=False,
    inserted_keys=(),
    warnings=(),
)
_SEED = SeedSummary(created_files=(), skipped_existing_files=(), overwritten_files=())


def _success_without(missing: str | None = None) -> WorkspaceInitResult:
    """Build a result with no error_code holding every step except missing."""
    return WorkspaceInitResult(
        bootstrap=None if missing == "bootstrap" else _BOOTSTRAP,
        project_identity=None if missing == "project_identity" else _IDENTITY,
        config_generation=None if missing == "config_generation" else _CONFIG,
        seed=None if missing == "seed" else _SEED,
    )


def _failure() -> WorkspaceInitResult:
    return WorkspaceInitResult(error_code=ErrorCode.CONFIG_PATH_INVALID, errors=["bad"])


class WorkspaceInitResultTests:
    @pytest.mark.parametrize(
        ("build", "ok"),
        [
            pytest.param(_success_without, True, id="success"),
            pytest.param(_failure, False, id="failure"),
        ],
    )
    def test_ok_is_true_exactly_when_error_code_is_null(
        self, build: Callable[[], WorkspaceInitResult], ok: bool
    ) -> None:
        assert build().ok is ok

    @pytest.mark.parametrize("missing", ["bootstrap", "project_identity", "config_generation", "seed"])
    def test_success_missing_a_step_is_rejected(self, missing: str) -> None:
        with pytest.raises(ValidationError):
            _success_without(missing)
