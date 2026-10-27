"""Step execution runner and outcome models."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO

from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord, error_diagnostics, record_exception
from dovo.common.exceptions import catch_failure
from dovo.common.models import FailurePolicy
from dovo.common.process import (
    get_isolated_process_kwargs,
    process_registry,
    terminate_process_tree,
)
from dovo.common.redact import SecretRedactor
from dovo.common.timing import Stopwatch
from dovo.core.catalog.definitions import StepDefinition, StepType
from dovo.core.catalog.exceptions import StepValidationError
from dovo.core.catalog.services.resolve_step import resolve_step_definition
from dovo.core.inputs import NamespaceProvider, NamespaceResolutionError, build_providers, interpolate_step_fields
from dovo.engine.executors.agent_progress import AgentProgressReporter
from dovo.engine.executors.artifact_namespace import ArtifactNamespaceProvider
from dovo.engine.executors.internal_dispatch import INTERNAL_COMMAND_HANDLERS
from dovo.engine.executors.metadata import (
    build_execution_metadata,
    metadata_to_env,
    resolve_step_log_paths,
    resolve_step_temp_paths,
)
from dovo.engine.executors.models import (
    DiagnosticContext,
    ExecutionMetadata,
    InternalCommandContext,
    OutputCallback,
    PreviousStepMetadata,
    StepDispatchOutcome,
    StepExecutionContext,
    StepResult,
    require_agent_runner,
)
from dovo.engine.executors.postconditions import build_postconditions


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
    """Synchronous executor for a single StepDefinition within a worktree directory."""

    def __init__(self, metadata: StepExecutionContext) -> None:
        self.step: StepDefinition = metadata.step
        self.worktree_path = metadata.worktree_path.resolve()
        self.inputs = metadata.inputs
        self._env_file_dir = metadata.paths.root_dir if metadata.paths is not None else None
        self._sensitive_variables = metadata.sensitive_variables
        self.redactor = SecretRedactor.from_environment(
            credential_envs=self._sensitive_variables, env_file_dir=self._env_file_dir
        )
        self.on_output = self._masked_output_callback(metadata.on_output)
        self.on_progress = metadata.on_progress
        self.step_index = metadata.step_index
        self.initial_attempt = metadata.initial_attempt
        self.identity = metadata.identity
        self.steps: Sequence[PreviousStepMetadata] = list(metadata.steps) if metadata.steps else []
        self.previous_step = metadata.previous_step or (self.steps[-1] if self.steps else None)
        self.session_tmp_dir = metadata.session_tmp_dir
        self.session_log_dir = metadata.session_log_dir
        self.save_attempt_logs = metadata.save_attempt_logs
        self.loop_iteration = metadata.loop_iteration
        self.loop_id = metadata.loop_id
        self.session_id = metadata.session_id or ""
        self.artifact_store = metadata.artifact_store
        self.paths = metadata.paths
        self.agent_runner = metadata.agent_runner
        self.log_warnings: list[str] = []
        # The caller's list for the run's diagnostics, extended as each record is written; kept off the
        # StepResult, which session state persists.
        self._diagnostics: list[DiagnosticRecord] = metadata.diagnostics
        self.step_scratch_dir: Path | None = None
        self.output_file: Path | None = None
        self.max_attempts = 1
        self._uninterpolated_step: StepDefinition = metadata.step

    def run(self) -> StepResult:
        """Execute the step definition within worktree_path and return its StepResult."""
        if not self.worktree_path.exists() or not self.worktree_path.is_dir():
            outcome = _failed_dispatch(f"Worktree path '{self.worktree_path}' does not exist or is not a directory.")
            return _step_result(self.redactor, self.step.id, outcome, 0.0)

        error = self._prepare()
        if error is not None:
            return _step_result(self.redactor, self.step.id, _failed_dispatch(error), 0.0)

        watch = Stopwatch.start()
        outcome = self._run_attempts()
        watch.stop()
        duration = watch.elapsed_seconds
        outputs, output_warnings = self._collect_step_outputs()
        output_warnings = [*self.log_warnings, *output_warnings]

        if outcome.status == "completed":
            return _step_result(
                self.redactor,
                self.step.id,
                outcome,
                duration,
                status="completed",
                exit_code=0,
                outputs=outputs,
                warnings=output_warnings,
            )
        return self._finalize_failure(outcome, duration, outputs=outputs, warnings=output_warnings)

    def _prepare(self) -> str | None:
        """Resolve shorthand aliases and compute retry budget, returning the step error when resolution fails."""
        if self.step.uses is not None or self.step.run is not None:
            resolved = self._resolve_shorthand()
            if isinstance(resolved, str):
                return resolved
            self.step = resolved
        self._uninterpolated_step = self.step
        self.max_attempts = (
            self.step.on_failure.max_retries if self.step.on_failure.action == FailurePolicy.RETRY else 1
        )
        if self.session_tmp_dir is not None:
            self.step_scratch_dir, self.output_file = resolve_step_temp_paths(self.session_tmp_dir, self.step.id)
        return None

    def _resolve_shorthand(self) -> StepDefinition | str:
        """Resolve a uses/run step, catching a uses step's catalog load once, or return the step error and add its record to the diagnostics list."""
        if self.step.uses is None:
            return self._resolve_step()
        with catch_failure("engine.step", step=self.step.uses) as caught:
            resolved = self._resolve_step()
        if caught.failure is None:
            return resolved
        self._diagnostics.extend(error_diagnostics(caught.failure.diagnostic))
        return caught.failure.message

    def _resolve_step(self) -> StepDefinition | str:
        """Resolve the step's shorthand, or return the step error when it cannot be resolved."""
        try:
            return resolve_step_definition(self.step, paths=self.paths)
        except StepValidationError:
            return f"Could not resolve step '{self.step.id}'."

    def _run_attempts(self) -> StepDispatchOutcome:
        """Run attempts up to max_attempts times, sleeping backoff_ms between failures."""
        backoff_ms = self._uninterpolated_step.on_failure.backoff_ms
        outcome = _failed_dispatch("Step did not run.")
        for attempt_offset in range(self.max_attempts):
            outcome = self._run_attempt(self.initial_attempt + attempt_offset)
            if outcome.status == "completed":
                return outcome
            if attempt_offset < self.max_attempts - 1 and backoff_ms > 0:
                time.sleep(backoff_ms / 1000)
        return outcome

    def _namespace_providers(self, metadata: ExecutionMetadata) -> list[NamespaceProvider]:
        """Build the attempt's providers from step inputs, execution metadata and the artifact store."""
        return build_providers(
            inputs=self.inputs,
            metadata=metadata,
            artifacts=ArtifactNamespaceProvider(self.artifact_store),
        )

    def _run_attempt(self, attempt: int) -> StepDispatchOutcome:
        """Interpolate, dispatch and apply postconditions for one attempt."""
        metadata = build_execution_metadata(
            self._uninterpolated_step,
            step_index=self.step_index,
            attempt=attempt,
            identity=self.identity,
            previous_step=self.previous_step,
            steps=self.steps,
            session_tmp_dir=self.session_tmp_dir,
            session_id=self.session_id,
            loop_iteration=self.loop_iteration,
        )
        try:
            self.step = interpolate_step_fields(self._uninterpolated_step, self._namespace_providers(metadata))
        except NamespaceResolutionError as error:
            return _failed_dispatch(str(error)).model_copy(update={"attempts": attempt})

        self._reset_step_output_file()
        outcome = self._dispatch_primitive(metadata).model_copy(update={"attempts": attempt})
        outcome = self._apply_postconditions(outcome, attempt)
        self._diagnostics.extend(outcome.diagnostics)
        return outcome

    def _apply_postconditions(self, outcome: StepDispatchOutcome, attempt: int) -> StepDispatchOutcome:
        """Run each postcondition in order, stopping at the first non-completed outcome."""
        postconditions = build_postconditions(
            self.step,
            worktree_path=self.worktree_path,
            store=self.artifact_store,
            loop_iteration=self.loop_iteration,
            warnings=self.log_warnings,
        )
        for postcondition in postconditions:
            if outcome.status != "completed":
                break
            outcome = postcondition.apply(outcome, attempt)
        return outcome

    def _dispatch_primitive(self, metadata: ExecutionMetadata) -> StepDispatchOutcome:
        """Run the step's primitive type once and return its dispatch outcome."""
        if self.step.type == StepType.COMMAND:
            return self._execute_command(metadata)
        if self.step.type == StepType.SCRIPT:
            return self._execute_script(metadata)
        if self.step.type == StepType.AGENT:
            return self._execute_agent(metadata)
        if self.step.type == StepType.INTERNAL:
            return self._execute_internal(metadata)
        return _failed_dispatch(f"Unsupported step primitive type '{self.step.type}'.")

    def _execute_internal(self, metadata: ExecutionMetadata) -> StepDispatchOutcome:
        """Dispatch the internal command in-process with step id, attempt and loop iteration in its context."""
        command = self.step.command
        if not command:
            return _failed_dispatch("Internal step has no command string defined.")

        handler = INTERNAL_COMMAND_HANDLERS.get(command)
        if handler is None:
            return _failed_dispatch(f"Unknown internal command '{command}'.")

        context = InternalCommandContext(
            worktree_path=self.worktree_path,
            env=self.step.env,
            artifact_store=self.artifact_store,
            step_id=self.step.id,
            attempt=metadata.step.attempt,
            loop_iteration=self.loop_iteration,
        )
        try:
            return handler(context)
        except Exception as exc:
            self._record_failure(exc)
            return _failed_dispatch(f"Internal command '{command}' error: {exc}")

    def _execute_command(self, metadata: ExecutionMetadata) -> StepDispatchOutcome:
        """Execute a COMMAND step inside worktree_path."""
        if not self.step.command:
            return _failed_dispatch("Command step has no command string defined.")
        return self._run_process(
            self.step.command,
            shell=True,
            timeout_seconds=self.step.timeout_seconds,
            failure_label="Command",
            step_kind="Command",
            metadata=metadata,
        )

    def _execute_script(self, metadata: ExecutionMetadata) -> StepDispatchOutcome:
        """Execute a SCRIPT step inside worktree_path."""
        if not self.step.script_path:
            return _failed_dispatch("Script step has no script_path defined.")

        script_file = self.worktree_path / self.step.script_path
        if not script_file.exists() or not script_file.is_file():
            return _failed_dispatch(f"Script file not found at '{self.step.script_path}'.")

        cmd, shell = _resolve_script_invocation(script_file)
        return self._run_process(
            cmd,
            shell=shell,
            timeout_seconds=self.step.timeout_seconds,
            failure_label="Script",
            step_kind="Script",
            metadata=metadata,
        )

    def _execute_agent(self, metadata: ExecutionMetadata) -> StepDispatchOutcome:
        """Execute an AGENT step through the injected runner, appending its masked provider lines to the attempt's stdout and stderr logs."""
        runner = require_agent_runner(self.step, self.agent_runner)
        self._refresh_redactor(self._build_process_env(metadata))
        stdout_log, stderr_log = self._open_attempt_logs(metadata)
        # Each stream is written by its own reader thread, so each handle has a single writer.
        handles: dict[str, IO[str] | None] = {"stdout": stdout_log, "stderr": stderr_log}

        def on_agent_line(stream_name: str, line: str) -> None:
            handles[stream_name] = self._append_log_line(handles[stream_name], f"{line}\n")

        progress = AgentProgressReporter(
            step_id=self.step.id,
            step_index=metadata.step.index,
            attempt=metadata.step.attempt,
            loop_id=self.loop_id,
            iteration=self.loop_iteration,
            on_progress=self.on_progress,
            session_log_dir=self.session_log_dir,
            redactor=self.redactor,
        )

        try:
            return runner(
                self.step,
                self.worktree_path,
                self.on_output,
                None if stdout_log is None and stderr_log is None else on_agent_line,
                progress,
                metadata,
            )
        finally:
            self._close_log_file(handles["stdout"])
            self._close_log_file(handles["stderr"])

    def _masked_output_callback(self, callback: OutputCallback | None) -> OutputCallback | None:
        """Wrap ``callback`` so every streamed line is masked with the current redactor; None stays None."""
        if callback is None:
            return None

        def _masked(stream_name: str, line: str) -> None:
            callback(stream_name, self.redactor.redact_text(line))

        return _masked

    def _refresh_redactor(self, process_env: Mapping[str, str]) -> None:
        """Rebuild the redactor from this attempt's merged process environment, the listed sensitive variables, and the repository .env."""
        self.redactor = SecretRedactor.from_environment(
            env=process_env, credential_envs=self._sensitive_variables, env_file_dir=self._env_file_dir
        )

    def _build_process_env(self, metadata: ExecutionMetadata) -> dict[str, str]:
        """Merge environment variables: explicit step env > DOVO_* metadata > ambient env."""
        # Authored command/script steps keep ambient inheritance on purpose: their environment is the author's
        # contract (explicit step env > DOVO_* metadata > ambient). Only Dovo-owned agent provider subprocesses use
        # the allowlist policy in dovo.core.agents.environment.
        process_env = os.environ.copy()
        process_env.update(metadata_to_env(metadata))
        process_env.update(self.step.env)
        return process_env

    def _diagnostic_context(self) -> DiagnosticContext:
        """Return the diagnostic log context naming this step's session, id and worktree."""
        return {"session_id": self.session_id or None, "step_id": self.step.id, "path": str(self.worktree_path)}

    def _record_failure(self, exc: Exception) -> None:
        """Record exc at error level and add its record to the diagnostics list, never the step's result."""
        self._diagnostics.extend(
            error_diagnostics(
                record_exception(
                    exc, level=DiagnosticLevel.ERROR, operation="engine.step", context=self._diagnostic_context()
                )
            )
        )

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
                if not collected_errors:
                    self._record_failure(exc)
                collected_errors.append(f"Output callback error on {stream_name}: {exc}")

    def _append_log_line(self, log_file: IO[str] | None, line: str) -> IO[str] | None:
        """Append and flush one line to the attempt log; on OSError warn and return None to stop logging."""
        if log_file is None:
            return None
        try:
            log_file.write(self.redactor.redact_text(line))
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
            if not collected_errors:
                self._record_failure(exc)
            collected_errors.append(f"Failed reading {stream_name} stream: {exc}")
        finally:
            try:
                pipe.close()
            except Exception:
                # No record: closing a pipe during termination.
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
        self._refresh_redactor(env)
        isolation_kwargs = get_isolated_process_kwargs()
        stdout_log, stderr_log = self._open_attempt_logs(metadata)
        try:
            proc = subprocess.Popen(
                cmd,
                shell=shell,
                cwd=self.worktree_path,
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
            self._record_failure(exc)
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
                    # No record: a timed-out step's exit code defaults to 124.
                    pass
            except BaseException:
                # No record: the tree is terminated and the exception re-raised.
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
        on_failure = self._uninterpolated_step.on_failure
        escalation = on_failure.on_max_retries if on_failure.action == FailurePolicy.RETRY else on_failure.action

        if escalation == FailurePolicy.CONTINUE:
            return _step_result(
                self.redactor,
                self.step.id,
                outcome,
                duration,
                status="ignored",
                exit_code=0,
                outputs=outputs,
                warnings=warnings,
            )
        return _step_result(
            self.redactor, self.step.id, outcome, duration, status="failed", outputs=outputs, warnings=warnings
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
    redactor: SecretRedactor,
    step_id: str,
    outcome: StepDispatchOutcome,
    duration: float,
    *,
    status: str | None = None,
    exit_code: int | None = None,
    outputs: dict[str, str] | None = None,
    warnings: list[str] | None = None,
) -> StepResult:
    """Convert a StepDispatchOutcome into a StepResult whose stdout, stderr, and error_message are masked."""
    return StepResult(
        step_id=step_id,
        status=status if status is not None else outcome.status,
        exit_code=exit_code if exit_code is not None else outcome.exit_code,
        stdout=redactor.redact_text(outcome.stdout),
        stderr=redactor.redact_text(outcome.stderr),
        duration_seconds=duration,
        attempts=outcome.attempts,
        error_message=None if outcome.error_message is None else redactor.redact_text(outcome.error_message),
        outputs=outputs or {},
        rejected_patch_path=outcome.rejected_patch_path,
        warnings=warnings or [],
    )
