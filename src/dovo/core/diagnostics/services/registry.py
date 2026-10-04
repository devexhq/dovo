"""Registry managing diagnostic check definitions."""

from dovo.core.diagnostics.checks.agent_setup import AgentSetupCheck
from dovo.core.diagnostics.checks.config_schema import ConfigSchemaCheck
from dovo.core.diagnostics.checks.env_binaries import EnvBinariesCheck
from dovo.core.diagnostics.checks.filesystem_writable import FilesystemWritableCheck
from dovo.core.diagnostics.checks.git_repo import GitRepoCheck
from dovo.core.diagnostics.checks.worktree_refs import WorktreeRefsCheck
from dovo.core.diagnostics.exceptions import CheckRegistrationError
from dovo.core.diagnostics.models import CheckCategory, DiagnosticCheck


class CheckRegistry:
    """Registry managing diagnostic checks by unique check_id."""

    def __init__(self) -> None:
        self._checks: dict[str, DiagnosticCheck] = {}

    def register(self, check: DiagnosticCheck) -> None:
        """Register a diagnostic check, raising CheckRegistrationError on duplicate check_id."""
        if check.check_id in self._checks:
            raise CheckRegistrationError(f"Check with ID '{check.check_id}' is already registered.")
        self._checks[check.check_id] = check

    def get(self, check_id: str) -> DiagnosticCheck | None:
        """Retrieve a registered check by unique check_id, or None if not registered."""
        return self._checks.get(check_id)

    def list_by_category(self, category: CheckCategory) -> list[DiagnosticCheck]:
        """Return registered checks matching the specified category."""
        return [c for c in self._checks.values() if c.category == category]

    def all(self) -> list[DiagnosticCheck]:
        """Return all registered checks."""
        return list(self._checks.values())


def get_default_registry() -> CheckRegistry:
    """Build and return a CheckRegistry pre-populated with all built-in diagnostic checks."""
    registry = CheckRegistry()
    registry.register(GitRepoCheck())
    registry.register(ConfigSchemaCheck())
    registry.register(FilesystemWritableCheck())
    registry.register(WorktreeRefsCheck())
    registry.register(EnvBinariesCheck())
    registry.register(AgentSetupCheck())
    return registry
