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
)
from dovo.core.diagnostics.services.registry import CheckRegistry, get_default_registry
from tests.harness.builders import StubCheck


class CheckRegistryTests:
    """Unit tests for CheckRegistry lifecycle operations."""

    def test_register_duplicate_raises_check_registration_error(self) -> None:
        registry = CheckRegistry()
        check1 = StubCheck(check_id="duplicate.id", name="First", category=CheckCategory.GIT)
        check2 = StubCheck(check_id="duplicate.id", name="Second", category=CheckCategory.CONFIG)

        registry.register(check1)

        with pytest.raises(CheckRegistrationError) as exc_info:
            registry.register(check2)

        assert "duplicate.id" in str(exc_info.value)
        assert registry.all() == [check1]

    def test_all_returns_all_registered_checks(self) -> None:
        registry = CheckRegistry()
        assert registry.all() == []

        check1 = StubCheck(check_id="one", category=CheckCategory.GIT)
        check2 = StubCheck(check_id="two", category=CheckCategory.AGENT)

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
