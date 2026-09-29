"""State-driven run coordinator: select the next node, apply one durable transition, repeat."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from worktree.common.lock import WorkspaceLock
from worktree.common.models import FailurePolicy
from worktree.common.process import process_registry
from worktree.core.blueprint import Blueprint
from worktree.core.blueprint.exceptions import (
    BlueprintLoadError,
    BlueprintNotFoundError,
    BlueprintValidationError,
)
from worktree.core.db import RunStatus
from worktree.core.engine.context import RunSessionContext
from worktree.core.engine.exceptions import EngineSnapshotMissingError
from worktree.core.engine.failure import (
    effective_terminal_policy,
    failed_step_message,
    mark_continued_after_prompt,
)
from worktree.core.engine.loop_runner import LoopBlockRunner
from worktree.core.engine.models import (
    FailurePrompter,
    RunContext,
    RunObserver,
    RunOutcome,
    StepLoopState,
)
from worktree.core.engine.projection import flatten_step_results, terminal_step_metadata
from worktree.core.engine.state_models import (
    TERMINAL_NODE_STATES,
    ExecutionIterationRecord,
    ExecutionLeafNode,
    ExecutionLoopNode,
    ExecutionStateTree,
    NodeState,
    StepAttemptRecord,
)
from worktree.core.engine.state_store import RunStateStore
from worktree.core.engine.step_executor import StepCoordinator
from worktree.core.engine.writer import load_blueprint_from_snapshot
from worktree.core.step import (
    ExecutionIdentity,
    LoopStepBlock,
    PreviousStepMetadata,
    StepDefinition,
    StepResult,
)

_INTERRUPTED_MESSAGE = "Step attempt was interrupted before it recorded a result."


class NodeTransitionKind(StrEnum):
    """Outcome of applying one durable transition to a node."""

    COMPLETED = "completed"
    CONTINUED = "continued"
    FAILED = "failed"
    PAUSED = "paused"
    RETRY = "retry"


@dataclass
class _LoadedRun:
    """Durable state and definitions the coordinator drives, loaded once per execute()."""

    state: ExecutionStateTree
    blueprint: Blueprint
    step_coordinator: StepCoordinator


def _now() -> str:
    """Return the current wall-clock time as an ISO-8601 UTC string."""
    return datetime.now(UTC).isoformat()


class RunCoordinator:
    """State-driven execution of a run: select the next node, apply one durable transition, repeat."""

    def __init__(
        self,
        state_store: RunStateStore,
        context: RunSessionContext,
        observer: RunObserver | None = None,
        prompter: FailurePrompter | None = None,
    ) -> None:
        """Bind the coordinator to one run's state store, session infrastructure, observer, and prompter."""
        self._state_store = state_store
        self._context = context
        self._observer = observer
        self._prompter = prompter
        self._errors: list[str] = []
        self._warnings: list[str] = []
        self._loaded: _LoadedRun | None = None
        self._in_flight: ExecutionLeafNode | ExecutionLoopNode | None = None

    @property
    def _run(self) -> _LoadedRun:
        """Return the loaded run, raising when the coordinator has not started."""
        if self._loaded is None:
            raise RuntimeError("RunCoordinator used before its run state was loaded.")
        return self._loaded

    def execute(self) -> RunOutcome:
        """Run the execution state machine to completion, pause, or terminal failure."""
        if not self._start():
            return self._outcome(RunStatus.FAILED)

        try:
            if not self._recover_interrupted():
                return self._outcome(RunStatus.FAILED)
            return self._drive()
        except KeyboardInterrupt:
            return self._cancel()

    def advance_leaf(self, node: ExecutionLeafNode, step_def: StepDefinition) -> NodeTransitionKind:
        """Execute a single leaf step, or if node.state is PAUSED, re-enter the failure gate with node.attempts[-1].result."""
        if self._loaded is None and not self._start():
            return NodeTransitionKind.FAILED

        located = self._locate_leaf(node)
        if located is None:
            self._errors.append(f"Step '{node.id}' is not part of the run state.")
            return NodeTransitionKind.FAILED

        position, leaf = located
        self._in_flight = leaf
        if leaf.state is NodeState.PAUSED:
            return self._resolve_failed_leaf(leaf, step_def)
        if leaf.state is NodeState.PENDING and not self._begin_attempt(leaf, 1):
            return NodeTransitionKind.FAILED

        state = self._run.state
        steps_metadata = terminal_step_metadata(state)
        result = self._run.step_coordinator.run_attempt(
            self._step_state(),
            step_def,
            idx=position + 1,
            total=len(state.nodes),
            step_context=self._step_context(),
            previous_step=steps_metadata[-1] if steps_metadata else PreviousStepMetadata(),
            steps=steps_metadata,
            initial_attempt=leaf.attempts[-1].number,
        )
        return self._settle_attempt(leaf, step_def, result)

    def _drive(self) -> RunOutcome:
        """Dispatch the next non-terminal node until the run completes, pauses, or fails."""
        while True:
            upcoming = self._next_node()
            if upcoming is None:
                return self._outcome(RunStatus.COMPLETED)

            index, node = upcoming
            self._in_flight = node
            transition = self._dispatch(index, node)
            if transition is NodeTransitionKind.PAUSED:
                return self._outcome(RunStatus.PAUSED)
            if transition is NodeTransitionKind.FAILED:
                return self._outcome(RunStatus.FAILED)

    def _cancel(self) -> RunOutcome:
        """Terminate child processes, persist the in-flight node as cancelled, and return the CANCELLED outcome."""
        process_registry.terminate_all(grace_seconds=0.5)
        if self._in_flight is not None:
            self._in_flight.state = NodeState.CANCELLED
            self._commit("while cancelling the run")
        self._errors = ["Execution cancelled by user."]
        return self._outcome(RunStatus.CANCELLED)

    def _start(self) -> bool:
        """Load state, run row, and snapshot definitions and build the step coordinator; append the failure to _errors and return False when unavailable."""
        session_id = self._context.session_id
        loaded = self._state_store.load()
        if not loaded.ok or loaded.state is None:
            self._errors.append(loaded.errors[0] if loaded.errors else f"Run '{session_id}' has no execution state.")
            return False

        row = self._state_store.runs.get(session_id)
        if row is None:
            self._errors.append(f"Run '{session_id}' not found.")
            return False

        try:
            blueprint = load_blueprint_from_snapshot(self._context.paths.session_dir(session_id), loaded.state.manifest)
        except (
            EngineSnapshotMissingError,
            BlueprintLoadError,
            BlueprintNotFoundError,
            BlueprintValidationError,
        ) as exc:
            self._errors.append(str(exc))
            return False

        self._warnings.extend(loaded.warnings)
        step_coordinator = StepCoordinator(
            RunContext(
                steps=[],
                cwd=self._context.target_dir,
                use_sandbox=row.use_sandbox,
                keep=row.keep,
                agent=row.agent,
                observer=self._observer,
                inputs=json.loads(row.inputs_json) if row.inputs_json else None,
                identity=ExecutionIdentity(blueprint_name=row.blueprint_name, blueprint_key=row.blueprint_key),
                session_id=session_id,
                no_tty=self._context.no_tty,
                failure_prompter=self._prompter,
                auto_apply=row.auto_apply,
                paths=self._context.paths,
            )
        )
        self._loaded = _LoadedRun(state=loaded.state, blueprint=blueprint, step_coordinator=step_coordinator)
        return True

    def _recover_interrupted(self) -> bool:
        """Close every RUNNING leaf whose last attempt has no result with a failed interrupted result and mark it PAUSED."""
        recovered = False
        for node in self._run.state.nodes:
            if node.kind != "step" or node.state is not NodeState.RUNNING:
                continue
            if not node.attempts or node.attempts[-1].result is not None:
                continue

            attempt = node.attempts[-1]
            interrupted = StepResult(
                step_id=node.id,
                status="failed",
                exit_code=1,
                stdout="",
                stderr="",
                duration_seconds=0.0,
                attempts=attempt.number,
                error_message=_INTERRUPTED_MESSAGE,
            )
            node.attempts[-1] = attempt.model_copy(update={"completed_at": _now(), "result": interrupted})
            node.state = NodeState.PAUSED
            recovered = True

        return self._commit("while recovering interrupted attempts") if recovered else True

    def _next_node(self) -> tuple[int, ExecutionLeafNode | ExecutionLoopNode] | None:
        """Return the index and first top-level node not in a terminal state, or None."""
        for index, node in enumerate(self._run.state.nodes):
            if node.state not in TERMINAL_NODE_STATES:
                return index, node
        return None

    def _locate_leaf(self, node: ExecutionLeafNode) -> tuple[int, ExecutionLeafNode] | None:
        """Return the top-level position and the live tree leaf sharing node's id, or None when the tree has none."""
        for position, candidate in enumerate(self._run.state.nodes):
            if candidate.kind == "step" and candidate.id == node.id:
                return position, candidate
        return None

    def _dispatch(self, index: int, node: ExecutionLeafNode | ExecutionLoopNode) -> NodeTransitionKind:
        """Route a leaf to advance_leaf or a loop to _advance_loop using the snapshot definition with the node's id."""
        definition = next((step for step in self._run.blueprint.steps if step.id == node.id), None)
        if isinstance(node, ExecutionLeafNode) and isinstance(definition, StepDefinition):
            return self.advance_leaf(node, definition)
        if isinstance(node, ExecutionLoopNode) and isinstance(definition, LoopStepBlock):
            return self._advance_loop(index, node, definition)

        self._errors.append(f"Step '{node.id}' has no matching definition in the run snapshot.")
        return NodeTransitionKind.FAILED

    def _advance_loop(self, index: int, node: ExecutionLoopNode, loop: LoopStepBlock) -> NodeTransitionKind:
        """Run a loop block through LoopBlockRunner between durable running and terminal loop-node commits."""
        node.state = NodeState.RUNNING
        if not self._commit(f"before loop '{node.id}' started"):
            return NodeTransitionKind.FAILED

        step_coordinator = self._run.step_coordinator
        runner = LoopBlockRunner(
            loop=loop,
            sandbox_path=self._context.target_dir,
            coordinator=step_coordinator,
            context=self._step_context(),
            observer=self._observer,
            failure_prompter=self._prompter,
            no_tty=self._context.no_tty,
            step_index=index + 1,
            identity=step_coordinator.context.identity,
            session_tmp_dir=self._context.session_tmp_dir,
            session_log_dir=self._context.session_log_dir,
            save_attempt_logs=self._context.save_attempt_logs,
            session_id=self._context.session_id,
            artifacts_dir=self._context.artifacts_dir,
            artifacts_db=self._context.artifacts_db,
        )
        action, results, error = runner.run(self._step_state())
        self._record_loop_iterations(node, loop, results)

        if action == "abort":
            node.state = NodeState.FAILED
            if error:
                self._errors.append(error)
            self._commit(f"after loop '{node.id}' aborted")
            return NodeTransitionKind.FAILED

        node.state = NodeState.COMPLETED
        if not self._commit(f"after loop '{node.id}' finished"):
            return NodeTransitionKind.FAILED
        return NodeTransitionKind.COMPLETED

    def _record_loop_iterations(
        self,
        node: ExecutionLoopNode,
        loop: LoopStepBlock,
        results: Sequence[StepResult],
    ) -> None:
        """Write the loop's per-turn results into node.iterations as terminal leaf attempts."""
        width = len(loop.do)
        for turn, start in enumerate(range(0, len(results), width), start=1):
            chunk = results[start : start + width]
            if turn == 1 and node.iterations:
                iteration = node.iterations[0]
            else:
                iteration = ExecutionIterationRecord(
                    number=turn,
                    steps=[ExecutionLeafNode(id=step.id, name=step.name) for step in loop.do],
                )
                node.iterations.append(iteration)

            now = _now()
            for leaf, result in zip(iteration.steps, chunk, strict=False):
                leaf.attempts = [
                    StepAttemptRecord(number=result.attempts, started_at=now, completed_at=now, result=result)
                ]
                leaf.state = NodeState(result.status)
            iteration.state = NodeState.FAILED if chunk[-1].status == "failed" else NodeState.COMPLETED

    def _begin_attempt(self, node: ExecutionLeafNode, number: int, run_status: RunStatus | None = None) -> bool:
        """Append an unfinished attempt, mark the node RUNNING, and commit before the attempt executes."""
        node.attempts.append(StepAttemptRecord(number=number, started_at=_now()))
        node.state = NodeState.RUNNING
        return self._commit(f"before step '{node.id}' started", run_status)

    def _settle_attempt(
        self,
        node: ExecutionLeafNode,
        step_def: StepDefinition,
        result: StepResult,
    ) -> NodeTransitionKind:
        """Record the attempt's result, commit terminal success, or hand a failed result to the failure gate."""
        attempt = node.attempts[-1]
        node.attempts[-1] = attempt.model_copy(
            update={"completed_at": _now(), "number": result.attempts, "result": result}
        )
        if not result.ok:
            return self._resolve_failed_leaf(node, step_def)

        state = NodeState.IGNORED if result.status == "ignored" else NodeState.COMPLETED
        if not self._finish_leaf(node, state, None, f"after step '{node.id}' finished"):
            return NodeTransitionKind.FAILED
        return NodeTransitionKind.CONTINUED if state is NodeState.IGNORED else NodeTransitionKind.COMPLETED

    def _resolve_failed_leaf(self, node: ExecutionLeafNode, step_def: StepDefinition) -> NodeTransitionKind:
        """Apply the terminal failure policy to node.attempts[-1].result: continue, abort, or prompt with a persisted PAUSED state."""
        result = node.attempts[-1].result if node.attempts else None
        if result is None:
            self._errors.append(f"Paused step '{node.id}' has no recorded attempt result.")
            return NodeTransitionKind.FAILED

        policy = effective_terminal_policy(step_def.on_failure)
        if policy == FailurePolicy.CONTINUE:
            return self._continue_leaf(node, mark_continued_after_prompt(result), None)
        if policy != FailurePolicy.PROMPT_USER:
            return self._abort_leaf(node, result, None)
        return self._prompt_failed_leaf(node, step_def, result)

    def _prompt_failed_leaf(
        self,
        node: ExecutionLeafNode,
        step_def: StepDefinition,
        result: StepResult,
    ) -> NodeTransitionKind:
        """Persist the PAUSED state, ask for a decision, and apply it as retry, continue, or abort."""
        step_coordinator = self._run.step_coordinator
        interactive = step_coordinator.is_interactive
        if interactive and not self._persist_paused(node, result):
            return NodeTransitionKind.FAILED

        try:
            decision, warning = step_coordinator.prompt_decision(step_def, result)
        except KeyboardInterrupt:
            self._errors.append(failed_step_message(result))
            return NodeTransitionKind.PAUSED

        if warning is not None:
            self._warnings.append(warning)
        action, recorded, _ = step_coordinator.apply_prompt_decision(decision, result)
        return self._apply_action(node, action, recorded or result, RunStatus.RUNNING if interactive else None)

    def _persist_paused(self, node: ExecutionLeafNode, result: StepResult) -> bool:
        """Commit the leaf as PAUSED and the run as paused before the prompter is consulted."""
        node.state = NodeState.PAUSED
        return self._commit(f"before prompting for step '{node.id}'", RunStatus.PAUSED, failed_step_message(result))

    def _apply_action(
        self,
        node: ExecutionLeafNode,
        action: str,
        result: StepResult,
        run_status: RunStatus | None,
    ) -> NodeTransitionKind:
        """Apply a prompt action: retry begins the next attempt, continue ignores the leaf, abort fails it."""
        if action == "retry":
            if not self._begin_attempt(node, node.attempts[-1].number + 1, run_status=RunStatus.RUNNING):
                return NodeTransitionKind.FAILED
            return NodeTransitionKind.RETRY
        if action == "continue":
            return self._continue_leaf(node, result, run_status)
        return self._abort_leaf(node, result, run_status)

    def _continue_leaf(
        self,
        node: ExecutionLeafNode,
        result: StepResult,
        run_status: RunStatus | None,
    ) -> NodeTransitionKind:
        """Persist the leaf as IGNORED with its non-fatal result."""
        if not self._finish_leaf(node, NodeState.IGNORED, result, f"after step '{node.id}' continued", run_status):
            return NodeTransitionKind.FAILED
        return NodeTransitionKind.CONTINUED

    def _abort_leaf(
        self,
        node: ExecutionLeafNode,
        result: StepResult,
        run_status: RunStatus | None,
    ) -> NodeTransitionKind:
        """Persist the leaf as FAILED and record the step failure as the run error."""
        self._errors.append(failed_step_message(result))
        self._finish_leaf(node, NodeState.FAILED, None, f"after step '{node.id}' failed", run_status)
        return NodeTransitionKind.FAILED

    def _finish_leaf(
        self,
        node: ExecutionLeafNode,
        state: NodeState,
        result: StepResult | None,
        boundary: str,
        run_status: RunStatus | None = None,
    ) -> bool:
        """Set the leaf's terminal state (and replace its last result when given) and commit."""
        node.state = state
        if result is not None:
            node.attempts[-1] = node.attempts[-1].model_copy(update={"result": result})
        return self._commit(boundary, run_status)

    def _commit(
        self,
        boundary: str,
        run_status: RunStatus | None = None,
        error_message: str | None = None,
    ) -> bool:
        """Save the in-memory state at revision + 1 under the workspace lock; append a boundary-naming error and return False on failure."""
        sandbox = self._context.sandbox
        with WorkspaceLock(self._context.paths.lock_file):
            saved = self._state_store.save(
                self._run.state,
                run_status=run_status,
                error_message=error_message,
                sandbox_id=sandbox.session_id if sandbox is not None else None,
            )
        if not saved.ok or saved.state is None:
            detail = saved.errors[0] if saved.errors else "state was not saved"
            self._errors.append(f"Failed to persist run state {boundary}: {detail}")
            return False

        self._run.state = saved.state
        self._warnings.extend(saved.warnings)
        return True

    def _step_state(self) -> StepLoopState:
        """Build the StepLoopState bridge for StepCoordinator from the session context and flattened results."""
        return StepLoopState(
            target_dir=self._context.target_dir,
            session=self._context.sandbox,
            step_results=flatten_step_results(self._run.state),
            session_tmp_dir=self._context.session_tmp_dir,
            session_log_dir=self._context.session_log_dir,
            save_attempt_logs=self._context.save_attempt_logs,
            warnings=self._warnings,
            artifacts_dir=self._context.artifacts_dir,
            artifacts_db=self._context.artifacts_db,
        )

    def _step_context(self) -> dict[str, object] | None:
        """Build the per-step context dict from the run row's agent and inputs."""
        return self._run.step_coordinator.build_step_context()

    def _outcome(self, status: RunStatus) -> RunOutcome:
        """Build the RunOutcome for status from the flattened state, accumulated errors, warnings, and sandbox identity."""
        sandbox = self._context.sandbox
        return RunOutcome(
            status=status,
            step_results=flatten_step_results(self._loaded.state) if self._loaded is not None else [],
            errors=list(self._errors),
            warnings=list(self._warnings),
            sandbox_path=sandbox.sandbox_path if sandbox is not None else self._context.target_dir,
            session_id=self._context.session_id,
            sandbox_id=sandbox.session_id if sandbox is not None else None,
        )
