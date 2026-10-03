"""Execution runner for diagnostic checks with containment, filtering, and timing."""

import time
from typing import Final

from dovo.core.config.models import DovoConfig
from dovo.core.doctor.models import (
    CheckCategory,
    CheckStatus,
    DiagnosticCheck,
    DiagnosticCheckResult,
    DoctorContext,
    DoctorReport,
)
from dovo.core.doctor.services.registry import CheckRegistry
from dovo.core.doctor.services.remediation import resolve_remediations

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
    context: DoctorContext,
) -> DiagnosticCheckResult:
    """Execute a single check with exception containment, timing, and remediation enrichment."""
    start_time = time.perf_counter()
    try:
        result = check.execute(context)
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        if result.duration_ms == 0.0:
            result = result.model_copy(update={"duration_ms": elapsed_ms})
        return _with_remediations(result)
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        result = DiagnosticCheckResult(
            check_id=check.check_id,
            name=check.name,
            category=check.category,
            status=CheckStatus.FAILED,
            message=f"Unhandled exception during check execution: {exc}",
            details={"exception": str(exc), "type": type(exc).__name__},
            duration_ms=elapsed_ms,
            error_code="DOCTOR_CHECK_CRASH",
            errors=[f"Unhandled exception in {check.check_id}: {exc}"],
            warnings=[],
            fixes=[],
        )
        return _with_remediations(result)


def _with_remediations(result: DiagnosticCheckResult) -> DiagnosticCheckResult:
    """Return result with remediations populated via resolve_remediations."""
    return result.model_copy(update={"remediations": resolve_remediations(result)})


class DiagnosticRunner:
    """Execution runner for diagnostic checks with containment, filtering, and timing."""

    def __init__(self, registry: CheckRegistry | None = None) -> None:
        self.registry = registry if registry is not None else CheckRegistry()

    def run_checks(
        self,
        context: DoctorContext,
        categories: list[CheckCategory] | None = None,
    ) -> DoctorReport:
        """Execute filtered checks against context, measuring durations and producing DoctorReport."""
        start_time = time.perf_counter()
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
                        duration_ms=0.0,
                        error_code=None,
                        errors=[],
                        warnings=[],
                        fixes=[],
                    )
                )
            else:
                results.append(execute_single_check(check, context))

        total_duration_ms = (time.perf_counter() - start_time) * 1000.0
        return DoctorReport(
            workspace_root=context.cwd,
            checks=results,
            total_duration_ms=total_duration_ms,
        )
