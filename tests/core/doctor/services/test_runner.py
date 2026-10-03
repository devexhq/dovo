"""Unit tests for dovo.core.doctor.services.runner."""

from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.common.filesystem import WorkspacePaths
from dovo.core.config.models import (
    DoctorConfig,
    DovoConfig,
    ProjectConfig,
)
from dovo.core.doctor.models import (
    CheckCategory,
    CheckStatus,
    DiagnosticCheckResult,
    DoctorContext,
    RemediationType,
)
from dovo.core.doctor.services.registry import CheckRegistry
from dovo.core.doctor.services.runner import (
    DiagnosticRunner,
    execute_single_check,
)

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]


class MockCheck:
    """Protocol-conforming test double for DiagnosticCheck."""

    def __init__(
        self,
        check_id: str,
        name: str = "Mock Check",
        category: CheckCategory = CheckCategory.GIT,
        result_status: CheckStatus = CheckStatus.OK,
        should_crash: bool = False,
        crash_message: str = "Unexpected failure",
        duration_ms: float = 0.0,
    ) -> None:
        self.check_id = check_id
        self.name = name
        self.category = category
        self.result_status = result_status
        self.should_crash = should_crash
        self.crash_message = crash_message
        self.duration_ms = duration_ms
        self.executed: bool = False

    def execute(self, context: DoctorContext) -> DiagnosticCheckResult:
        self.executed = True
        if self.should_crash:
            raise RuntimeError(self.crash_message)
        return DiagnosticCheckResult(
            check_id=self.check_id,
            name=self.name,
            category=self.category,
            status=self.result_status,
            message=f"Executed {self.check_id}",
            details={"ran": True},
            duration_ms=self.duration_ms,
            error_code=None,
            errors=[],
            warnings=[],
            fixes=[],
        )


class DiagnosticRunnerFilteringTests:
    """Unit tests for DiagnosticRunner category filtering contracts."""

    def test_category_filter_omits_non_matching_checks(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] DiagnosticRunner.run_checks: category filter omits checks belonging to other categories from DoctorReport.checks."""
        registry = CheckRegistry()
        git_check = MockCheck(check_id="git.repo", category=CheckCategory.GIT)
        config_check = MockCheck(check_id="config.schema", category=CheckCategory.CONFIG)
        agent_check = MockCheck(check_id="agent.setup", category=CheckCategory.AGENT)

        registry.register(git_check)
        registry.register(config_check)
        registry.register(agent_check)

        runner = DiagnosticRunner(registry=registry)
        context = DoctorContext(cwd=tmp_path, config=None, paths=workspace_paths_factory(tmp_path, None))

        report = runner.run_checks(context, categories=[CheckCategory.GIT, CheckCategory.AGENT])

        assert git_check.executed is True
        assert agent_check.executed is True
        assert config_check.executed is False

        assert report.workspace_root == tmp_path
        assert [c.check_id for c in report.checks] == ["git.repo", "agent.setup"]
        assert all(c.status == CheckStatus.OK for c in report.checks)

    @pytest.mark.parametrize(
        "categories",
        [
            pytest.param(None, id="none_categories"),
            pytest.param([], id="empty_list_categories"),
        ],
    )
    def test_none_or_empty_category_runs_all_checks(
        self,
        tmp_path: Path,
        categories: list[CheckCategory] | None,
        workspace_paths_factory: WorkspacePathsFactory,
    ) -> None:
        """[tier-1/unit] DiagnosticRunner.run_checks: passing categories=None or categories=[] considers all registered checks."""
        registry = CheckRegistry()
        check1 = MockCheck(check_id="check.one", category=CheckCategory.GIT)
        check2 = MockCheck(check_id="check.two", category=CheckCategory.ENVIRONMENT)

        registry.register(check1)
        registry.register(check2)

        runner = DiagnosticRunner(registry=registry)
        context = DoctorContext(cwd=tmp_path, config=None, paths=workspace_paths_factory(tmp_path, None))

        report = runner.run_checks(context, categories=categories)

        assert check1.executed is True
        assert check2.executed is True
        assert report.workspace_root == tmp_path
        assert [c.check_id for c in report.checks] == ["check.one", "check.two"]
        assert all(c.status == CheckStatus.OK for c in report.checks)


class DiagnosticRunnerConfigToggleTests:
    """Unit tests for DoctorConfig toggle skipping in DiagnosticRunner."""

    @pytest.mark.parametrize(
        ("toggle_attr", "check_id", "category"),
        [
            pytest.param("check_git", "git.repo", CheckCategory.GIT, id="git_repo"),
            pytest.param(
                "check_paths_writable",
                "filesystem.writable",
                CheckCategory.FILESYSTEM,
                id="filesystem_writable",
            ),
            pytest.param(
                "check_config_schema",
                "config.schema",
                CheckCategory.CONFIG,
                id="config_schema",
            ),
            pytest.param(
                "check_stale_worktrees",
                "sandbox.refs",
                CheckCategory.SANDBOX,
                id="sandbox_refs",
            ),
            pytest.param(
                "check_required_binaries",
                "env.binaries",
                CheckCategory.ENVIRONMENT,
                id="env_binaries",
            ),
        ],
    )
    def test_config_toggle_false_marks_check_skipped(
        self,
        tmp_path: Path,
        toggle_attr: str,
        check_id: str,
        category: CheckCategory,
        workspace_paths_factory: WorkspacePathsFactory,
    ) -> None:
        """[tier-1/unit] DiagnosticRunner.run_checks: config.doctor.<toggle>=False produces status SKIPPED and duration 0.0."""
        registry = CheckRegistry()
        check = MockCheck(check_id=check_id, category=category)
        registry.register(check)

        doctor_config = DoctorConfig(**{toggle_attr: False})
        config = DovoConfig(
            version=1,
            project=ProjectConfig(name="test"),
            doctor=doctor_config,
        )

        runner = DiagnosticRunner(registry=registry)
        context = DoctorContext(cwd=tmp_path, config=config, paths=workspace_paths_factory(tmp_path, None))

        report = runner.run_checks(context)

        assert check.executed is False
        assert len(report.checks) == 1
        res = report.checks[0]
        assert res.check_id == check_id
        assert res.name == check.name
        assert res.category == category
        assert res.status == CheckStatus.SKIPPED
        assert res.message == f"Check '{check_id}' skipped by configuration."

    def test_config_none_does_not_skip_checks(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] DiagnosticRunner.run_checks: when context.config is None, config toggle skipping is bypassed and checks execute."""
        registry = CheckRegistry()
        check = MockCheck(check_id="git.repo", category=CheckCategory.GIT)
        registry.register(check)

        runner = DiagnosticRunner(registry=registry)
        context = DoctorContext(cwd=tmp_path, config=None, paths=workspace_paths_factory(tmp_path, None))

        report = runner.run_checks(context)

        assert check.executed is True
        assert report.checks[0].status == CheckStatus.OK


class DiagnosticRunnerContainmentTests:
    """Unit tests for DiagnosticRunner exception containment contracts."""

    def test_unhandled_exception_recorded_as_failed_with_doctor_check_crash(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] execute_single_check: check raising unexpected RuntimeError returns status FAILED with error_code DOCTOR_CHECK_CRASH and measured duration."""
        check = MockCheck(
            check_id="crashing.check",
            name="Crashing Check",
            category=CheckCategory.SANDBOX,
            should_crash=True,
            crash_message="Simulated crash in sandbox inspection",
        )
        context = DoctorContext(cwd=tmp_path, config=None, paths=workspace_paths_factory(tmp_path, None))

        result = execute_single_check(check, context)

        assert result.check_id == "crashing.check"
        assert result.name == "Crashing Check"
        assert result.category == CheckCategory.SANDBOX
        assert result.status == CheckStatus.FAILED
        assert result.error_code == "DOCTOR_CHECK_CRASH"
        assert "Simulated crash in sandbox inspection" in result.message
        assert result.details == {
            "exception": "Simulated crash in sandbox inspection",
            "type": "RuntimeError",
        }
        assert len(result.remediations) == 1
        assert result.remediations[0].code == "DOCTOR_CHECK_CRASH"
        assert result.remediations[0].action_type == RemediationType.MANUAL

    def test_crashing_check_does_not_halt_subsequent_checks(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] DiagnosticRunner.run_checks: first check raising exception does not prevent subsequent checks from executing and reporting results."""
        registry = CheckRegistry()
        crash_check = MockCheck(
            check_id="check.crash",
            category=CheckCategory.GIT,
            should_crash=True,
            crash_message="crash 1",
        )
        healthy_check = MockCheck(
            check_id="check.healthy",
            category=CheckCategory.CONFIG,
            should_crash=False,
        )

        registry.register(crash_check)
        registry.register(healthy_check)

        runner = DiagnosticRunner(registry=registry)
        context = DoctorContext(cwd=tmp_path, config=None, paths=workspace_paths_factory(tmp_path, None))

        report = runner.run_checks(context)

        assert crash_check.executed is True
        assert healthy_check.executed is True
        assert report.ok is False
        assert [c.check_id for c in report.checks] == ["check.crash", "check.healthy"]
        assert report.checks[0].status == CheckStatus.FAILED
        assert report.checks[0].error_code == "DOCTOR_CHECK_CRASH"
        assert report.checks[1].status == CheckStatus.OK


class DiagnosticRunnerMetricsTests:
    """Unit tests for DiagnosticRunner execution duration metrics."""

    def test_total_duration_ms_sums_check_execution_and_overhead(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        workspace_paths_factory: WorkspacePathsFactory,
    ) -> None:
        """[tier-1/unit] DiagnosticRunner.run_checks: total_duration_ms on DoctorReport captures check execution elapsed time and runner overhead."""
        registry = CheckRegistry()
        check1 = MockCheck(check_id="c1", category=CheckCategory.GIT)
        check2 = MockCheck(check_id="c2", category=CheckCategory.FILESYSTEM)

        registry.register(check1)
        registry.register(check2)

        # Sequence of 6 perf_counter ticks:
        # 1. run_checks start:          10.0s
        # 2. check1 start:              11.0s (1000ms overhead before check1)
        # 3. check1 end:                13.0s (2000ms execution time)
        # 4. check2 start:              14.0s (1000ms overhead between checks)
        # 5. check2 end:                17.0s (3000ms execution time)
        # 6. run_checks finish:         20.0s (3000ms overhead after checks)
        tick_sequence = iter([10.0, 11.0, 13.0, 14.0, 17.0, 20.0])
        monkeypatch.setattr(
            "dovo.core.doctor.services.runner.time.perf_counter",
            lambda: next(tick_sequence),
        )

        runner = DiagnosticRunner(registry=registry)
        context = DoctorContext(cwd=tmp_path, config=None, paths=workspace_paths_factory(tmp_path, None))

        report = runner.run_checks(context)

        assert report.checks[0].duration_ms == 2000.0
        assert report.checks[1].duration_ms == 3000.0
        assert report.total_duration_ms == 10000.0
        assert [c.check_id for c in report.checks] == ["c1", "c2"]
        assert report.workspace_root == tmp_path
