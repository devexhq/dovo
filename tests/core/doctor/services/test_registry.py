"""Unit tests for dovo.core.doctor.services.registry."""

import pytest

from dovo.core.doctor.checks.agent_setup import AgentSetupCheck
from dovo.core.doctor.checks.config_schema import ConfigSchemaCheck
from dovo.core.doctor.checks.env_binaries import EnvBinariesCheck
from dovo.core.doctor.checks.filesystem_writable import FilesystemWritableCheck
from dovo.core.doctor.checks.git_repo import GitRepoCheck
from dovo.core.doctor.checks.worktree_refs import WorktreeRefsCheck
from dovo.core.doctor.exceptions import CheckRegistrationError
from dovo.core.doctor.models import (
    CheckCategory,
    CheckStatus,
    DiagnosticCheckResult,
    DoctorContext,
)
from dovo.core.doctor.services.registry import CheckRegistry, get_default_registry


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

    def execute(self, context: DoctorContext) -> DiagnosticCheckResult:
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

    def test_register_check_adds_to_registry(self) -> None:
        """[tier-1/unit] CheckRegistry.register: registering a check makes it retrievable by check_id via get()."""
        registry = CheckRegistry()
        check = DummyCheck(check_id="git.repo", name="Git Repo Check", category=CheckCategory.GIT)

        registry.register(check)

        retrieved = registry.get("git.repo")
        assert retrieved is check

    def test_register_duplicate_raises_check_registration_error(self) -> None:
        """[tier-1/unit] CheckRegistry.register: registering a duplicate check_id raises CheckRegistrationError without mutating registry."""
        registry = CheckRegistry()
        check1 = DummyCheck(check_id="duplicate.id", name="First", category=CheckCategory.GIT)
        check2 = DummyCheck(check_id="duplicate.id", name="Second", category=CheckCategory.CONFIG)

        registry.register(check1)

        with pytest.raises(CheckRegistrationError) as exc_info:
            registry.register(check2)

        assert "duplicate.id" in str(exc_info.value)
        assert registry.get("duplicate.id") is check1

    def test_get_missing_check_returns_none(self) -> None:
        """[tier-1/unit] CheckRegistry.get: requesting unregistered check_id returns None."""
        registry = CheckRegistry()
        assert registry.get("nonexistent.check") is None

    def test_list_by_category_returns_matching_checks(self) -> None:
        """[tier-1/unit] CheckRegistry.list_by_category: returns only checks whose category equals the specified CheckCategory."""
        registry = CheckRegistry()
        git_check1 = DummyCheck(check_id="git.repo", category=CheckCategory.GIT)
        git_check2 = DummyCheck(check_id="git.status", category=CheckCategory.GIT)
        config_check = DummyCheck(check_id="config.schema", category=CheckCategory.CONFIG)

        registry.register(git_check1)
        registry.register(git_check2)
        registry.register(config_check)

        git_checks = registry.list_by_category(CheckCategory.GIT)
        assert git_checks == [git_check1, git_check2]

        config_checks = registry.list_by_category(CheckCategory.CONFIG)
        assert config_checks == [config_check]

        env_checks = registry.list_by_category(CheckCategory.ENVIRONMENT)
        assert env_checks == []

    def test_all_returns_all_registered_checks(self) -> None:
        """[tier-1/unit] CheckRegistry.all: returns list of all registered DiagnosticCheck instances."""
        registry = CheckRegistry()
        assert registry.all() == []

        check1 = DummyCheck(check_id="one", category=CheckCategory.GIT)
        check2 = DummyCheck(check_id="two", category=CheckCategory.AGENT)

        registry.register(check1)
        registry.register(check2)

        assert registry.all() == [check1, check2]


class DefaultRegistryTests:
    """Unit tests for get_default_registry factory wiring."""

    def test_get_default_registry_registers_all_six_builtin_checks(self) -> None:
        """[tier-1/unit] get_default_registry: returns a registry with exactly the 6 built-in checks by check_id."""
        registry = get_default_registry()

        assert len(registry.all()) == 6
        assert isinstance(registry.get("git.repo"), GitRepoCheck)
        assert isinstance(registry.get("config.schema"), ConfigSchemaCheck)
        assert isinstance(registry.get("filesystem.writable"), FilesystemWritableCheck)
        assert isinstance(registry.get("worktree.refs"), WorktreeRefsCheck)
        assert isinstance(registry.get("env.binaries"), EnvBinariesCheck)
        assert isinstance(registry.get("agent.setup"), AgentSetupCheck)
