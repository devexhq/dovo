"""State-driven run coordinator: select the next node, apply one durable transition, repeat."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from dovo.common.lock import WorkspaceLock
from dovo.common.models import FailurePolicy
from dovo.common.process import process_registry
from dovo.core.agents.models import ResolvedAgentSettings
from dovo.core.catalog.blueprint import Blueprint
from dovo.core.catalog.definitions import LoopStepBlock, StepDefinition
from dovo.core.catalog.exceptions import BlueprintLoadError, BlueprintNotFoundError, BlueprintValidationError
from dovo.core.db import SessionStatus
from dovo.engine.exceptions import EngineSnapshotMissingError
from dovo.engine.executors.models import ExecutionIdentity, PreviousStepMetadata, StepResult
from dovo.engine.failure import (
    effective_terminal_policy,
    failed_step_message,
    resolve_terminal_action,
)
from dovo.engine.loop_events import LoopEventEmitter
from dovo.engine.loop_policy import LoopDecision, LoopPolicy, LoopTransitionKind
from dovo.engine.models import (
    FailurePrompter,
    LoopPromptDecision,
    RunContext,
    RunObserver,
    RunOutcome,
    RunSettings,
    StepAction,
)
from dovo.engine.projection import (
    flatten_step_results,
    iter_leaves,
    iteration_results,
    terminal_step_metadata,
)
from dovo.engine.state_models import (
    TERMINAL_NODE_STATES,
    ExecutionIterationRecord,
    ExecutionLeafNode,
    ExecutionLoopNode,
    ExecutionStateTree,
    NodeState,
    StepAttemptRecord,
)
from dovo.engine.state_store import SessionStateStore, new_iteration
from dovo.engine.state_validation import validate_loop_structure
from dovo.engine.step_coordinator import StepCoordinator
from dovo.engine.writer import load_blueprint_from_snapshot

_INTERRUPTED_MESSAGE = "Step attempt was interrupted before it recorded a result."
_CEILING_GRANT_COUNT = 3


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
        state_store: SessionStateStore,
        context: RunContext,
        observer: RunObserver | None = None,
        prompter: FailurePrompter | None = None,
        agent: ResolvedAgentSettings | None = None,
    ) -> None:
        """Bind the coordinator to one run's state store, session infrastructure, observer, prompter, and resolved agent settings."""
        self._state_store = state_store
        self._context = context
        self._observer = observer
        self._prompter = prompter
        self._agent = agent
        self._errors: list[str] = []
        self._warnings: list[str] = []
        self._loaded: _LoadedRun | None = None
        self._load_ok: bool | None = None
        self._in_flight: ExecutionLeafNode | ExecutionLoopNode | None = None

    @property
    def _run(self) -> _LoadedRun:
        """Return the loaded run, raising when the coordinator has not started."""
        if self._loaded is None:
            raise RuntimeError("RunCoordinator used before its run state was loaded.")
        return self._loaded

    @property
    def steps(self) -> list[StepDefinition | LoopStepBlock]:
        """Return the loaded blueprint's top-level definitions; raise RuntimeError before a successful load()."""
        return self._run.blueprint.steps

    def load(self) -> bool:
        """Load state, row, and snapshot definitions once; cache and return whether the run can execute."""
        if self._load_ok is None:
            self._load_ok = self._start()
        return self._load_ok

    def execute(self) -> RunOutcome:
        """Run the execution state machine to completion, pause, or terminal failure."""
        if not self.load():
            return self._outcome(SessionStatus.FAILED)

        try:
            if not self._recover_interrupted():
                return self._outcome(SessionStatus.FAILED)
            return self._drive()
        except KeyboardInterrupt:
            return self._cancel()

    def _advance_leaf(self, node: ExecutionLeafNode, step_def: StepDefinition, position: int) -> NodeTransitionKind:
        """Execute the leaf at top-level position, or if node.state is PAUSED, re-enter the failure gate with node.attempts[-1].result."""
        return self._advance_slot(
            node,
            step_def,
            idx=position + 1,
            total=len(self._run.state.nodes),
            loop=None,
        )

    def _advance_loop(self, loop: ExecutionLoopNode, loop_block: LoopStepBlock) -> NodeTransitionKind:
        """Orchestrates loop iterations and child steps driven by LoopPolicy."""
        events = LoopEventEmitter(loop.id, self._context.session_log_dir, self._observer)
        if loop.state is NodeState.PENDING:
            events.start(loop.max_iterations)
            if not self._begin_iteration(loop, loop.iterations[0], events):
                return NodeTransitionKind.FAILED

        while True:
            decision = LoopPolicy.evaluate_iteration(loop, loop.iterations[-1], loop_block.do)
            transition = self._apply_loop_decision(loop, loop_block, decision, events)
            if transition is not None:
                return transition

    def _advance_slot(
        self,
        leaf: ExecutionLeafNode,
        step_def: StepDefinition,
        *,
        idx: int,
        total: int,
        loop: ExecutionLoopNode | None,
    ) -> NodeTransitionKind:
        """Run or re-enter one live leaf at 1-based idx of total, attributed to the loop's last iteration when loop is set, with the shared attempt, failure-gate, and commit path."""
        loop_id = loop.id if loop is not None else None
        loop_iteration = loop.iterations[-1].number if loop is not None else None
        if leaf.state is NodeState.PAUSED:
            return self._resolve_failed_leaf(leaf, step_def)
        if leaf.state is NodeState.PENDING and not self._begin_attempt(leaf, 1):
            return NodeTransitionKind.FAILED

        steps_metadata = terminal_step_metadata(self._run.state)
        result = self._run.step_coordinator.run_attempt(
            self._context,
            self._warnings,
            step_def,
            idx=idx,
            total=total,
            step_context=self._step_context(loop_iteration),
            previous_step=steps_metadata[-1] if steps_metadata else PreviousStepMetadata(),
            steps=steps_metadata,
            initial_attempt=leaf.attempts[-1].number,
            loop_iteration=loop_iteration,
            loop_id=loop_id,
        )
        return self._settle_attempt(leaf, step_def, result)

    def _drive(self) -> RunOutcome:
        """Dispatch the next non-terminal node until the run completes, pauses, or fails."""
        while True:
            upcoming = self._next_node()
            if upcoming is None:
                return self._outcome(SessionStatus.COMPLETED)

            position, node = upcoming
            self._in_flight = node
            transition = self._dispatch(position, node)
            if transition is NodeTransitionKind.PAUSED:
                return self._outcome(SessionStatus.PAUSED)
            if transition is NodeTransitionKind.FAILED:
                return self._outcome(SessionStatus.FAILED)

    def _cancel(self) -> RunOutcome:
        """Terminate child processes, persist the in-flight node as cancelled, and return the CANCELLED outcome."""
        process_registry.terminate_all(grace_seconds=0.5)
        if self._in_flight is not None:
            self._in_flight.state = NodeState.CANCELLED
            self._commit("while cancelling the run")
        self._errors = ["Execution cancelled by user."]
        return self._outcome(SessionStatus.CANCELLED)

    def _start(self) -> bool:
        """Load state, session row, and snapshot definitions and build the step coordinator; append the failure to _errors and return False when unavailable."""
        session_id = self._context.session_id
        loaded = self._state_store.load()
        if not loaded.ok or loaded.state is None:
            self._errors.append(
                loaded.errors[0] if loaded.errors else f"Session '{session_id}' has no execution state."
            )
            return False

        row = self._state_store.sessions.get(session_id)
        if row is None:
            self._errors.append(f"Session '{session_id}' not found.")
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

        structure_errors = validate_loop_structure(loaded.state, blueprint)
        if structure_errors:
            self._errors.append(f"Execution state for session '{session_id}' is corrupt: {structure_errors[0]}")
            return False

        self._warnings.extend(loaded.warnings)
        step_coordinator = StepCoordinator(
            RunSettings(
                cwd=self._context.target_dir,
                use_worktree=row.use_worktree,
                keep=row.keep,
                agent=self._agent,
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
        for node in iter_leaves(self._run.state):
            if node.state is not NodeState.RUNNING:
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
        """Return the top-level position and node of the first node not in a terminal state, or None."""
        for position, node in enumerate(self._run.state.nodes):
            if node.state not in TERMINAL_NODE_STATES:
                return position, node
        return None

    def _dispatch(self, position: int, node: ExecutionLeafNode | ExecutionLoopNode) -> NodeTransitionKind:
        """Route the leaf at position to _advance_leaf or a loop to _advance_loop using the snapshot definition with the node's id."""
        definition = next((step for step in self._run.blueprint.steps if step.id == node.id), None)
        if isinstance(node, ExecutionLeafNode) and isinstance(definition, StepDefinition):
            return self._advance_leaf(node, definition, position)
        if isinstance(node, ExecutionLoopNode) and isinstance(definition, LoopStepBlock):
            return self._advance_loop(node, definition)

        self._errors.append(f"Step '{node.id}' has no matching definition in the run snapshot.")
        return NodeTransitionKind.FAILED

    def _begin_iteration(
        self, loop: ExecutionLoopNode, iteration: ExecutionIterationRecord, events: LoopEventEmitter
    ) -> bool:
        """Mark iteration and loop RUNNING, emit iteration_start, and commit before the iteration's first body step runs."""
        iteration.state = NodeState.RUNNING
        loop.state = NodeState.RUNNING
        events.iteration_start(iteration.number, loop.iteration_ceiling)
        return self._commit(f"before loop '{loop.id}' iteration {iteration.number} started")

    def _apply_loop_decision(
        self,
        loop: ExecutionLoopNode,
        loop_block: LoopStepBlock,
        decision: LoopDecision,
        events: LoopEventEmitter,
    ) -> NodeTransitionKind | None:
        """Apply one LoopDecision durably; return the loop's terminal transition, or None to re-evaluate."""
        action = decision.action
        iteration = loop.iterations[-1]
        if action is LoopTransitionKind.ADVANCE_BODY_STEP and decision.next_body_step_index is not None:
            return self._advance_body_step(loop, loop_block, decision.next_body_step_index, events)
        if action is LoopTransitionKind.COMPLETE_ITERATION:
            return self._complete_iteration(loop, iteration, events)
        if action is LoopTransitionKind.REPEAT_NEXT_ITERATION and decision.iteration_number is not None:
            return self._repeat_iteration(loop, loop_block, decision.iteration_number, events)
        if action is LoopTransitionKind.TERMINATE_LOOP_PASSED:
            return self._finish_loop(loop, iteration, events, failed=False, diagnostic=None)
        if action is LoopTransitionKind.TERMINATE_LOOP_CEILING:
            failed = loop.on_max_iterations is FailurePolicy.ABORT
            return self._finish_loop(loop, iteration, events, failed=failed, diagnostic=decision.diagnostic)
        return self._resolve_ceiling(loop, loop_block, iteration, decision, events)

    def _advance_body_step(
        self,
        loop: ExecutionLoopNode,
        loop_block: LoopStepBlock,
        body_index: int,
        events: LoopEventEmitter,
    ) -> NodeTransitionKind | None:
        """Advance the body leaf at body_index of the last iteration through _advance_slot; PAUSED returns PAUSED, FAILED fails the loop."""
        iteration = loop.iterations[-1]
        leaf = iteration.steps[body_index]
        self._in_flight = leaf
        transition = self._advance_slot(
            leaf,
            loop_block.do[body_index],
            idx=body_index + 1,
            total=len(loop_block.do),
            loop=loop,
        )
        if transition is NodeTransitionKind.PAUSED:
            return transition
        if transition is NodeTransitionKind.FAILED:
            return self._finish_loop(loop, iteration, events, failed=True, diagnostic=None)
        return None

    def _complete_iteration(
        self, loop: ExecutionLoopNode, iteration: ExecutionIterationRecord, events: LoopEventEmitter
    ) -> NodeTransitionKind | None:
        """Evaluate until over every terminal body result of iteration, persist until_passed with the iteration COMPLETED, and emit conditions_evaluated."""
        conditions = LoopPolicy.evaluate_conditions(loop.until, iteration_results(iteration), iteration.number)
        passed = all(condition.passed for condition in conditions)
        iteration.until_passed = passed
        iteration.state = NodeState.COMPLETED
        boundary = f"after loop '{loop.id}' iteration {iteration.number} conditions evaluated"
        if not self._commit(boundary):
            return NodeTransitionKind.FAILED

        has_next = not passed and iteration.number < loop.iteration_ceiling
        events.conditions_evaluated(conditions, passed, iteration.number + 1 if has_next else None)
        return None

    def _repeat_iteration(
        self, loop: ExecutionLoopNode, loop_block: LoopStepBlock, number: int, events: LoopEventEmitter
    ) -> NodeTransitionKind | None:
        """Append iteration number, mark it RUNNING with iteration_start, and commit; FAILED when the commit fails."""
        iteration = new_iteration(loop_block, number)
        loop.iterations.append(iteration)
        return None if self._begin_iteration(loop, iteration, events) else NodeTransitionKind.FAILED

    def _finish_loop(
        self,
        loop: ExecutionLoopNode,
        iteration: ExecutionIterationRecord,
        events: LoopEventEmitter,
        *,
        failed: bool,
        diagnostic: str | None,
    ) -> NodeTransitionKind:
        """Set the loop terminal, record diagnostic as error or warning, emit done, and commit."""
        if diagnostic is not None:
            (self._errors if failed else self._warnings).append(diagnostic)
        if failed and iteration.state not in TERMINAL_NODE_STATES:
            iteration.state = NodeState.FAILED

        loop.state = NodeState.FAILED if failed else NodeState.COMPLETED
        events.done("failed" if failed else "completed", iteration.number)
        committed = self._commit(f"after loop '{loop.id}' {'aborted' if failed else 'finished'}")
        return NodeTransitionKind.FAILED if failed or not committed else NodeTransitionKind.COMPLETED

    def _resolve_ceiling(
        self,
        loop: ExecutionLoopNode,
        loop_block: LoopStepBlock,
        iteration: ExecutionIterationRecord,
        decision: LoopDecision,
        events: LoopEventEmitter,
    ) -> NodeTransitionKind | None:
        """Apply a prompt_user ceiling: grant three more iterations (None), continue, or abort."""
        self._in_flight = loop
        ceiling = loop.iteration_ceiling
        if self._prompter is None or not self._run.step_coordinator.is_interactive:
            diagnostic = f"Loop '{loop.id}' reached max_iterations ({ceiling}) and run is non-interactive."
            return self._finish_loop(loop, iteration, events, failed=True, diagnostic=diagnostic)

        answer = self._prompter.prompt_loop_max_iterations(
            loop=loop_block,
            iteration=iteration.number,
            diagnostic=decision.diagnostic or "",
            grant_count=_CEILING_GRANT_COUNT,
        )
        if answer is LoopPromptDecision.GRANT:
            loop.granted_iterations += _CEILING_GRANT_COUNT
            return None if self._commit(f"after loop '{loop.id}' ceiling granted") else NodeTransitionKind.FAILED
        if answer is LoopPromptDecision.CONTINUE:
            diagnostic = (
                f"Loop '{loop.id}' reached max_iterations ({ceiling}) without meeting 'until' conditions; continuing."
            )
            return self._finish_loop(loop, iteration, events, failed=False, diagnostic=diagnostic)

        diagnostic = f"Loop '{loop.id}' aborted by user after max_iterations."
        return self._finish_loop(loop, iteration, events, failed=True, diagnostic=diagnostic)

    def _begin_attempt(self, node: ExecutionLeafNode, number: int, run_status: SessionStatus | None = None) -> bool:
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
        if policy == FailurePolicy.PROMPT_USER:
            return self._prompt_failed_leaf(node, step_def, result)

        action, recorded, _ = resolve_terminal_action(policy, result)
        return self._apply_action(node, action, recorded or result, None)

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
        return self._apply_action(node, action, recorded or result, SessionStatus.RUNNING if interactive else None)

    def _persist_paused(self, node: ExecutionLeafNode, result: StepResult) -> bool:
        """Commit the leaf as PAUSED and the run as paused before the prompter is consulted."""
        node.state = NodeState.PAUSED
        return self._commit(f"before prompting for step '{node.id}'", SessionStatus.PAUSED, failed_step_message(result))

    def _apply_action(
        self,
        node: ExecutionLeafNode,
        action: StepAction,
        result: StepResult,
        run_status: SessionStatus | None,
    ) -> NodeTransitionKind:
        """Apply a StepAction: retry begins the next attempt, continue ignores the leaf, abort fails it."""
        if action is StepAction.RETRY:
            if not self._begin_attempt(node, node.attempts[-1].number + 1, run_status=SessionStatus.RUNNING):
                return NodeTransitionKind.FAILED
            return NodeTransitionKind.RETRY
        if action is StepAction.CONTINUE:
            return self._continue_leaf(node, result, run_status)
        return self._abort_leaf(node, result, run_status)

    def _continue_leaf(
        self,
        node: ExecutionLeafNode,
        result: StepResult,
        run_status: SessionStatus | None,
    ) -> NodeTransitionKind:
        """Persist the leaf as IGNORED with its non-fatal result."""
        if not self._finish_leaf(node, NodeState.IGNORED, result, f"after step '{node.id}' continued", run_status):
            return NodeTransitionKind.FAILED
        return NodeTransitionKind.CONTINUED

    def _abort_leaf(
        self,
        node: ExecutionLeafNode,
        result: StepResult,
        run_status: SessionStatus | None,
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
        run_status: SessionStatus | None = None,
    ) -> bool:
        """Set the leaf's terminal state (and replace its last result when given) and commit."""
        node.state = state
        if result is not None:
            node.attempts[-1] = node.attempts[-1].model_copy(update={"result": result})
        return self._commit(boundary, run_status)

    def _commit(
        self,
        boundary: str,
        run_status: SessionStatus | None = None,
        error_message: str | None = None,
    ) -> bool:
        """Save the in-memory state at revision + 1 under the workspace lock; append a boundary-naming error and return False on failure."""
        worktree = self._context.worktree
        with WorkspaceLock(self._context.paths.lock_file):
            saved = self._state_store.save(
                self._run.state,
                run_status=run_status,
                error_message=error_message,
                worktree_id=worktree.session_id if worktree is not None else None,
            )
        if not saved.ok or saved.state is None:
            detail = saved.errors[0] if saved.errors else "state was not saved"
            self._errors.append(f"Failed to persist run state {boundary}: {detail}")
            return False

        self._run.state = saved.state
        self._warnings.extend(saved.warnings)
        return True

    def _step_context(self, loop_iteration: int | None = None) -> dict[str, object] | None:
        """Build the per-step context dict, adding iteration_index when loop_iteration is given."""
        context = self._run.step_coordinator.build_step_context()
        if loop_iteration is None:
            return context
        return {**(context or {}), "iteration_index": loop_iteration}

    def _outcome(self, status: SessionStatus) -> RunOutcome:
        """Build the RunOutcome for status from the flattened state, accumulated errors, warnings, and worktree identity."""
        worktree = self._context.worktree
        return RunOutcome(
            status=status,
            step_results=flatten_step_results(self._loaded.state) if self._loaded is not None else [],
            errors=list(self._errors),
            warnings=list(self._warnings),
            worktree_path=worktree.worktree_path if worktree is not None else self._context.target_dir,
            session_id=self._context.session_id,
            worktree_id=worktree.session_id if worktree is not None else None,
        )
