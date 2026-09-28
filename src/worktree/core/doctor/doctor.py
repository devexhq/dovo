"""Domain entrypoint coordinator for Worktree doctor diagnostics."""

from worktree.common.filesystem import WorkspacePaths
from worktree.core.config.models import WorktreeConfig
from worktree.core.config.services.resolve import resolve_effective_config
from worktree.core.doctor.models import (
    CheckCategory,
    DoctorContext,
    DoctorReport,
)
from worktree.core.doctor.services.registry import CheckRegistry, get_default_registry
from worktree.core.doctor.services.runner import DiagnosticRunner


class Doctor:
    """Domain entrypoint coordinator for Worktree doctor diagnostics."""

    def __init__(self, paths: WorkspacePaths, registry: CheckRegistry | None = None) -> None:
        """Initialize the coordinator's workspace paths, defaulting its check registry to the built-in check set."""
        self.paths = paths
        self.registry = registry if registry is not None else get_default_registry()

    def run_diagnostics(
        self,
        categories: list[CheckCategory] | None = None,
        config: WorktreeConfig | None = None,
    ) -> DoctorReport:
        """Run registered diagnostic checks and return aggregated DoctorReport."""
        active_config: WorktreeConfig | None = config
        if active_config is None:
            try:
                load_result = resolve_effective_config(self.paths)
                if load_result.ok:
                    active_config = load_result.config
            except Exception:
                active_config = None

        context = DoctorContext(cwd=self.paths.root_dir, config=active_config, paths=self.paths)
        runner = DiagnosticRunner(registry=self.registry)

        return runner.run_checks(context=context, categories=categories)
