"""Unit tests for dovo.core.diagnostics.diagnostics entrypoint coordinator."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from dovo.common.filesystem import Filesystem
from dovo.core.config import Config
from dovo.core.config.models import (
    ConfigTier,
    DoctorConfig,
    DovoConfig,
    ProjectConfig,
)
from dovo.core.config.serialize import serialize_config
from dovo.core.diagnostics.diagnostics import Diagnostics
from dovo.core.diagnostics.models import (
    CheckCategory,
    CheckStatus,
)
from dovo.core.diagnostics.services.registry import CheckRegistry, get_default_registry
from dovo.core.git.runner import GitRunner
from tests.harness.builders import StubCheck
from tests.harness.diagnostic_log import read_single_event
from tests.harness.workspace_paths import initialized_workspace_paths


class DiagnosticsCoordinatorTests:
    """Unit tests for Diagnostics entrypoint coordinator."""

    def test_run_diagnostics_delegates_to_runner_with_registered_checks(self, tmp_path: Path) -> None:
        doctor = Diagnostics(initialized_workspace_paths(tmp_path), registry=CheckRegistry(), git=GitRunner())
        check = StubCheck(check_id="test.delegation", category=CheckCategory.GIT)
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
        config_dir = tmp_path / ".dovo"
        config_dir.mkdir(parents=True, exist_ok=True)
        config = DovoConfig(
            version=1,
            project=ProjectConfig(name="resolved-project"),
            doctor=DoctorConfig(check_git=False),
        )
        Filesystem.atomic_write_json(config_dir / "config.json", serialize_config(config))

        doctor = Diagnostics(initialized_workspace_paths(tmp_path), registry=CheckRegistry(), git=GitRunner())
        git_check = StubCheck(check_id="git.repo", category=CheckCategory.GIT)
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
        """Skip git.repo when the User tier sets doctor.check_git=False, which the repo config lacks."""
        write_tier_config(ConfigTier.USER, {"doctor": {"check_git": False}})

        config_dir = tmp_path / ".dovo"
        config_dir.mkdir(parents=True, exist_ok=True)
        Filesystem.atomic_write_json(
            config_dir / "config.json", {"version": 1, "project": {"name": "resolved-project"}}
        )

        doctor = Diagnostics(initialized_workspace_paths(tmp_path), registry=CheckRegistry(), git=GitRunner())
        git_check = StubCheck(check_id="git.repo", category=CheckCategory.GIT)
        doctor.registry.register(git_check)

        report = doctor.run_diagnostics(config=None)

        assert report.checks[0].check_id == "git.repo"
        assert report.checks[0].status == CheckStatus.SKIPPED
        assert report.checks[0].message == "Check 'git.repo' skipped by configuration."

    def test_run_diagnostics_uses_explicit_config_when_provided(self, tmp_path: Path) -> None:
        explicit_config = DovoConfig(
            version=1,
            project=ProjectConfig(name="explicit-project"),
            doctor=DoctorConfig(check_git=False),
        )

        doctor = Diagnostics(initialized_workspace_paths(tmp_path), registry=CheckRegistry(), git=GitRunner())
        git_check = StubCheck(check_id="git.repo", category=CheckCategory.GIT)
        doctor.registry.register(git_check)

        report = doctor.run_diagnostics(config=explicit_config)

        assert report.workspace_root == tmp_path.resolve()
        assert len(report.checks) == 1
        assert report.checks[0].check_id == "git.repo"
        assert report.checks[0].status == CheckStatus.SKIPPED

    def test_run_diagnostics_propagates_category_filter(self, tmp_path: Path) -> None:
        doctor = Diagnostics(initialized_workspace_paths(tmp_path), registry=CheckRegistry(), git=GitRunner())
        git_check = StubCheck(check_id="git.repo", category=CheckCategory.GIT)
        config_check = StubCheck(check_id="config.schema", category=CheckCategory.CONFIG)
        doctor.registry.register(git_check)
        doctor.registry.register(config_check)

        report = doctor.run_diagnostics(categories=[CheckCategory.CONFIG])

        assert report.workspace_root == tmp_path.resolve()
        assert len(report.checks) == 1
        assert report.checks[0].check_id == "config.schema"
        assert report.checks[0].status == CheckStatus.OK

    def test_config_load_exception_records_debug_and_runs_with_no_config(
        self, tmp_path: Path, frozen_diagnostic_clock: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Run checks with config None and write one doctor.check debug record when Config.load raises."""

        def _broken(_self: Config) -> None:
            raise RuntimeError("broken")

        monkeypatch.setattr(Config, "load", _broken)
        doctor = Diagnostics(initialized_workspace_paths(tmp_path), registry=CheckRegistry(), git=GitRunner())
        check = StubCheck(check_id="test.config", category=CheckCategory.GIT)
        doctor.registry.register(check)

        report = doctor.run_diagnostics()

        assert [result.status for result in report.checks] == [CheckStatus.OK]
        assert check.captured_context is not None
        assert check.captured_context.config is None
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "debug"
        assert event["operation"] == "doctor.check"
        assert event["context"] == {"path": str(tmp_path.resolve())}


class DiagnosticsDefaultRegistryTests:
    """Unit tests for Diagnostics.__init__ registry default-wiring."""

    def test_init_without_registry_uses_default_registry_with_all_builtin_checks(self, tmp_path: Path) -> None:
        doctor = Diagnostics(initialized_workspace_paths(tmp_path), git=GitRunner())

        assert [(check.check_id, type(check)) for check in doctor.registry.all()] == [
            (check.check_id, type(check)) for check in get_default_registry().all()
        ]

    def test_init_with_explicit_registry_does_not_use_default_registry(self, tmp_path: Path) -> None:
        explicit_registry = CheckRegistry()

        doctor = Diagnostics(initialized_workspace_paths(tmp_path), registry=explicit_registry, git=GitRunner())

        assert doctor.registry is explicit_registry
        assert doctor.registry.all() == []
