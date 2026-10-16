"""Unit tests for dovo.core.diagnostics.services.registry."""

import pytest

from dovo.core.diagnostics.checks.agent_setup import AgentSetupCheck
from dovo.core.diagnostics.checks.artifacts_integrity import ArtifactsIntegrityCheck
from dovo.core.diagnostics.checks.config_schema import ConfigSchemaCheck
from dovo.core.diagnostics.checks.env_binaries import EnvBinariesCheck
from dovo.core.diagnostics.checks.filesystem_writable import FilesystemWritableCheck
from dovo.core.diagnostics.checks.git_repo import GitRepoCheck
from dovo.core.diagnostics.checks.worktree_refs import WorktreeRefsCheck
from dovo.core.diagnostics.exceptions import CheckRegistrationError
from dovo.core.diagnostics.models import (
    CheckCategory,
    CheckStatus,
    DiagnosticCheckResult,
    DiagnosticsContext,
)
from dovo.core.diagnostics.services.registry import CheckRegistry, get_default_registry


class DummyCheck:
    """Protocol-conforming test double for DiagnosticCheck."""

    def __init__(
        self,
        check_id: str,
        name: str = "Dummy Check",
        category: CheckCategory = CheckCategory.GIT,
    ) -> None:
        self.check_id = check_id
        self.name = name
        self.category = category

    def execute(self, context: DiagnosticsContext) -> DiagnosticCheckResult:
        return DiagnosticCheckResult(
            check_id=self.check_id,
            name=self.name,
            category=self.category,
            status=CheckStatus.OK,
            message="Check executed successfully.",
            details={},
            duration_ms=0.1,
            error_code=None,
            errors=[],
            warnings=[],
            fixes=[],
        )


class CheckRegistryTests:
    """Unit tests for CheckRegistry lifecycle operations."""

    def test_register_duplicate_raises_check_registration_error(self) -> None:
        registry = CheckRegistry()
        check1 = DummyCheck(check_id="duplicate.id", name="First", category=CheckCategory.GIT)
        check2 = DummyCheck(check_id="duplicate.id", name="Second", category=CheckCategory.CONFIG)

        registry.register(check1)

        with pytest.raises(CheckRegistrationError) as exc_info:
            registry.register(check2)

        assert "duplicate.id" in str(exc_info.value)
        assert registry.all() == [check1]

    def test_all_returns_all_registered_checks(self) -> None:
        registry = CheckRegistry()
        assert registry.all() == []

        check1 = DummyCheck(check_id="one", category=CheckCategory.GIT)
        check2 = DummyCheck(check_id="two", category=CheckCategory.AGENT)

        registry.register(check1)
        registry.register(check2)

        assert registry.all() == [check1, check2]


class DefaultRegistryTests:
    """Unit tests for get_default_registry factory wiring."""

    def test_get_default_registry_registers_all_seven_builtin_checks(self) -> None:
        registry = get_default_registry()

        assert [(check.check_id, type(check)) for check in registry.all()] == [
            ("git.repo", GitRepoCheck),
            ("config.schema", ConfigSchemaCheck),
            ("filesystem.writable", FilesystemWritableCheck),
            ("worktree.refs", WorktreeRefsCheck),
            ("env.binaries", EnvBinariesCheck),
            ("agent.setup", AgentSetupCheck),
            ("artifacts.integrity", ArtifactsIntegrityCheck),
        ]
