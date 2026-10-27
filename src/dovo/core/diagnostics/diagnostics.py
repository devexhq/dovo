"""Domain entrypoint coordinator for Dovo doctor diagnostics."""

from dovo.common.diagnostic_log import DiagnosticLevel, record_exception
from dovo.common.filesystem import WorkspacePaths
from dovo.core.config import Config
from dovo.core.config.models import DovoConfig
from dovo.core.diagnostics.models import (
    CheckCategory,
    DiagnosticsContext,
    DiagnosticsReport,
)
from dovo.core.diagnostics.services.registry import CheckRegistry, get_default_registry
from dovo.core.diagnostics.services.runner import DiagnosticRunner
from dovo.core.git import GitRunner


class Diagnostics:
    """Domain entrypoint coordinator for Dovo doctor diagnostics."""

    def __init__(self, paths: WorkspacePaths, *, git: GitRunner, registry: CheckRegistry | None = None) -> None:
        """Initialize the coordinator's workspace paths and git runner, defaulting its check registry to the built-in check set."""
        self.paths = paths
        self.git = git
        self.registry = registry if registry is not None else get_default_registry()

    def run_diagnostics(
        self,
        categories: list[CheckCategory] | None = None,
        config: DovoConfig | None = None,
    ) -> DiagnosticsReport:
        """Run registered diagnostic checks and return aggregated DiagnosticsReport."""
        active_config: DovoConfig | None = config
        if active_config is None:
            try:
                load_result = Config(self.paths).load()
                if load_result.ok:
                    active_config = load_result.config
            except Exception as exc:
                record_exception(
                    exc,
                    level=DiagnosticLevel.DEBUG,
                    operation="diagnostics.check",
                    context={"path": str(self.paths.root_dir)},
                )
                active_config = None

        context = DiagnosticsContext(cwd=self.paths.root_dir, config=active_config, paths=self.paths, git=self.git)
        runner = DiagnosticRunner(registry=self.registry)

        return runner.run_checks(context=context, categories=categories)
