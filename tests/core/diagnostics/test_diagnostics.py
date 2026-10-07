"""Unit tests for dovo.core.diagnostics.diagnostics entrypoint coordinator."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from dovo.common.filesystem import Filesystem
from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.config.models import (
    ConfigTier,
    DoctorConfig,
    DovoConfig,
    ProjectConfig,
)
from dovo.core.config.serialize import serialize_config
from dovo.core.diagnostics.checks.agent_setup import AgentSetupCheck
from dovo.core.diagnostics.checks.config_schema import ConfigSchemaCheck
from dovo.core.diagnostics.checks.env_binaries import EnvBinariesCheck
from dovo.core.diagnostics.checks.filesystem_writable import FilesystemWritableCheck
from dovo.core.diagnostics.checks.git_repo import GitRepoCheck
from dovo.core.diagnostics.checks.worktree_refs import WorktreeRefsCheck
from dovo.core.diagnostics.diagnostics import Diagnostics
from dovo.core.diagnostics.models import (
    CheckCategory,
    CheckStatus,
    DiagnosticCheckResult,
    DiagnosticsContext,
)
from dovo.core.diagnostics.services.registry import CheckRegistry
from tests.harness.workspace_paths import initialized_workspace_paths


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return initialized_workspace_paths(root)


class DummyDiagnosticCheck:
    """Protocol-conforming test double for DiagnosticCheck."""

    def __init__(
        self,
        check_id: str,
        name: str = "Dummy Diagnostics Check",
        category: CheckCategory = CheckCategory.GIT,
    ) -> None:
        self.check_id = check_id
        self.name = name
        self.category = category
        self.captured_context: DiagnosticsContext | None = None

    def execute(self, context: DiagnosticsContext) -> DiagnosticCheckResult:
        self.captured_context = context
        return DiagnosticCheckResult(
            check_id=self.check_id,
            name=self.name,
            category=self.category,
            status=CheckStatus.OK,
            message=f"Executed {self.check_id}",
            details={},
            duration_ms=0.5,
            error_code=None,
            errors=[],
            warnings=[],
            fixes=[],
            remediations=[],
        )


class DiagnosticsCoordinatorTests:
    """Unit tests for Diagnostics entrypoint coordinator."""

    def test_run_diagnostics_delegates_to_runner_with_registered_checks(self, tmp_path: Path) -> None:
        """[tier-2/unit] Diagnostics.run_diagnostics: initializes context with self.path and executes checks registered in self.registry."""
        doctor = Diagnostics(_paths_for(tmp_path), registry=CheckRegistry())
        check = DummyDiagnosticCheck(check_id="test.delegation", category=CheckCategory.GIT)
        doctor.registry.register(check)

        report = doctor.run_diagnostics()

        assert report.workspace_root == tmp_path.resolve()
        assert len(report.checks) == 1
        assert report.checks[0].check_id == "test.delegation"
        assert report.checks[0].status == CheckStatus.OK
        assert report.ok is True
        assert check.captured_context is not None
        assert check.captured_context.cwd == tmp_path.resolve()

    def test_run_diagnostics_resolves_config_when_none_provided(self, tmp_path: Path) -> None:
        """[tier-2/unit] Diagnostics.run_diagnostics: when config is None, attempts loading config from path and attaches to DiagnosticsContext."""
        config_dir = tmp_path / ".dovo"
        config_dir.mkdir(parents=True, exist_ok=True)
        config = DovoConfig(
            version=1,
            project=ProjectConfig(name="resolved-project"),
            doctor=DoctorConfig(check_git=False),
        )
        Filesystem.atomic_write_json(config_dir / "config.json", serialize_config(config))

        doctor = Diagnostics(_paths_for(tmp_path), registry=CheckRegistry())
        git_check = DummyDiagnosticCheck(check_id="git.repo", category=CheckCategory.GIT)
        doctor.registry.register(git_check)

        report = doctor.run_diagnostics(config=None)

        assert report.workspace_root == tmp_path.resolve()
        assert len(report.checks) == 1
        assert report.checks[0].check_id == "git.repo"
        assert report.checks[0].status == CheckStatus.SKIPPED
        assert report.checks[0].message == "Check 'git.repo' skipped by configuration."

    def test_run_diagnostics_observes_user_tier_config_override(
        self, tmp_path: Path, write_tier_config: Callable[[ConfigTier, dict[str, Any] | str], Path]
    ) -> None:
        """[tier-2/unit] Diagnostics.run_diagnostics: User tier doctor.check_git=False (absent from repo config) skips the git.repo check, proving a non-config-domain Config consumer observes a Global/User tier override."""
        write_tier_config(ConfigTier.USER, {"doctor": {"check_git": False}})

        config_dir = tmp_path / ".dovo"
        config_dir.mkdir(parents=True, exist_ok=True)
        Filesystem.atomic_write_json(
            config_dir / "config.json", {"version": 1, "project": {"name": "resolved-project"}}
        )

        doctor = Diagnostics(_paths_for(tmp_path), registry=CheckRegistry())
        git_check = DummyDiagnosticCheck(check_id="git.repo", category=CheckCategory.GIT)
        doctor.registry.register(git_check)

        report = doctor.run_diagnostics(config=None)

        assert report.checks[0].check_id == "git.repo"
        assert report.checks[0].status == CheckStatus.SKIPPED
        assert report.checks[0].message == "Check 'git.repo' skipped by configuration."

    def test_run_diagnostics_uses_explicit_config_when_provided(self, tmp_path: Path) -> None:
        """[tier-2/unit] Diagnostics.run_diagnostics: when config is provided explicitly, uses it directly in DiagnosticsContext."""
        explicit_config = DovoConfig(
            version=1,
            project=ProjectConfig(name="explicit-project"),
            doctor=DoctorConfig(check_git=False),
        )

        doctor = Diagnostics(_paths_for(tmp_path), registry=CheckRegistry())
        git_check = DummyDiagnosticCheck(check_id="git.repo", category=CheckCategory.GIT)
        doctor.registry.register(git_check)

        report = doctor.run_diagnostics(config=explicit_config)

        assert report.workspace_root == tmp_path.resolve()
        assert len(report.checks) == 1
        assert report.checks[0].check_id == "git.repo"
        assert report.checks[0].status == CheckStatus.SKIPPED

    def test_run_diagnostics_propagates_category_filter(self, tmp_path: Path) -> None:
        """[tier-2/unit] Diagnostics.run_diagnostics: category filter argument is passed to DiagnosticRunner and filters report checks."""
        doctor = Diagnostics(_paths_for(tmp_path), registry=CheckRegistry())
        git_check = DummyDiagnosticCheck(check_id="git.repo", category=CheckCategory.GIT)
        config_check = DummyDiagnosticCheck(check_id="config.schema", category=CheckCategory.CONFIG)
        doctor.registry.register(git_check)
        doctor.registry.register(config_check)

        report = doctor.run_diagnostics(categories=[CheckCategory.CONFIG])

        assert report.workspace_root == tmp_path.resolve()
        assert len(report.checks) == 1
        assert report.checks[0].check_id == "config.schema"
        assert report.checks[0].status == CheckStatus.OK


class DiagnosticsDefaultRegistryTests:
    """Unit tests for Diagnostics.__init__ registry default-wiring."""

    def test_init_without_registry_uses_default_registry_with_all_builtin_checks(self, tmp_path: Path) -> None:
        """[tier-1/unit] Diagnostics.__init__: called with no registry argument -> self.registry has the 6 built-in checks."""
        doctor = Diagnostics(_paths_for(tmp_path))

        checks = doctor.registry.all()

        assert len(checks) == 6
        assert isinstance(doctor.registry.get("git.repo"), GitRepoCheck)
        assert isinstance(doctor.registry.get("config.schema"), ConfigSchemaCheck)
        assert isinstance(doctor.registry.get("filesystem.writable"), FilesystemWritableCheck)
        assert isinstance(doctor.registry.get("worktree.refs"), WorktreeRefsCheck)
        assert isinstance(doctor.registry.get("env.binaries"), EnvBinariesCheck)
        assert isinstance(doctor.registry.get("agent.setup"), AgentSetupCheck)

    def test_init_with_explicit_registry_does_not_use_default_registry(self, tmp_path: Path) -> None:
        """[tier-1/unit] Diagnostics.__init__: called with registry=CheckRegistry() -> self.registry stays that empty instance."""
        explicit_registry = CheckRegistry()

        doctor = Diagnostics(_paths_for(tmp_path), registry=explicit_registry)

        assert doctor.registry is explicit_registry
        assert doctor.registry.all() == []
