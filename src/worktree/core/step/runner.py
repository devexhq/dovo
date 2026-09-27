"""Step execution runner and outcome models."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import IO, Any

from worktree.common.models import FailurePolicy
from worktree.common.process import (
    get_isolated_process_kwargs,
    process_registry,
    terminate_process_tree,
)
from worktree.core.agents.factory import get_agent_adapter
from worktree.core.inputs.services.interpolate import interpolate_step_fields
from worktree.core.step.assertions import evaluate_assertions
from worktree.core.step.models import (
    ExecutionMetadata,
    PreviousStepMetadata,
    StepDefinition,
    StepDispatchOutcome,
    StepExecutionContext,
    StepResult,
    StepType,
)
from worktree.core.step.services.metadata import (
    build_execution_metadata,
    metadata_to_env,
    resolve_step_log_paths,
    resolve_step_temp_paths,
)

from .step import Step


def _failed_dispatch(
    error_message: str,
    *,
    exit_code: int = 1,
    stdout: str = "",
    stderr: str = "",
) -> StepDispatchOutcome:
    """Build a failed StepDispatchOutcome with error message and status."""
    return StepDispatchOutcome(
        status="failed",
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        error_message=error_message,
    )


def _int_from_context_or_default(context: dict[str, Any], key: str, default: int) -> int:
    """Extract an integer field from execution context dictionary or return default."""
    if default != 1 or key not in context:
        return default
    return int(context[key])


_HEREDOC_START_RE = re.compile(r"^([^=<\s]+)<<(.+)$")


def _consume_heredoc_block(lines: list[str], start: int, delimiter: str) -> tuple[int, str | None, bool]:
    """Consume raw lines from start until the delimiter line; return (next_index, joined_value, unterminated)."""
    body: list[str] = []
    i = start
    while i < len(lines):
        if lines[i] == delimiter:
            return i + 1, "\n".join(body), False
        body.append(lines[i])
        i += 1
    return i, None, True


def _parse_step_output_line(stripped: str) -> tuple[str, str] | None:
    """Parse a plain KEY=VALUE line, or return None when malformed (missing '=')."""
    if "=" not in stripped:
        return None
    key, _, value = stripped.partition("=")
    return key, value


def _handle_heredoc_line(
    lines: list[str],
    i: int,
    heredoc_match: re.Match[str],
    outputs: dict[str, str],
    warnings: list[str],
) -> int:
    """Consume a heredoc block starting at i, recording its value or an unterminated-block warning."""
    key, delimiter = heredoc_match.group(1), heredoc_match.group(2)
    next_i, value, unterminated = _consume_heredoc_block(lines, i + 1, delimiter)
    if unterminated:
        warnings.append(f"Malformed step output heredoc, missing terminator {delimiter!r} for key {key!r}")
    else:
        outputs[key] = value or ""
    return next_i


def _process_step_output_line(lines: list[str], i: int, outputs: dict[str, str], warnings: list[str]) -> int:
    """Process one output-file line (or heredoc block) starting at i; return the next index."""
    stripped = lines[i].strip()
    if not stripped or stripped.startswith("#"):
        return i + 1
    heredoc_match = _HEREDOC_START_RE.match(stripped)
    if heredoc_match:
        return _handle_heredoc_line(lines, i, heredoc_match, outputs, warnings)
    parsed = _parse_step_output_line(stripped)
    if parsed is None:
        warnings.append(f"Malformed step output line, missing '=': {stripped!r}")
        return i + 1
    key, value = parsed
    outputs[key] = value
    return i + 1


class StepExecution:
    """Synchronous executor for a single StepDefinition within a sandbox directory."""

    def __init__(self, metadata: StepExecutionContext) -> None:
        self.step: Step = Step(instance=metadata.step)
        self.sandbox_path = metadata.sandbox_path.resolve()
        self.context = metadata.context or {}
        self.on_output = metadata.on_output
        self.step_index = _int_from_context_or_default(self.context, "step_index", metadata.step_index)
        self.initial_attempt = _int_from_context_or_default(self.context, "initial_attempt", metadata.initial_attempt)
        self.iteration_index = _int_from_context_or_default(self.context, "iteration_index", metadata.iteration_index)
        self.identity = metadata.identity or self.context.get("identity")
        raw_steps = metadata.steps or self.context.get("steps")
        self.steps: Sequence[PreviousStepMetadata] = list(raw_steps) if raw_steps else []
        self.previous_step = (
            metadata.previous_step or self.context.get("previous_step") or (self.steps[-1] if self.steps else None)
        )
        self.session_tmp_dir = metadata.session_tmp_dir
        self.session_log_dir = metadata.session_log_dir
        self.save_attempt_logs = metadata.save_attempt_logs
        self.loop_iteration = metadata.loop_iteration
        self.log_warnings: list[str] = []
        self.step_scratch_dir: Path | None = None
        self.output_file: Path | None = None
        self.max_attempts = 1
        self._uninterpolated_step: Step = Step(instance=metadata.step)

    def run(self) -> StepResult:
        """Execute the step definition within sandbox_path and return its StepResult."""
        if not self.sandbox_path.exists() or not self.sandbox_path.is_dir():
            outcome = _failed_dispatch(f"Sandbox path '{self.sandbox_path}' does not exist or is not a directory.")
            return _step_result(self.step.instance.id, outcome, 0.0)

        if not self._prepare():
            outcome = _failed_dispatch(f"Could not resolve step '{self.step.instance.id}'.")
            return _step_result(self.step.instance.id, outcome, 0.0)

        start_time = time.monotonic()
        outcome = self._run_attempts()
        duration = time.monotonic() - start_time
        outputs, output_warnings = self._collect_step_outputs()
        output_warnings = [*self.log_warnings, *output_warnings]

        if outcome.status == "completed":
            return _step_result(
                self.step.instance.id,
                outcome,
                duration,
                status="completed",
                exit_code=0,
                outputs=outputs,
                warnings=output_warnings,
            )
        return self._finalize_failure(outcome, duration, outputs=outputs, warnings=output_warnings)

    def _prepare(self) -> bool:
        """Resolve shorthand aliases and compute retry budget."""
        if self.step.instance.uses is not None or self.step.instance.run is not None:
            resolved = self.step.resolve(path=self.sandbox_path)
            if resolved is None:
                return False
            self.step = Step(instance=resolved)
        self._uninterpolated_step = Step(instance=self.step.instance)
        self.max_attempts = (
            self.step.instance.on_failure.max_retries
            if self.step.instance.on_failure.action == FailurePolicy.RETRY
            else 1
        )
        if self.session_tmp_dir is not None:
            self.step_scratch_dir, self.output_file = resolve_step_temp_paths(
                self.session_tmp_dir, self.step.instance.id
            )
        return True

    def _run_attempts(self) -> StepDispatchOutcome:
        """Dispatch primitive up to max_attempts times, sleeping backoff_ms between failures."""
        backoff_ms = self._uninterpolated_step.instance.on_failure.backoff_ms
        outcome = _failed_dispatch("Step did not run.")
        for attempt_offset in range(self.max_attempts):
            attempt = self.initial_attempt + attempt_offset
            metadata = build_execution_metadata(
                self._uninterpolated_step.instance,
                step_index=self.step_index,
                attempt=attempt,
                iteration_index=self.iteration_index,
                identity=self.identity,
                previous_step=self.previous_step,
                steps=self.steps,
                session_tmp_dir=self.session_tmp_dir,
            )
            inputs = self.context.get("inputs")
            inputs_dict = inputs if isinstance(inputs, dict) else None
            interpolated_step_definition = interpolate_step_fields(
                self._uninterpolated_step.instance,
                inputs=inputs_dict,
                metadata=metadata,
            )
            # Replace the step
            self.step = Step(instance=interpolated_step_definition)
            self._reset_step_output_file()
            outcome = self._dispatch_primitive(metadata)
            outcome = outcome.model_copy(update={"attempts": attempt})
            outcome = self._apply_assertions(outcome)
            if outcome.status == "completed":
                return outcome
            if attempt_offset < self.max_attempts - 1 and backoff_ms > 0:
                time.sleep(backoff_ms / 1000)
        return outcome

    def _dispatch_primitive(self, metadata: ExecutionMetadata) -> StepDispatchOutcome:
        """Run the step's primitive type once and return its dispatch outcome."""
        if self.step.instance.type == StepType.COMMAND:
            return self._execute_command(metadata)
        if self.step.instance.type == StepType.SCRIPT:
            return self._execute_script(metadata)
        if self.step.instance.type == StepType.AGENT:
            return self._execute_agent()
        return _failed_dispatch(f"Unsupported step primitive type '{self.step.instance.type}'.")

    def _execute_command(self, metadata: ExecutionMetadata) -> StepDispatchOutcome:
        """Execute a COMMAND step inside sandbox_path."""
        if not self.step.instance.command:
            return _failed_dispatch("Command step has no command string defined.")
        return self._run_process(
            self.step.instance.command,
            shell=True,
            timeout_seconds=self.step.instance.timeout_seconds,
            failure_label="Command",
            step_kind="Command",
            metadata=metadata,
        )

    def _execute_script(self, metadata: ExecutionMetadata) -> StepDispatchOutcome:
        """Execute a SCRIPT step inside sandbox_path."""
        if not self.step.instance.script_path:
            return _failed_dispatch("Script step has no script_path defined.")

        script_file = self.sandbox_path / self.step.instance.script_path
        if not script_file.exists() or not script_file.is_file():
            return _failed_dispatch(f"Script file not found at '{self.step.instance.script_path}'.")

        cmd, shell = _resolve_script_invocation(script_file)
        return self._run_process(
            cmd,
            shell=shell,
            timeout_seconds=self.step.instance.timeout_seconds,
            failure_label="Script",
            step_kind="Script",
            metadata=metadata,
        )

    def _execute_agent(self) -> StepDispatchOutcome:
        """Execute an AGENT step inside sandbox_path."""
        provider = self.context.get("agent") or "local"
        try:
            _ = get_agent_adapter(provider)
            stdout = f"Agent prompt executed with tools: {self.step.instance.tools}"
            if self.on_output is not None:
                try:
                    self.on_output("stdout", stdout)
                except Exception as exc:
                    return _failed_dispatch(f"Agent output callback error: {exc}", stdout=stdout)
            return StepDispatchOutcome(status="completed", exit_code=0, stdout=stdout, stderr="")
        except Exception as exc:
            return _failed_dispatch(f"Agent provider error: {exc}")

    def _build_process_env(self, metadata: ExecutionMetadata) -> dict[str, str]:
        """Merge environment variables: explicit step env > WT_* metadata > ambient env."""
        process_env = os.environ.copy()
        process_env.update(metadata_to_env(metadata))
        process_env.update(self.step.instance.env)
        return process_env

    def _dispatch_pipe_line(
        self,
        line: str,
        stream_name: str,
        collected_lines: list[str],
        collected_errors: list[str],
    ) -> None:
        """Append streamed line and forward it to on_output callback if present."""
        collected_lines.append(line)
        if self.on_output is not None:
            try:
                self.on_output(stream_name, line)
            except Exception as exc:
                collected_errors.append(f"Output callback error on {stream_name}: {exc}")

    def _append_log_line(self, log_file: IO[str] | None, line: str) -> IO[str] | None:
        """Append and flush one line to the attempt log; on OSError warn and return None to stop logging."""
        if log_file is None:
            return None
        try:
            log_file.write(line)
            log_file.flush()
        except OSError as exc:
            self.log_warnings.append(f"Failed writing attempt log '{log_file.name}': {exc}")
            try:
                log_file.close()
            except OSError:
                # Already warned above: closing re-flushes the same unwritable buffer.
                pass
            return None
        return log_file

    def _stream_pipe(
        self,
        pipe: IO[str] | None,
        stream_name: str,
        collected_lines: list[str],
        collected_errors: list[str],
        log_file: IO[str] | None,
    ) -> None:
        """Read lines from pipe, accumulate them, invoke on_output callback, and append them to the attempt log.

        The streaming thread owns log_file and closes it, so a thread outliving its join never writes to a closed handle.
        """
        if pipe is None:
            self._close_log_file(log_file)
            return
        try:
            for line in iter(pipe.readline, ""):
                self._dispatch_pipe_line(line, stream_name, collected_lines, collected_errors)
                log_file = self._append_log_line(log_file, line)
        except Exception as exc:
            collected_errors.append(f"Failed reading {stream_name} stream: {exc}")
        finally:
            try:
                pipe.close()
            except Exception:
                # Best-effort cleanup: closing stream pipe during termination.
                pass
            self._close_log_file(log_file)

    def _terminate_process_tree(
        self,
        proc: subprocess.Popen[str],
        *,
        pgid: int | None = None,
        grace_seconds: float = 2.0,
    ) -> None:
        """Terminate or kill a subprocess and its child process tree."""
        terminate_process_tree(proc, grace_seconds=grace_seconds, pgid=pgid)

    def _open_log_file(self, path: Path | None) -> IO[str] | None:
        """Open an attempt log file for writing, or warn and return None when it cannot be opened."""
        if path is None:
            return None
        try:
            return open(path, "w", encoding="utf-8")
        except OSError as exc:
            self.log_warnings.append(f"Failed opening attempt log '{path}': {exc}")
            return None

    def _open_attempt_logs(self, metadata: ExecutionMetadata) -> tuple[IO[str] | None, IO[str] | None]:
        """Open this attempt's stdout/stderr log files when attempt logging is enabled."""
        stdout_log_path, stderr_log_path = (
            resolve_step_log_paths(
                self.session_log_dir,
                step_index=metadata.step.index,
                step_id=metadata.step.id,
                attempt=metadata.step.attempt,
                iteration=self.loop_iteration,
            )
            if self.session_log_dir is not None and self.save_attempt_logs
            else (None, None)
        )
        return self._open_log_file(stdout_log_path), self._open_log_file(stderr_log_path)

    def _close_log_file(self, log_file: IO[str] | None) -> None:
        """Best-effort close of one attempt log file handle."""
        if log_file is None:
            return
        try:
            log_file.close()
        except OSError as exc:
            self.log_warnings.append(f"Failed closing attempt log '{log_file.name}': {exc}")

    def _run_process(
        self,
        cmd: str | list[str],
        *,
        shell: bool,
        timeout_seconds: int | float | None,
        failure_label: str,
        step_kind: str,
        metadata: ExecutionMetadata,
    ) -> StepDispatchOutcome:
        """Spawn subprocess, stream standard output/error, and collect dispatch outcome."""
        env = self._build_process_env(metadata)
        isolation_kwargs = get_isolated_process_kwargs()
        stdout_log, stderr_log = self._open_attempt_logs(metadata)
        try:
            proc = subprocess.Popen(
                cmd,
                shell=shell,
                cwd=self.sandbox_path,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
                **isolation_kwargs,
            )
        except Exception as exc:
            self._close_log_file(stdout_log)
            self._close_log_file(stderr_log)
            return _failed_dispatch(f"{failure_label} execution error: {exc}")

        pgid = proc.pid
        process_registry.register(proc, pgid=pgid)

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        pipe_errors: list[str] = []

        stdout_thread = threading.Thread(
            target=self._stream_pipe,
            args=(proc.stdout, "stdout", stdout_lines, pipe_errors, stdout_log),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=self._stream_pipe,
            args=(proc.stderr, "stderr", stderr_lines, pipe_errors, stderr_log),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        timed_out = False
        exit_code = 0
        try:
            try:
                exit_code = proc.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._terminate_process_tree(proc, pgid=pgid, grace_seconds=2.0)
                try:
                    exit_code = proc.wait(timeout=5)
                except Exception:
                    # Best-effort wait: process tree was killed; exit code defaults to 124.
                    pass
            except BaseException:
                self._terminate_process_tree(proc, pgid=pgid, grace_seconds=0.5)
                raise
        finally:
            process_registry.unregister(proc)
            if proc.poll() is None:
                self._terminate_process_tree(proc, pgid=pgid, grace_seconds=0.5)
            stdout_thread.join(timeout=2)
            stderr_thread.join(timeout=2)

        stdout = "".join(stdout_lines)
        stderr = "".join(stderr_lines)

        if pipe_errors:
            error_details = "; ".join(pipe_errors)
            return _failed_dispatch(
                f"{failure_label} pipe error: {error_details}",
                exit_code=exit_code if exit_code != 0 else 1,
                stdout=stdout,
                stderr=stderr,
            )

        if timed_out:
            return _failed_dispatch(
                f"{step_kind} step execution timed out after {timeout_seconds} seconds.",
                exit_code=124,
                stdout=stdout,
                stderr=stderr,
            )

        if exit_code == 0:
            return StepDispatchOutcome(status="completed", exit_code=0, stdout=stdout, stderr=stderr)
        return _failed_dispatch(
            f"{failure_label} failed with exit code {exit_code}.",
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )

    def _apply_assertions(self, outcome: StepDispatchOutcome) -> StepDispatchOutcome:
        """Downgrade a completed attempt to failed when step.assert_ checks do not pass."""
        if outcome.status != "completed" or self.step.instance.assert_ is None:
            return outcome

        result = evaluate_assertions(
            self.step.instance.assert_,
            exit_code=outcome.exit_code,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            sandbox_path=self.sandbox_path,
        )
        if result.passed:
            return outcome

        return outcome.model_copy(
            update={
                "status": "failed",
                "error_message": _format_assertion_failure(self.step.instance, result.failed_conditions),
            }
        )

    def _reset_step_output_file(self) -> None:
        """Create the step scratch directory and truncate the step output file before dispatch."""
        if self.step_scratch_dir is None or self.output_file is None:
            return
        self.step_scratch_dir.mkdir(parents=True, exist_ok=True)
        self.output_file.write_text("", encoding="utf-8")

    def _collect_step_outputs(self) -> tuple[dict[str, str], list[str]]:
        """Parse KEY=VALUE and KEY<<DELIM heredoc lines from the step output file."""
        if self.output_file is None or not self.output_file.exists():
            return {}, []
        outputs: dict[str, str] = {}
        warnings: list[str] = []
        lines = self.output_file.read_text(encoding="utf-8").splitlines()
        i = 0
        while i < len(lines):
            i = _process_step_output_line(lines, i, outputs, warnings)
        return outputs, warnings

    def _finalize_failure(
        self,
        outcome: StepDispatchOutcome,
        duration: float,
        *,
        outputs: dict[str, str],
        warnings: list[str],
    ) -> StepResult:
        """Apply the on_failure escalation once retries (if any) are exhausted."""
        on_failure = self._uninterpolated_step.instance.on_failure
        escalation = on_failure.on_max_retries if on_failure.action == FailurePolicy.RETRY else on_failure.action

        if escalation == FailurePolicy.CONTINUE:
            return _step_result(
                self.step.instance.id,
                outcome,
                duration,
                status="ignored",
                exit_code=0,
                outputs=outputs,
                warnings=warnings,
            )
        return _step_result(
            self.step.instance.id, outcome, duration, status="failed", outputs=outputs, warnings=warnings
        )


def _resolve_script_invocation(script_file: Path) -> tuple[str | list[str], bool]:
    """Determine command arguments and shell execution mode for a script path."""
    if os.access(script_file, os.X_OK):
        return [str(script_file)], False
    if script_file.suffix == ".py":
        return [sys.executable, str(script_file)], False
    if script_file.suffix in (".sh", ".bash"):
        return ["bash", str(script_file)], False
    return str(script_file), True


def _step_result(
    step_id: str,
    outcome: StepDispatchOutcome,
    duration: float,
    *,
    status: str | None = None,
    exit_code: int | None = None,
    outputs: dict[str, str] | None = None,
    warnings: list[str] | None = None,
) -> StepResult:
    """Convert a StepDispatchOutcome into a finalized StepResult model."""
    return StepResult(
        step_id=step_id,
        status=status if status is not None else outcome.status,
        exit_code=exit_code if exit_code is not None else outcome.exit_code,
        stdout=outcome.stdout,
        stderr=outcome.stderr,
        duration_seconds=duration,
        attempts=outcome.attempts,
        error_message=outcome.error_message,
        outputs=outputs or {},
        warnings=warnings or [],
    )


def _format_assertion_failure(step: StepDefinition, failed_conditions: list[str]) -> str:
    """Build the multi-line diagnostic block for a failed step assertion."""
    step_label = step.name or step.id
    lines = [f"Step '{step_label}' failed assertion checks:"]
    lines.extend(f"  [FAIL] {condition}" for condition in failed_conditions)
    return "\n".join(lines)
