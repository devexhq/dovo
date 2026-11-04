"""Contract tests for StepExecution shorthand resolution, context fields and durations."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dovo.common.diagnostic_log import DiagnosticRecord
from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem.models import WorkspacePaths
from dovo.common.filesystem.services import resolve_global_paths
from dovo.common.models import FailurePolicy
from dovo.core.artifacts.models import ArtifactUploadResult
from dovo.core.artifacts.store import ArtifactStore
from dovo.core.catalog.definitions import ArtifactPublishSpec, StepDefinition, StepType
from dovo.engine.executors import step_executor
from dovo.engine.executors.models import OutputCallback, StepExecutionContext
from dovo.engine.executors.step_executor import StepExecution
from tests.harness.builders import StepBuilder
from tests.harness.catalog import write_runnable_step
from tests.harness.diagnostic_log import diagnostic_record, read_single_event


class StepExecutionResolutionTests:
    def test_uses_merge_with_no_valid_step_fails_step_with_resolving_message(
        self, tmp_path: Path, engine_paths: WorkspacePaths
    ) -> None:
        """Fail a uses: step whose merge onto an ask base yields no valid step with the resolving failure's message."""
        write_runnable_step(
            engine_paths.root_dir,
            key="ask-base",
            definition={"id": "ask-base", "type": "ask", "interaction": {"mode": "confirm", "message": "Go?"}},
        )
        step = StepDefinition(id="s1", uses="ask-base")
        sink: list[DiagnosticRecord] = []

        result = StepExecution(
            StepExecutionContext(step=step, worktree_path=tmp_path, paths=engine_paths, diagnostics=sink)
        ).run()

        assert (result.status, result.error_code) == ("failed", ErrorCode.CATALOG_SCHEMA_INVALID)
        assert result.error_message == "Step 's1' must specify one of 'run', 'uses', or 'type'."
        assert sink == []

    def test_uses_step_failure_fails_step_with_failure_message(
        self, tmp_path: Path, engine_paths: WorkspacePaths
    ) -> None:
        """Return a failed StepRecord with the catalog's CATALOG_NOT_FOUND and error_message == "Catalog item 'missing-step' not found." for uses: missing-step, and add no record to the diagnostics list."""
        step = StepDefinition(id="s1", uses="missing-step")
        sink: list[DiagnosticRecord] = []
        execution = StepExecution(
            StepExecutionContext(step=step, worktree_path=tmp_path, paths=engine_paths, diagnostics=sink)
        )

        result = execution.run()

        assert (result.status, result.error_code) == ("failed", ErrorCode.CATALOG_NOT_FOUND)
        assert result.error_message == "Catalog item 'missing-step' not found."
        assert sink == []
        assert not resolve_global_paths().logs_dir.exists()

    def test_uses_step_unexpected_error_records_into_diagnostics(
        self,
        tmp_path: Path,
        engine_paths: WorkspacePaths,
        monkeypatch: pytest.MonkeyPatch,
        frozen_diagnostic_clock: Path,
    ) -> None:
        """With StepDefinition.model_validate raising RuntimeError("x") during get, fail the step with the unexpected-error message alone, leave the diagnostics list == [the engine.step record], and write one event with context {step: <ref>}."""
        write_runnable_step(engine_paths.root_dir, key="base-step", definition={"id": "base-step", "run": "true"})
        step = StepDefinition(id="s1", uses="base-step")

        def _explode(cls: type[StepDefinition], obj: object, **kwargs: object) -> StepDefinition:
            raise RuntimeError("x")

        monkeypatch.setattr(StepDefinition, "model_validate", classmethod(_explode))
        sink: list[DiagnosticRecord] = []
        execution = StepExecution(
            StepExecutionContext(step=step, worktree_path=tmp_path, paths=engine_paths, diagnostics=sink)
        )

        result = execution.run()

        assert (result.status, result.error_code) == ("failed", ErrorCode.INTERNAL_ERROR)
        assert result.error_message == "Unexpected error during engine.step: x"
        assert sink == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["operation"] == "engine.step"
        assert event["context"] == {"step": "base-step"}
        assert event["exception_type"] == "RuntimeError"

    def test_run_shorthand_step_executes_as_command(self, tmp_path: Path) -> None:
        step = StepDefinition(id="s1", run="echo ok")

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path, diagnostics=[])).run()

        assert (result.status, result.error_code) == ("completed", None)
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

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path, diagnostics=[])).run()

        assert result.status == "completed"
        assert result.stdout.strip() == "1|probe|from-step"


class StepExecutionContextFieldsTests:
    def test_inputs_interpolate_into_command(self, tmp_path: Path) -> None:
        step = StepBuilder.command("echo ${{ inputs.feature }}").with_id("s1").build()

        result = StepExecution(
            StepExecutionContext(step=step, worktree_path=tmp_path, inputs={"feature": "export"}, diagnostics=[])
        ).run()

        assert result.stdout == "export\n"

    def test_missing_input_stays_literal_like_a_run_without_inputs(self, tmp_path: Path) -> None:
        step = StepBuilder.command("echo '${{ inputs.x }}'").with_id("s1").build()

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path, diagnostics=[])).run()

        assert result.status == "completed"
        assert result.stdout == "${{ inputs.x }}\n"

    def test_iteration_index_follows_loop_iteration(self, tmp_path: Path) -> None:
        step = StepBuilder.command("echo $DOVO_ITERATION_INDEX ${{ iteration.index }}").with_id("s1").build()

        result = StepExecution(
            StepExecutionContext(step=step, worktree_path=tmp_path, loop_iteration=3, diagnostics=[])
        ).run()

        assert result.stdout == "3 3\n"


def _unread_clock() -> float:
    raise AssertionError("a step failing before dispatch must not read the clock")


class StepExecutionDurationTests:
    def test_completed_step_duration_is_the_attempts_stopwatch_duration(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return a completed StepRecord for `run: echo ok` with duration_seconds == 2.5 for readings [10.0, 12.5]."""
        readings = iter([10.0, 12.5])
        monkeypatch.setattr("dovo.common.timing.monotonic", lambda: next(readings))
        step = StepDefinition(id="s1", run="echo ok")

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path, diagnostics=[])).run()

        assert result.status == "completed"
        assert result.duration_seconds == 2.5

    @pytest.mark.parametrize("failure", ["missing_worktree", "unresolved_step"])
    def test_step_failing_before_dispatch_has_zero_duration(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
    ) -> None:
        """Return a failed StepRecord with duration_seconds == 0.0 and read no clock (patched monotonic raises)."""
        monkeypatch.setattr("dovo.common.timing.monotonic", _unread_clock)
        if failure == "missing_worktree":
            context = StepExecutionContext(
                step=StepDefinition(id="s1", run="echo ok"), worktree_path=tmp_path / "missing", diagnostics=[]
            )
        else:
            context = StepExecutionContext(
                step=StepDefinition(id="s1", uses="base-step"), worktree_path=tmp_path, diagnostics=[]
            )

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


class _SuccessTimesOutPopen(subprocess.Popen[str]):
    """Popen whose wait raises TimeoutExpired once for a process that exits 0, and passes other exits through."""

    _timed_out = False

    def wait(self, timeout: float | None = None) -> int:
        exit_code = super().wait(timeout)
        if exit_code == 0 and not self._timed_out:
            self._timed_out = True
            raise subprocess.TimeoutExpired(self.args, timeout or 0)
        return exit_code


def _site_step(
    site: str, monkeypatch: pytest.MonkeyPatch
) -> tuple[StepDefinition, OutputCallback | None, str, ErrorCode]:
    """Return the step, output callback, expected error message and expected error code of one failure at site."""
    if site == "launch":
        monkeypatch.setattr("dovo.engine.executors.step_executor.subprocess.Popen", _raise_exec_format_error)
        return (
            StepBuilder.command("true").with_id("s1").build(),
            None,
            "Command execution error: exec format error",
            ErrorCode.STEP_LAUNCH_FAILED,
        )
    if site == "stream-read":
        return (
            StepBuilder.command(r"printf '\377'").with_id("s1").build(),
            None,
            "Command pipe error: Failed reading stdout stream: "
            "'utf-8' codec can't decode byte 0xff in position 0: invalid start byte",
            ErrorCode.STEP_OUTPUT_READ_FAILED,
        )
    if site == "output-callback":

        def _crashing(_stream: str, _line: str) -> None:
            raise RuntimeError("observer crashed")

        return (
            StepBuilder.command("echo one").with_id("s1").build(),
            _crashing,
            "Command pipe error: Output callback error on stdout: observer crashed",
            ErrorCode.STEP_OUTPUT_READ_FAILED,
        )

    def _broken_handler(_context: object) -> object:
        raise RuntimeError("boom")

    monkeypatch.setitem(step_executor.INTERNAL_COMMAND_HANDLERS, "artifacts.upload", _broken_handler)
    return (
        StepDefinition(id="s1", type=StepType.INTERNAL, command="artifacts.upload"),
        None,
        "Internal command 'artifacts.upload' error: boom",
        ErrorCode.INTERNAL_ERROR,
    )


class StepExecutionDiagnosticLogTests:
    @pytest.mark.parametrize("site", ["launch", "stream-read", "output-callback", "internal-handler"])
    def test_step_failure_records_error_into_diagnostics_only(
        self, tmp_path: Path, frozen_diagnostic_clock: Path, monkeypatch: pytest.MonkeyPatch, site: str
    ) -> None:
        """Fail with the site's error code, keep the step result free of the line and its warnings empty, and leave the diagnostics list == [the engine.step record]."""
        step, on_output, expected_error, expected_code = _site_step(site, monkeypatch)
        sink: list[DiagnosticRecord] = []
        execution = StepExecution(
            StepExecutionContext(step=step, worktree_path=tmp_path, on_output=on_output, diagnostics=sink)
        )

        result = execution.run()

        assert (result.status, result.error_message, result.warnings) == ("failed", expected_error, [])
        assert result.error_code == expected_code
        assert sink == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert (event["level"], event["operation"], event["context"]) == (
            "error",
            "engine.step",
            {"session_id": None, "step_id": "s1", "path": str(tmp_path.resolve())},
        )

    @pytest.mark.parametrize(
        ("command", "popen", "expected_error", "expected_code"),
        [
            pytest.param(
                "exit 1",
                subprocess.Popen,
                "Command failed with exit code 1.",
                ErrorCode.STEP_COMMAND_FAILED,
                id="nonzero",
            ),
            pytest.param(
                "true",
                _TimingOutPopen,
                "Command step execution timed out after 1 seconds.",
                ErrorCode.STEP_TIMEOUT,
                id="timeout",
            ),
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
        expected_code: ErrorCode,
    ) -> None:
        """Return today's failed result with its error code and leave <DOVO_HOME>/logs absent for a nonzero exit and a timeout."""
        monkeypatch.setattr("dovo.engine.executors.step_executor.subprocess.Popen", popen)
        step = StepBuilder.command(command).with_id("s1").with_timeout(1).build()
        sink: list[DiagnosticRecord] = []
        execution = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path, diagnostics=sink))

        result = execution.run()

        assert (result.status, result.error_message, result.warnings) == ("failed", expected_error, [])
        assert result.error_code == expected_code
        assert sink == []
        assert not frozen_diagnostic_clock.parent.parent.exists()

    def test_publish_failure_record_reaches_diagnostics_not_step_warnings(
        self,
        tmp_path: Path,
        frozen_diagnostic_clock: Path,
        monkeypatch: pytest.MonkeyPatch,
        artifact_store: ArtifactStore,
    ) -> None:
        """Keep the publish warning on the step result and put the artifacts.publish record only in the diagnostics list."""

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
        sink: list[DiagnosticRecord] = []
        execution = StepExecution(
            StepExecutionContext(step=step, worktree_path=tmp_path, artifact_store=artifact_store, diagnostics=sink)
        )

        result = execution.run()

        assert (result.status, result.warnings) == (
            "completed",
            ["Failed to publish artifact 'plan': Unexpected error during artifacts.publish: boom"],
        )
        assert sink == [diagnostic_record(frozen_diagnostic_clock)]

    def test_launch_failure_unchanged_when_recorder_cannot_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return error_message 'Command execution error: exec format error' and no warnings or records when <DOVO_HOME>/logs is a regular file."""
        monkeypatch.setattr("dovo.engine.executors.step_executor.subprocess.Popen", _raise_exec_format_error)
        logs_dir = resolve_global_paths().logs_dir
        logs_dir.parent.mkdir(parents=True, exist_ok=True)
        logs_dir.write_text("not a directory", encoding="utf-8")
        step = StepBuilder.command("true").with_id("s1").build()
        sink: list[DiagnosticRecord] = []
        execution = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path, diagnostics=sink))

        result = execution.run()

        assert (result.error_message, result.warnings) == ("Command execution error: exec format error", [])
        assert sink == []


class StepExecutionContinueTests:
    @pytest.mark.parametrize(
        ("builder", "attempts"),
        [
            pytest.param(StepBuilder.command("exit 3").with_on_failure(FailurePolicy.CONTINUE), 1, id="direct"),
            pytest.param(
                StepBuilder.command("exit 3").with_retry(
                    max_retries=2, backoff_ms=0, on_max_retries=FailurePolicy.CONTINUE
                ),
                2,
                id="retry-exhausted",
            ),
        ],
    )
    def test_continued_failure_keeps_exit_code(self, tmp_path: Path, builder: StepBuilder, attempts: int) -> None:
        """Record a continued `exit 3` as ignored with exit code 3 and STEP_COMMAND_FAILED, directly and after retries are exhausted."""
        step = builder.with_id("s1").build()

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path, diagnostics=[])).run()

        assert (result.status, result.exit_code, result.error_message) == (
            "ignored",
            3,
            "Command failed with exit code 3.",
        )
        assert (result.error_code, result.attempts) == (ErrorCode.STEP_COMMAND_FAILED, attempts)

    def test_timed_out_continued_step_records_124(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Record a continued timeout as ignored with exit code 124 and STEP_TIMEOUT, as a timeout records without continue."""
        monkeypatch.setattr("dovo.engine.executors.step_executor.subprocess.Popen", _TimingOutPopen)
        step = StepBuilder.command("true").with_id("s1").with_timeout(1).with_on_failure(FailurePolicy.CONTINUE).build()

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path, diagnostics=[])).run()

        assert (result.status, result.exit_code, result.error_code) == ("ignored", 124, ErrorCode.STEP_TIMEOUT)


def _definition_step(site: str) -> StepDefinition:
    """Return a step whose definition cannot run at site; model_copy skips the validation that rejects it on load."""
    if site == "command-without-command":
        return StepBuilder.command("true").with_id("s1").build().model_copy(update={"command": None})
    if site == "script-without-path":
        return StepBuilder.script("run.sh").with_id("s1").build().model_copy(update={"script_path": None})
    if site == "internal-without-command":
        return StepDefinition(id="s1", type=StepType.INTERNAL, command="artifacts.upload").model_copy(
            update={"command": None}
        )
    if site == "unknown-internal":
        return StepDefinition(id="s1", type=StepType.INTERNAL, command="artifacts.nonexistent")
    return StepDefinition.model_validate(
        {"id": "s1", "type": "ask", "interaction": {"mode": "confirm", "message": "Go?"}}
    )


class StepExecutionErrorCodeTests:
    @pytest.mark.parametrize(
        ("site", "expected_error"),
        [
            pytest.param("command-without-command", "Command step has no command string defined.", id="command"),
            pytest.param("script-without-path", "Script step has no script_path defined.", id="script"),
            pytest.param("internal-without-command", "Internal step has no command string defined.", id="internal"),
            pytest.param("unknown-internal", "Unknown internal command 'artifacts.nonexistent'.", id="unknown"),
            pytest.param("unsupported-type", "Unsupported step primitive type 'ask'.", id="unsupported"),
        ],
    )
    def test_step_that_cannot_run_as_defined_records_definition_invalid(
        self, tmp_path: Path, site: str, expected_error: str
    ) -> None:
        """Record STEP_DEFINITION_INVALID with the site's message for each step whose definition cannot run."""
        step = _definition_step(site)

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path, diagnostics=[])).run()

        assert (result.status, result.error_code, result.error_message) == (
            "failed",
            ErrorCode.STEP_DEFINITION_INVALID,
            expected_error,
        )

    def test_missing_script_file_records_script_not_found(self, tmp_path: Path) -> None:
        step = StepBuilder.script("missing.sh").with_id("s1").build()

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path, diagnostics=[])).run()

        assert (result.status, result.error_code, result.error_message) == (
            "failed",
            ErrorCode.STEP_SCRIPT_NOT_FOUND,
            "Script file not found at 'missing.sh'.",
        )

    def test_missing_worktree_records_worktree_missing(self, tmp_path: Path) -> None:
        step = StepDefinition(id="s1", run="echo ok")

        result = StepExecution(
            StepExecutionContext(step=step, worktree_path=tmp_path / "missing", diagnostics=[])
        ).run()

        assert (result.status, result.error_code, result.error_message) == (
            "failed",
            ErrorCode.STEP_WORKTREE_MISSING,
            f"Worktree path '{tmp_path / 'missing'}' does not exist or is not a directory.",
        )

    def test_retried_step_records_its_last_attempts_code(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Record STEP_TIMEOUT for a retried step whose first attempt exits 1 and whose last attempt times out."""
        script = tmp_path / "attempt.sh"
        script.write_text('if [ "$DOVO_STEP_ATTEMPT" = 1 ]; then exit 1; fi\n', encoding="utf-8")
        monkeypatch.setattr("dovo.engine.executors.step_executor.subprocess.Popen", _SuccessTimesOutPopen)
        step = (
            StepBuilder.script("attempt.sh")
            .with_id("s1")
            .with_timeout(1)
            .with_retry(max_retries=2, backoff_ms=0, on_max_retries=FailurePolicy.CONTINUE)
            .build()
        )

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path, diagnostics=[])).run()

        assert (result.status, result.attempts, result.error_code) == ("ignored", 2, ErrorCode.STEP_TIMEOUT)
