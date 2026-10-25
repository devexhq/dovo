"""Execution runner for diagnostic checks with containment, filtering, and timing."""

from typing import Final

from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord, append_diagnostic_hint, record_exception
from dovo.common.timing import Stopwatch
from dovo.core.config.models import DovoConfig
from dovo.core.diagnostics.models import (
    CheckCategory,
    CheckStatus,
    DiagnosticCheck,
    DiagnosticCheckResult,
    DiagnosticsContext,
    DiagnosticsReport,
)
from dovo.core.diagnostics.services.registry import CheckRegistry
from dovo.core.diagnostics.services.remediation import resolve_remediations

CHECK_ID_TO_DOCTOR_CONFIG_ATTR: Final[dict[str, str]] = {
    "git.repo": "check_git",
    "filesystem.writable": "check_paths_writable",
    "config.schema": "check_config_schema",
    "worktree.refs": "check_stale_worktrees",
    "env.binaries": "check_required_binaries",
}


def filter_checks_by_categories(
    checks: list[DiagnosticCheck],
    categories: list[CheckCategory] | None,
) -> list[DiagnosticCheck]:
    """Filter candidate checks by category list, retaining all if categories is None or empty."""
    if not categories:
        return list(checks)
    allowed = set(categories)
    return [c for c in checks if c.category in allowed]


def should_skip_by_config(check_id: str, config: DovoConfig | None) -> bool:
    """Check if check_id is toggled off in config.doctor."""
    if config is None:
        return False
    attr_name = CHECK_ID_TO_DOCTOR_CONFIG_ATTR.get(check_id)
    if attr_name is None:
        return False
    return not getattr(config.doctor, attr_name, True)


def execute_single_check(
    check: DiagnosticCheck,
    context: DiagnosticsContext,
) -> DiagnosticCheckResult:
    """Execute a single check with exception containment, measured duration, and remediation enrichment."""
    watch = Stopwatch.start()
    try:
        result = check.execute(context)
    except Exception as exc:
        record = record_exception(
            exc,
            level=DiagnosticLevel.ERROR,
            operation="doctor.check",
            context={"check": check.check_id, "path": str(context.cwd)},
        )
        result = _crash_result(check, exc, record)
    watch.stop()
    return _with_remediations(result.model_copy(update={"duration_seconds": watch.elapsed_seconds}))


def _crash_result(check: DiagnosticCheck, exc: Exception, record: DiagnosticRecord | None) -> DiagnosticCheckResult:
    """Return the FAILED DOCTOR_CHECK_CRASH result for a check that raised exc, hinting its message and error."""
    return DiagnosticCheckResult(
        check_id=check.check_id,
        name=check.name,
        category=check.category,
        status=CheckStatus.FAILED,
        message=append_diagnostic_hint(f"Unhandled exception during check execution: {exc}", record),
        details={"exception": str(exc), "type": type(exc).__name__},
        error_code="DOCTOR_CHECK_CRASH",
        errors=[append_diagnostic_hint(f"Unhandled exception in {check.check_id}: {exc}", record)],
        warnings=[],
    )


def _with_remediations(result: DiagnosticCheckResult) -> DiagnosticCheckResult:
    """Return result with remediations populated via resolve_remediations."""
    return result.model_copy(update={"remediations": resolve_remediations(result)})


class DiagnosticRunner:
    """Execution runner for diagnostic checks with containment, filtering, and timing."""

    def __init__(self, registry: CheckRegistry | None = None) -> None:
        self.registry = registry if registry is not None else CheckRegistry()

    def run_checks(
        self,
        context: DiagnosticsContext,
        categories: list[CheckCategory] | None = None,
    ) -> DiagnosticsReport:
        """Execute filtered checks against context, measuring durations and producing DiagnosticsReport."""
        watch = Stopwatch.start()
        candidate_checks = self.registry.all()
        checks_to_run = filter_checks_by_categories(candidate_checks, categories)

        results: list[DiagnosticCheckResult] = []
        for check in checks_to_run:
            if should_skip_by_config(check.check_id, context.config):
                results.append(
                    DiagnosticCheckResult(
                        check_id=check.check_id,
                        name=check.name,
                        category=check.category,
                        status=CheckStatus.SKIPPED,
                        message=f"Check '{check.check_id}' skipped by configuration.",
                        details={},
                        error_code=None,
                        errors=[],
                        warnings=[],
                    )
                )
            else:
                results.append(execute_single_check(check, context))

        watch.stop()
        return DiagnosticsReport(
            workspace_root=context.cwd,
            checks=results,
            duration_seconds=watch.elapsed_seconds,
        )
