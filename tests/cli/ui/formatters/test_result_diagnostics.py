"""Cross-formatter contract: every result-backed JSON payload carries the result's diagnostics."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Final, cast

import pytest

from dovo.cli.ui.formatters.registry import FORMATTER_GROUPS, formatters_for
from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord
from dovo.common.models import BaseResult, Remediation
from dovo.core.artifacts import (
    ArtifactDownloadResult,
    ArtifactDownloadStatus,
    ArtifactsListResult,
    ArtifactsPruneResult,
)
from dovo.core.bootstrap import WorkspaceInitResult
from dovo.core.catalog import (
    CatalogCreateResult,
    CatalogDeleteResult,
    CatalogListResult,
    CatalogShowResult,
    CatalogValidateResult,
)
from dovo.core.config import (
    ConfigLoadResult,
    ConfigLoadStatus,
    ConfigSetResult,
    ConfigSetStatus,
    ConfigUnsetResult,
    ConfigUnsetStatus,
    ConfigValidationResult,
    ConfigValidationStatus,
)
from dovo.core.diagnostics import CheckStatus, DiagnosticsReport
from dovo.core.sessions import (
    DiffResult,
    DiffStatus,
    HistoryListResult,
    HistoryShowResult,
    HistoryShowStatus,
    LogsShowResult,
    LogsShowStatus,
)
from dovo.core.status import (
    CatalogStatusInfo,
    ConfigStatusInfo,
    DatabaseStatusInfo,
    DovoStatusResult,
    GitStatusInfo,
    WorktreeStatusInfo,
)
from dovo.core.worktree import (
    WorktreeApplyResult,
    WorktreeCreateResult,
    WorktreeDeleteResult,
    WorktreeDiffResult,
    WorktreeListResult,
    WorktreePruneResult,
    WorktreeShowResult,
)
from tests.harness.builders import diagnostic_result
from tests.harness.formatter import render_rich

ROOT: Final[Path] = Path("/workspace/my-repo")
RECORD: Final[DiagnosticRecord] = DiagnosticRecord(
    event_id="exc_3f9a1c07b2de",
    log_path=Path("/home/me/.dovo/logs/2026-10-23/exc_3f9a1c07b2de.json"),
    level=DiagnosticLevel.ERROR,
)
RECORD_JSON: Final[dict[str, str]] = {
    "event_id": "exc_3f9a1c07b2de",
    "log_path": "/home/me/.dovo/logs/2026-10-23/exc_3f9a1c07b2de.json",
}
RECORD_LINE: Final[str] = (
    "Details logged: /home/me/.dovo/logs/2026-10-23/exc_3f9a1c07b2de.json (event exc_3f9a1c07b2de)"
)
CONFIG_PATH: Final[Path] = ROOT / ".dovo" / "config.json"


def _status_sample() -> DovoStatusResult:
    """Build a DovoStatusResult for an initialized workspace carrying [RECORD]."""
    return DovoStatusResult(
        root_dir=ROOT,
        is_initialized=True,
        git=GitStatusInfo(is_git_repo=True, branch="main", is_dirty=False, uncommitted_files=0),
        config=ConfigStatusInfo(status=ConfigLoadStatus.OK, config_path=CONFIG_PATH, is_valid=True),
        catalog=CatalogStatusInfo(
            exists=True,
            catalog_dir=ROOT / ".dovo" / "catalog",
            total_items=0,
            blueprints_count=0,
            steps_count=0,
            invalid_items=0,
        ),
        database=DatabaseStatusInfo(exists=True, db_path=ROOT / ".dovo" / "data.db", is_accessible=True),
        worktrees=WorktreeStatusInfo(active_worktrees=0, total_worktrees=0, max_active_worktrees=5),
        diagnostics=[RECORD],
    )


SAMPLES: Final[dict[type[BaseResult], Callable[[], BaseResult]]] = {
    ArtifactsListResult: lambda: ArtifactsListResult(diagnostics=[RECORD]),
    ArtifactDownloadResult: lambda: ArtifactDownloadResult(
        status=ArtifactDownloadStatus.NOT_FOUND, diagnostics=[RECORD]
    ),
    ArtifactsPruneResult: lambda: ArtifactsPruneResult(diagnostics=[RECORD]),
    CatalogListResult: lambda: CatalogListResult(diagnostics=[RECORD]),
    CatalogShowResult: lambda: CatalogShowResult(diagnostics=[RECORD]),
    CatalogDeleteResult: lambda: CatalogDeleteResult(diagnostics=[RECORD]),
    CatalogCreateResult: lambda: CatalogCreateResult(diagnostics=[RECORD]),
    CatalogValidateResult: lambda: CatalogValidateResult(valid=False, target="my-bp", diagnostics=[RECORD]),
    ConfigLoadResult: lambda: ConfigLoadResult(
        status=ConfigLoadStatus.NOT_FOUND, config_path=CONFIG_PATH, diagnostics=[RECORD]
    ),
    ConfigValidationResult: lambda: ConfigValidationResult(
        status=ConfigValidationStatus.NOT_FOUND, config_path=CONFIG_PATH, diagnostics=[RECORD]
    ),
    ConfigSetResult: lambda: ConfigSetResult(
        status=ConfigSetStatus.NOT_FOUND, config_path=CONFIG_PATH, key="project.name", diagnostics=[RECORD]
    ),
    ConfigUnsetResult: lambda: ConfigUnsetResult(
        status=ConfigUnsetStatus.NOT_FOUND,
        config_path=CONFIG_PATH,
        key="project.name",
        existed=False,
        diagnostics=[RECORD],
    ),
    DiffResult: lambda: DiffResult(status=DiffStatus.SESSION_NOT_FOUND, diagnostics=[RECORD]),
    HistoryListResult: lambda: HistoryListResult(diagnostics=[RECORD]),
    HistoryShowResult: lambda: HistoryShowResult(status=HistoryShowStatus.NOT_FOUND, diagnostics=[RECORD]),
    LogsShowResult: lambda: LogsShowResult(status=LogsShowStatus.SESSION_NOT_FOUND, diagnostics=[RECORD]),
    WorkspaceInitResult: lambda: WorkspaceInitResult(
        error_code="INTERNAL_ERROR", errors=["boom"], diagnostics=[RECORD]
    ),
    WorktreePruneResult: lambda: WorktreePruneResult(diagnostics=[RECORD]),
    WorktreeShowResult: lambda: WorktreeShowResult(error_code="WORKTREE_NOT_FOUND", diagnostics=[RECORD]),
    WorktreeListResult: lambda: WorktreeListResult(diagnostics=[RECORD]),
    WorktreeCreateResult: lambda: WorktreeCreateResult(error_code="WORKTREE_PATH_CONFLICT", diagnostics=[RECORD]),
    WorktreeApplyResult: lambda: WorktreeApplyResult(
        error_code="WORKTREE_NOT_FOUND", worktree_id="wt-1", diagnostics=[RECORD]
    ),
    WorktreeDeleteResult: lambda: WorktreeDeleteResult(diagnostics=[RECORD]),
    WorktreeDiffResult: lambda: WorktreeDiffResult(
        error_code="WORKTREE_NOT_FOUND", worktree_id="wt-1", diagnostics=[RECORD]
    ),
    DovoStatusResult: _status_sample,
}


# The results whose formatters render their diagnostics in terminal output.
RENDERED: Final[list[type[BaseResult]]] = [
    CatalogListResult,
    CatalogShowResult,
    CatalogCreateResult,
    CatalogDeleteResult,
    CatalogValidateResult,
    ConfigLoadResult,
    ConfigValidationResult,
    ConfigSetResult,
    ConfigUnsetResult,
    WorkspaceInitResult,
    DovoStatusResult,
    HistoryListResult,
    WorktreePruneResult,
    WorktreeShowResult,
    WorktreeListResult,
    WorktreeCreateResult,
    WorktreeApplyResult,
    WorktreeDeleteResult,
    WorktreeDiffResult,
]


def _registered_result_models() -> set[type[BaseResult]]:
    """Return every BaseResult model with a formatter in FORMATTER_GROUPS."""
    return {
        model_cls
        for group in FORMATTER_GROUPS.values()
        for model_cls in group(None)
        if issubclass(model_cls, BaseResult)
    }


class ResultPayloadDiagnosticsTests:
    def test_every_registered_result_formatter_has_a_sample(self) -> None:
        """Require SAMPLES' keys to equal every BaseResult model registered in FORMATTER_GROUPS, so a new formatter fails until it has a sample."""
        assert set(SAMPLES) == _registered_result_models()

    @pytest.mark.parametrize("model_cls", list(SAMPLES), ids=lambda model_cls: model_cls.__name__)
    def test_payload_carries_result_diagnostics_after_remediations(self, model_cls: type[BaseResult]) -> None:
        """Emit payload["diagnostics"] == [RECORD_JSON] for a sample carrying [RECORD], with the key after "remediations" when the payload has one."""
        payload = formatters_for(model_cls)[model_cls].to_json_serializable(SAMPLES[model_cls]())

        assert payload["diagnostics"] == [RECORD_JSON]
        keys = list(payload)
        if "remediations" in keys:
            assert keys.index("diagnostics") > keys.index("remediations")

    def test_doctor_payload_lists_check_diagnostics_in_check_order(self, tmp_path: Path) -> None:
        """Emit each check's diagnostics on its checks[] entry and their concatenation, in check order, as the top-level diagnostics."""
        record_b = DiagnosticRecord(
            event_id="exc_bbbbbbbbbbbb", log_path=tmp_path / "exc_bbbbbbbbbbbb.json", level=DiagnosticLevel.ERROR
        )
        report = DiagnosticsReport(
            workspace_root=ROOT,
            checks=[
                diagnostic_result(check_id="check.a", status=CheckStatus.FAILED, diagnostics=[RECORD]),
                diagnostic_result(check_id="check.none"),
                diagnostic_result(check_id="check.b", status=CheckStatus.FAILED, diagnostics=[record_b]),
            ],
            diagnostics=[RECORD, record_b],
        )
        record_b_json = {"event_id": "exc_bbbbbbbbbbbb", "log_path": str(tmp_path / "exc_bbbbbbbbbbbb.json")}

        payload = formatters_for(DiagnosticsReport)[DiagnosticsReport].to_json_serializable(report)

        checks = cast(list[dict[str, object]], payload["checks"])
        assert [check["diagnostics"] for check in checks] == [[RECORD_JSON], [], [record_b_json]]
        assert payload["diagnostics"] == [RECORD_JSON, record_b_json]


class ResultRenderDiagnosticsTests:
    @pytest.mark.parametrize("model_cls", RENDERED, ids=lambda model_cls: model_cls.__name__)
    def test_formatter_renders_one_details_line_per_record(self, model_cls: type[BaseResult]) -> None:
        """Render 'Details logged: /home/me/.dovo/logs/2026-10-23/exc_3f9a1c07b2de.json (event exc_3f9a1c07b2de)' for SAMPLES[model_cls] in terminal output."""
        rendered = render_rich(formatters_for(model_cls)[model_cls].to_rich(SAMPLES[model_cls]()))

        assert rendered.count(RECORD_LINE) == 1

    def test_doctor_report_renders_details_line_after_fix_block(self) -> None:
        """Render a report whose crashed check carries [RECORD] and show its 'Details logged: …' line after the 'Fix:' text."""
        crashed = diagnostic_result(
            check_id="check.crash",
            status=CheckStatus.FAILED,
            message="Unhandled exception during check execution: boom",
            error_code="DOCTOR_CHECK_CRASH",
            errors=["Unhandled exception in check.crash: boom"],
            remediations=[Remediation(message="Rerun 'dovo doctor' after fixing the check.")],
            diagnostics=[RECORD],
        )
        report = DiagnosticsReport(workspace_root=ROOT, checks=[crashed], diagnostics=[RECORD])

        rendered = render_rich(formatters_for(DiagnosticsReport)[DiagnosticsReport].to_rich(report))

        assert rendered.count(RECORD_LINE) == 1
        assert rendered.index("Fix:") < rendered.index(RECORD_LINE)
