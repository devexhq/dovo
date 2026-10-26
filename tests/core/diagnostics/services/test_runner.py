"""Unit tests for dovo.core.diagnostics.services.runner."""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord
from dovo.common.filesystem import WorkspacePaths
from dovo.common.filesystem.services import resolve_global_paths
from dovo.common.models import Remediation
from dovo.core.config.models import (
    DoctorConfig,
    DovoConfig,
    ProjectConfig,
)
from dovo.core.diagnostics.models import (
    CheckCategory,
    CheckStatus,
    DiagnosticsContext,
)
from dovo.core.diagnostics.services.registry import CheckRegistry
from dovo.core.diagnostics.services.runner import (
    DiagnosticRunner,
    execute_single_check,
)
from dovo.core.git.runner import GitRunner
from tests.harness.builders import StubCheck
from tests.harness.diagnostic_log import diagnostic_record, read_single_event

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]

T0 = "2026-10-19T12:00:00+00:00"


def _patch_clocks(monkeypatch: pytest.MonkeyPatch, ticks: list[float]) -> None:
    """Patch dovo.common.timing.monotonic to return ticks in order and utc_now to return T0."""
    tick_sequence = iter(ticks)
    monkeypatch.setattr("dovo.common.timing.monotonic", lambda: next(tick_sequence))
    monkeypatch.setattr("dovo.common.timing.utc_now", lambda: datetime.fromisoformat(T0))


class DiagnosticRunnerFilteringTests:
    """Unit tests for DiagnosticRunner category filtering contracts."""

    def test_category_filter_omits_non_matching_checks(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        registry = CheckRegistry()
        git_check = StubCheck(check_id="git.repo", category=CheckCategory.GIT)
        config_check = StubCheck(check_id="config.schema", category=CheckCategory.CONFIG)
        agent_check = StubCheck(check_id="agent.setup", category=CheckCategory.AGENT)

        registry.register(git_check)
        registry.register(config_check)
        registry.register(agent_check)

        runner = DiagnosticRunner(registry=registry)
        context = DiagnosticsContext(
            cwd=tmp_path, config=None, paths=workspace_paths_factory(tmp_path, None), git=GitRunner()
        )

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
        registry = CheckRegistry()
        check1 = StubCheck(check_id="check.one", category=CheckCategory.GIT)
        check2 = StubCheck(check_id="check.two", category=CheckCategory.ENVIRONMENT)

        registry.register(check1)
        registry.register(check2)

        runner = DiagnosticRunner(registry=registry)
        context = DiagnosticsContext(
            cwd=tmp_path, config=None, paths=workspace_paths_factory(tmp_path, None), git=GitRunner()
        )

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
                "worktree.refs",
                CheckCategory.WORKTREE,
                id="worktree_refs",
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
        registry = CheckRegistry()
        check = StubCheck(check_id=check_id, category=category)
        registry.register(check)

        doctor_config = DoctorConfig(**{toggle_attr: False})
        config = DovoConfig(
            version=1,
            project=ProjectConfig(name="test"),
            doctor=doctor_config,
        )

        runner = DiagnosticRunner(registry=registry)
        context = DiagnosticsContext(
            cwd=tmp_path, config=config, paths=workspace_paths_factory(tmp_path, None), git=GitRunner()
        )

        report = runner.run_checks(context)

        assert check.executed is False
        assert len(report.checks) == 1
        res = report.checks[0]
        assert res.check_id == check_id
        assert res.name == check.name
        assert res.category == category
        assert res.status == CheckStatus.SKIPPED
        assert res.duration_seconds == 0.0
        assert res.message == f"Check '{check_id}' skipped by configuration."

    def test_config_none_does_not_skip_checks(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        registry = CheckRegistry()
        check = StubCheck(check_id="git.repo", category=CheckCategory.GIT)
        registry.register(check)

        runner = DiagnosticRunner(registry=registry)
        context = DiagnosticsContext(
            cwd=tmp_path, config=None, paths=workspace_paths_factory(tmp_path, None), git=GitRunner()
        )

        report = runner.run_checks(context)

        assert check.executed is True
        assert report.checks[0].status == CheckStatus.OK


class DiagnosticRunnerContainmentTests:
    """Unit tests for DiagnosticRunner exception containment contracts."""

    def test_unhandled_exception_recorded_as_failed_with_doctor_check_crash(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        check = StubCheck(
            check_id="crashing.check",
            name="Crashing Check",
            category=CheckCategory.WORKTREE,
            crash=RuntimeError("Simulated crash in worktree inspection"),
        )
        context = DiagnosticsContext(
            cwd=tmp_path, config=None, paths=workspace_paths_factory(tmp_path, None), git=GitRunner()
        )

        result = execute_single_check(check, context)

        assert result.check_id == "crashing.check"
        assert result.name == "Crashing Check"
        assert result.category == CheckCategory.WORKTREE
        assert result.status == CheckStatus.FAILED
        assert result.error_code == "DOCTOR_CHECK_CRASH"
        assert "Simulated crash in worktree inspection" in result.message
        assert result.details == {
            "exception": "Simulated crash in worktree inspection",
            "type": "RuntimeError",
        }
        assert result.remediations == [
            Remediation(
                message=(
                    "No deterministic remediation is registered for check 'crashing.check' "
                    "(error_code='DOCTOR_CHECK_CRASH'). Review the check message and details to diagnose and resolve "
                    "the issue."
                )
            )
        ]

    def test_crashing_check_records_error_in_diagnostics(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory, frozen_diagnostic_clock: Path
    ) -> None:
        """Keep message and errors[0] without a line, carry the record in diagnostics and write one doctor.check error record naming the check."""
        check = StubCheck(check_id="crashing.check", crash=RuntimeError("boom"))
        context = DiagnosticsContext(
            cwd=tmp_path, config=None, paths=workspace_paths_factory(tmp_path, None), git=GitRunner()
        )

        result = execute_single_check(check, context)

        assert result.message == "Unhandled exception during check execution: boom"
        assert result.errors == ["Unhandled exception in crashing.check: boom"]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        assert result.details == {"exception": "boom", "type": "RuntimeError"}
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == "doctor.check"
        assert event["context"] == {"check": "crashing.check", "path": str(tmp_path)}

    def test_crashing_check_unchanged_when_recorder_cannot_write(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """Return the message and errors[0] with diagnostics [] when <DOVO_HOME>/logs is a regular file."""
        logs_dir = resolve_global_paths().logs_dir
        logs_dir.parent.mkdir(parents=True, exist_ok=True)
        logs_dir.write_text("not a directory", encoding="utf-8")
        check = StubCheck(check_id="crashing.check", crash=RuntimeError("boom"))
        context = DiagnosticsContext(
            cwd=tmp_path, config=None, paths=workspace_paths_factory(tmp_path, None), git=GitRunner()
        )

        result = execute_single_check(check, context)

        assert result.message == "Unhandled exception during check execution: boom"
        assert result.errors == ["Unhandled exception in crashing.check: boom"]
        assert result.diagnostics == []

    def test_crashing_check_does_not_halt_subsequent_checks(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        registry = CheckRegistry()
        crash_check = StubCheck(
            check_id="check.crash",
            category=CheckCategory.GIT,
            crash=RuntimeError("crash 1"),
        )
        healthy_check = StubCheck(
            check_id="check.healthy",
            category=CheckCategory.CONFIG,
        )

        registry.register(crash_check)
        registry.register(healthy_check)

        runner = DiagnosticRunner(registry=registry)
        context = DiagnosticsContext(
            cwd=tmp_path, config=None, paths=workspace_paths_factory(tmp_path, None), git=GitRunner()
        )

        report = runner.run_checks(context)

        assert crash_check.executed is True
        assert healthy_check.executed is True
        assert report.ok is False
        assert [c.check_id for c in report.checks] == ["check.crash", "check.healthy"]
        assert report.checks[0].status == CheckStatus.FAILED
        assert report.checks[0].error_code == "DOCTOR_CHECK_CRASH"
        assert report.checks[1].status == CheckStatus.OK

    def test_report_diagnostics_concatenates_checks_in_order(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """With three checks returning [record_a], [] and [record_b], return a report whose diagnostics == [record_a, record_b]."""
        record_a = DiagnosticRecord(
            event_id="exc_aaaaaaaaaaaa", log_path=tmp_path / "exc_aaaaaaaaaaaa.json", level=DiagnosticLevel.ERROR
        )
        record_b = DiagnosticRecord(
            event_id="exc_bbbbbbbbbbbb", log_path=tmp_path / "exc_bbbbbbbbbbbb.json", level=DiagnosticLevel.ERROR
        )
        registry = CheckRegistry()
        registry.register(StubCheck(check_id="check.a", diagnostics=[record_a]))
        registry.register(StubCheck(check_id="check.none"))
        registry.register(StubCheck(check_id="check.b", diagnostics=[record_b]))
        context = DiagnosticsContext(
            cwd=tmp_path, config=None, paths=workspace_paths_factory(tmp_path, None), git=GitRunner()
        )

        report = DiagnosticRunner(registry=registry).run_checks(context)

        assert report.diagnostics == [record_a, record_b]


class DiagnosticRunnerMetricsTests:
    """Unit tests for DiagnosticRunner execution duration metrics."""

    def test_duration_seconds_measures_each_check_and_report_total(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        workspace_paths_factory: WorkspacePathsFactory,
    ) -> None:
        registry = CheckRegistry()
        check1 = StubCheck(check_id="c1", category=CheckCategory.GIT)
        check2 = StubCheck(check_id="c2", category=CheckCategory.FILESYSTEM)

        registry.register(check1)
        registry.register(check2)

        # Sequence of 6 monotonic ticks:
        # 1. run_checks start:          10.0s
        # 2. check1 start:              11.0s (1s overhead before check1)
        # 3. check1 end:                13.0s (2s execution time)
        # 4. check2 start:              14.0s (1s overhead between checks)
        # 5. check2 end:                17.0s (3s execution time)
        # 6. run_checks finish:         20.0s (3s overhead after checks)
        _patch_clocks(monkeypatch, [10.0, 11.0, 13.0, 14.0, 17.0, 20.0])

        runner = DiagnosticRunner(registry=registry)
        context = DiagnosticsContext(
            cwd=tmp_path, config=None, paths=workspace_paths_factory(tmp_path, None), git=GitRunner()
        )

        report = runner.run_checks(context)

        assert report.checks[0].duration_seconds == 2.0
        assert report.checks[1].duration_seconds == 3.0
        assert report.duration_seconds == 10.0
        assert [c.check_id for c in report.checks] == ["c1", "c2"]
        assert report.workspace_root == tmp_path

    @pytest.mark.parametrize(
        ("check", "ticks", "expected"),
        [
            pytest.param(StubCheck(duration_seconds=99.0), [1.0, 1.5], 0.5, id="self_reported"),
            pytest.param(StubCheck(crash=RuntimeError("boom")), [5.0, 5.25], 0.25, id="crash"),
        ],
    )
    def test_execute_single_check_reports_measured_duration(
        self,
        check: StubCheck,
        ticks: list[float],
        expected: float,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        workspace_paths_factory: WorkspacePathsFactory,
    ) -> None:
        _patch_clocks(monkeypatch, ticks)
        context = DiagnosticsContext(
            cwd=tmp_path, config=None, paths=workspace_paths_factory(tmp_path, None), git=GitRunner()
        )

        result = execute_single_check(check, context)

        assert result.duration_seconds == expected
