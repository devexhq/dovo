"""Contract tests for StepExecution shorthand resolution, context fields and durations."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dovo.common.filesystem.models import WorkspacePaths
from dovo.common.filesystem.services import resolve_global_paths
from dovo.core.artifacts.models import ArtifactUploadResult
from dovo.core.artifacts.store import ArtifactStore
from dovo.core.catalog.definitions import ArtifactPublishSpec, StepDefinition, StepType
from dovo.engine.executors import step_executor
from dovo.engine.executors.models import OutputCallback, StepExecutionContext
from dovo.engine.executors.step_executor import StepExecution
from tests.harness.builders import StepBuilder
from tests.harness.catalog import write_runnable_step
from tests.harness.diagnostic_log import diagnostic_hint, read_single_event


class StepExecutionResolutionTests:
    def test_unresolvable_uses_step_returns_failed_result_with_could_not_resolve_message(self, tmp_path: Path) -> None:
        step = StepDefinition(id="s1", uses="base-step")

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path)).run()

        assert result.status == "failed"
        assert result.error_message == "Could not resolve step 's1'."

    def test_uses_step_failure_fails_step_with_failure_message(
        self, tmp_path: Path, engine_paths: WorkspacePaths
    ) -> None:
        """Return a failed StepResult with error_message == "Catalog item 'missing-step' not found." for uses: missing-step, and add no diagnostic hint."""
        step = StepDefinition(id="s1", uses="missing-step")
        execution = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path, paths=engine_paths))

        result = execution.run()

        assert result.status == "failed"
        assert result.error_message == "Catalog item 'missing-step' not found."
        assert execution.diagnostic_hints == []
        assert not resolve_global_paths().logs_dir.exists()

    def test_uses_step_unexpected_error_appends_hint_to_step_error(
        self,
        tmp_path: Path,
        engine_paths: WorkspacePaths,
        monkeypatch: pytest.MonkeyPatch,
        frozen_diagnostic_clock: Path,
    ) -> None:
        """With StepDefinition.model_validate raising RuntimeError("x") during get, fail the step with the unexpected-error message plus hint, leave diagnostic_hints empty, and write one event with context {step: <ref>}."""
        write_runnable_step(engine_paths.root_dir, key="base-step", definition={"id": "base-step", "run": "true"})
        step = StepDefinition(id="s1", uses="base-step")

        def _explode(cls: type[StepDefinition], obj: object, **kwargs: object) -> StepDefinition:
            raise RuntimeError("x")

        monkeypatch.setattr(StepDefinition, "model_validate", classmethod(_explode))
        execution = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path, paths=engine_paths))

        result = execution.run()

        assert result.status == "failed"
        assert result.error_message == (
            f"Unexpected error during catalog.load_definition: x{diagnostic_hint(frozen_diagnostic_clock)}"
        )
        assert execution.diagnostic_hints == []
        event = read_single_event(frozen_diagnostic_clock)
        assert event["operation"] == "catalog.load_definition"
        assert event["context"] == {"step": "base-step"}
        assert event["exception_type"] == "RuntimeError"

    def test_run_shorthand_step_executes_as_command(self, tmp_path: Path) -> None:
        step = StepDefinition(id="s1", run="echo ok")

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path)).run()

        assert result.status == "completed"
        assert "ok" in result.stdout


class CommandScriptEnvironmentPreservedTests:
    @pytest.mark.parametrize("step_type", [pytest.param("command", id="command"), pytest.param("script", id="script")])
    def test_command_and_script_children_still_inherit_ambient_and_keep_precedence(
        self, step_type: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("UNRELATED", "1")
        monkeypatch.setenv("DOVO_STEP_ID", "ambient-id")
        monkeypatch.setenv("DOVO_STEP_NAME", "ambient-name")
        if step_type == "command":
            builder = StepBuilder.command('echo "$UNRELATED|$DOVO_STEP_ID|$DOVO_STEP_NAME"')
        else:
            (tmp_path / "probe.py").write_text(
                "import os\nprint('|'.join(os.environ[k] for k in ('UNRELATED', 'DOVO_STEP_ID', 'DOVO_STEP_NAME')))\n",
                encoding="utf-8",
            )
            builder = StepBuilder.script("probe.py")
        step = builder.with_id("probe").with_env("DOVO_STEP_NAME", "from-step").build()

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path)).run()

        assert result.status == "completed"
        assert result.stdout.strip() == "1|probe|from-step"


class StepExecutionContextFieldsTests:
    def test_inputs_interpolate_into_command(self, tmp_path: Path) -> None:
        step = StepBuilder.command("echo ${{ inputs.feature }}").with_id("s1").build()

        result = StepExecution(
            StepExecutionContext(step=step, worktree_path=tmp_path, inputs={"feature": "export"})
        ).run()

        assert result.stdout == "export\n"

    def test_missing_input_stays_literal_like_a_run_without_inputs(self, tmp_path: Path) -> None:
        step = StepBuilder.command("echo '${{ inputs.x }}'").with_id("s1").build()

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path)).run()

        assert result.status == "completed"
        assert result.stdout == "${{ inputs.x }}\n"

    def test_iteration_index_follows_loop_iteration(self, tmp_path: Path) -> None:
        step = StepBuilder.command("echo $DOVO_ITERATION_INDEX ${{ iteration.index }}").with_id("s1").build()

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path, loop_iteration=3)).run()

        assert result.stdout == "3 3\n"


def _unread_clock() -> float:
    raise AssertionError("a step failing before dispatch must not read the clock")


class StepExecutionDurationTests:
    def test_completed_step_duration_is_the_attempts_stopwatch_duration(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return a completed StepResult for `run: echo ok` with duration_seconds == 2.5 for readings [10.0, 12.5]."""
        readings = iter([10.0, 12.5])
        monkeypatch.setattr("dovo.common.timing.monotonic", lambda: next(readings))
        step = StepDefinition(id="s1", run="echo ok")

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path)).run()

        assert result.status == "completed"
        assert result.duration_seconds == 2.5

    @pytest.mark.parametrize("failure", ["missing_worktree", "unresolved_step"])
    def test_step_failing_before_dispatch_has_zero_duration(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
    ) -> None:
        """Return a failed StepResult with duration_seconds == 0.0 and read no clock (patched monotonic raises)."""
        monkeypatch.setattr("dovo.common.timing.monotonic", _unread_clock)
        if failure == "missing_worktree":
            context = StepExecutionContext(
                step=StepDefinition(id="s1", run="echo ok"), worktree_path=tmp_path / "missing"
            )
        else:
            context = StepExecutionContext(step=StepDefinition(id="s1", uses="base-step"), worktree_path=tmp_path)

        result = StepExecution(context).run()

        assert result.status == "failed"
        assert result.duration_seconds == 0.0


def _raise_exec_format_error(*_args: object, **_kwargs: object) -> subprocess.Popen[str]:
    """Stand in for subprocess.Popen when the command cannot be executed."""
    raise OSError("exec format error")


class _TimingOutPopen(subprocess.Popen[str]):
    """Popen whose first wait raises TimeoutExpired, standing in for a step that outlives its timeout."""

    _timed_out = False

    def wait(self, timeout: float | None = None) -> int:
        if not self._timed_out:
            self._timed_out = True
            raise subprocess.TimeoutExpired(self.args, timeout or 0)
        return super().wait(timeout)


def _site_step(site: str, monkeypatch: pytest.MonkeyPatch) -> tuple[StepDefinition, OutputCallback | None, str]:
    """Return the step, output callback and expected error message that induce one failure at site."""
    if site == "launch":
        monkeypatch.setattr("dovo.engine.executors.step_executor.subprocess.Popen", _raise_exec_format_error)
        return StepBuilder.command("true").with_id("s1").build(), None, "Command execution error: exec format error"
    if site == "stream-read":
        return (
            StepBuilder.command(r"printf '\377'").with_id("s1").build(),
            None,
            "Command pipe error: Failed reading stdout stream: "
            "'utf-8' codec can't decode byte 0xff in position 0: invalid start byte",
        )
    if site == "output-callback":

        def _crashing(_stream: str, _line: str) -> None:
            raise RuntimeError("observer crashed")

        return (
            StepBuilder.command("echo one").with_id("s1").build(),
            _crashing,
            "Command pipe error: Output callback error on stdout: observer crashed",
        )

    def _broken_handler(_context: object) -> object:
        raise RuntimeError("boom")

    monkeypatch.setitem(step_executor.INTERNAL_COMMAND_HANDLERS, "artifacts.upload", _broken_handler)
    return (
        StepDefinition(id="s1", type=StepType.INTERNAL, command="artifacts.upload"),
        None,
        "Internal command 'artifacts.upload' error: boom",
    )


class StepExecutionDiagnosticLogTests:
    @pytest.mark.parametrize("site", ["launch", "stream-read", "output-callback", "internal-handler"])
    def test_step_failure_records_error_and_hints_warnings_only(
        self, tmp_path: Path, frozen_diagnostic_clock: Path, monkeypatch: pytest.MonkeyPatch, site: str
    ) -> None:
        """Keep the step result unhinted, put exactly one hint in diagnostic_hints and write one engine.step record."""
        step, on_output, expected_error = _site_step(site, monkeypatch)
        execution = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path, on_output=on_output))

        result = execution.run()

        assert (result.status, result.error_message, result.warnings) == ("failed", expected_error, [])
        assert execution.diagnostic_hints == [diagnostic_hint(frozen_diagnostic_clock).lstrip("\n")]
        event = read_single_event(frozen_diagnostic_clock)
        assert (event["level"], event["operation"], event["context"]) == (
            "error",
            "engine.step",
            {"session_id": None, "step_id": "s1", "path": str(tmp_path.resolve())},
        )

    @pytest.mark.parametrize(
        ("command", "popen", "expected_error"),
        [
            pytest.param("exit 1", subprocess.Popen, "Command failed with exit code 1.", id="nonzero"),
            pytest.param("true", _TimingOutPopen, "Command step execution timed out after 1 seconds.", id="timeout"),
        ],
    )
    def test_normal_step_outcomes_write_no_record(
        self,
        tmp_path: Path,
        frozen_diagnostic_clock: Path,
        monkeypatch: pytest.MonkeyPatch,
        command: str,
        popen: type[subprocess.Popen[str]],
        expected_error: str,
    ) -> None:
        """Return today's failed result and leave <DOVO_HOME>/logs absent for a nonzero exit and a timeout."""
        monkeypatch.setattr("dovo.engine.executors.step_executor.subprocess.Popen", popen)
        step = StepBuilder.command(command).with_id("s1").with_timeout(1).build()
        execution = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path))

        result = execution.run()

        assert (result.status, result.error_message, result.warnings) == ("failed", expected_error, [])
        assert execution.diagnostic_hints == []
        assert not frozen_diagnostic_clock.parent.parent.exists()

    def test_publish_failure_hint_reaches_diagnostic_hints_not_step_warnings(
        self,
        tmp_path: Path,
        frozen_diagnostic_clock: Path,
        monkeypatch: pytest.MonkeyPatch,
        artifact_store: ArtifactStore,
    ) -> None:
        """Keep the publish warning on the step result and carry its 'Details logged' line only in diagnostic_hints."""

        def _raising_publish(*_args: object, **_kwargs: object) -> ArtifactUploadResult:
            raise ValueError("boom")

        monkeypatch.setattr("dovo.core.artifacts.artifacts.publish_artifact", _raising_publish)
        (tmp_path / "PLAN.md").write_text("plan", encoding="utf-8")
        step = StepDefinition(
            id="s1",
            type=StepType.COMMAND,
            command="true",
            artifacts=[ArtifactPublishSpec(name="plan", path="PLAN.md", required=False)],
        )
        execution = StepExecution(
            StepExecutionContext(step=step, worktree_path=tmp_path, artifact_store=artifact_store)
        )

        result = execution.run()

        assert (result.status, result.warnings) == ("completed", ["Failed to publish artifact 'plan': boom"])
        assert execution.diagnostic_hints == [diagnostic_hint(frozen_diagnostic_clock).lstrip("\n")]

    def test_launch_failure_unchanged_when_recorder_cannot_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return error_message 'Command execution error: exec format error' and no warnings or hints when <DOVO_HOME>/logs is a regular file."""
        monkeypatch.setattr("dovo.engine.executors.step_executor.subprocess.Popen", _raise_exec_format_error)
        logs_dir = resolve_global_paths().logs_dir
        logs_dir.parent.mkdir(parents=True, exist_ok=True)
        logs_dir.write_text("not a directory", encoding="utf-8")
        step = StepBuilder.command("true").with_id("s1").build()
        execution = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path))

        result = execution.run()

        assert (result.error_message, result.warnings) == ("Command execution error: exec format error", [])
        assert execution.diagnostic_hints == []
