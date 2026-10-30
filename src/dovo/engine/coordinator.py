"""State-driven run coordinator: select the next node, apply one durable transition, repeat."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from dovo.common.diagnostic_log import DiagnosticRecord
from dovo.common.error_codes import ErrorCode
from dovo.common.lock import WorkspaceLock
from dovo.common.models import FailurePolicy
from dovo.common.process import process_registry
from dovo.common.timing import TimeSpan
from dovo.core.agents.models import ResolvedAgentSettings
from dovo.core.catalog.blueprint import Blueprint
from dovo.core.catalog.definitions import LoopStepBlock, StepDefinition, StepType
from dovo.core.catalog.exceptions import BlueprintLoadError, BlueprintValidationError
from dovo.core.db import SessionStatus
from dovo.core.interaction import Interaction, InteractionAnswer, InteractionRequest
from dovo.core.sessions import SessionLogEvent, SessionLogEventType
from dovo.engine.exceptions import EngineError
from dovo.engine.executors import StepExecution
from dovo.engine.executors.agent_step import build_agent_step_runner
from dovo.engine.executors.ask_step import complete_ask_step, resolve_ask_request
from dovo.engine.executors.models import (
    AskResolution,
    ExecutionIdentity,
    PreviousStepMetadata,
    ProgressCallback,
    StepExecutionContext,
    StepResult,
)
from dovo.engine.executors.session_log import append_session_log_event
from dovo.engine.failure import (
    apply_prompt_decision,
    effective_terminal_policy,
    failed_step_message,
    prompt_failure_decision,
    resolve_terminal_action,
)
from dovo.engine.loop_events import LoopEventEmitter
from dovo.engine.loop_policy import LoopDecision, LoopPolicy, LoopTransitionKind
from dovo.engine.models import (
    FailurePrompter,
    LifecycleUpdate,
    LoopPromptDecision,
    RunContext,
    RunObserver,
    RunOutcome,
    RunSettings,
    StepAction,
    StepInvocation,
)
from dovo.engine.notify import safe_notify
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
from dovo.engine.writer import load_blueprint_from_snapshot

_RERUN_WARNING = (
    "Step '{step_id}' was interrupted before it recorded a result and was run again as attempt {number}; "
    "check for side effects its interrupted attempt already made."
)
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
    settings: RunSettings


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
        self._paused_prompt_id: str | None = None
        self._errors: list[str] = []
        self._warnings: list[str] = []
        self._diagnostics: list[DiagnosticRecord] = []
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
    def _interactive(self) -> bool:
        """Return True when the run has a failure prompter and is not running with no_tty."""
        return not self._context.no_tty and self._prompter is not None

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
        if step_def.type is StepType.ASK:
            return self._advance_ask(leaf, step_def, index=idx, total=total, loop=loop)

        if leaf.state is NodeState.PAUSED:
            return self._resolve_failed_leaf(leaf, step_def)
        if leaf.state is NodeState.PENDING and not self._begin_attempt(leaf, 1):
            return NodeTransitionKind.FAILED

        result = self._run_attempt(self._invocation(leaf, step_def, idx=idx, total=total, loop=loop))
        return self._settle_attempt(leaf, step_def, result)

    def _advance_ask(
        self,
        leaf: ExecutionLeafNode,
        step_def: StepDefinition,
        *,
        index: int,
        total: int,
        loop: ExecutionLoopNode | None,
    ) -> NodeTransitionKind:
        """Run, re-ask or fall back to the failure gate for an ask leaf depending on its state and pending_ask."""
        if leaf.state is NodeState.PAUSED:
            if leaf.pending_ask is None:
                return self._resolve_failed_leaf(leaf, step_def)
            if Interaction.request_errors(leaf.pending_ask):
                return self._fail_invalid_pending_ask(leaf)
            invocation = self._invocation(leaf, step_def, idx=index, total=total, loop=loop)
            return self._ask_pending(leaf, leaf.pending_ask, invocation)

        if leaf.state is NodeState.PENDING and not self._begin_attempt(leaf, 1):
            return NodeTransitionKind.FAILED
        return self._start_ask(leaf, self._invocation(leaf, step_def, idx=index, total=total, loop=loop))

    def _start_ask(self, leaf: ExecutionLeafNode, invocation: StepInvocation) -> NodeTransitionKind:
        """Resolve the request of a fresh ask attempt and either settle it without a prompt or persist and ask it."""
        resolution = self._resolve_ask(invocation)
        if resolution.result is not None:
            return self._settle_attempt(leaf, invocation.step, resolution.result)
        if resolution.request is None:
            self._errors.append(f"Ask step '{leaf.id}' resolved neither a request nor a result.")
            return NodeTransitionKind.FAILED
        return self._ask_pending(leaf, resolution.request, invocation)

    def _ask_pending(
        self,
        leaf: ExecutionLeafNode,
        request: InteractionRequest,
        invocation: StepInvocation,
    ) -> NodeTransitionKind:
        """Persist the pending ask, read an answer, and apply it or pause the run at the prompt."""
        if not self._persist_pending_ask(leaf, request):
            return NodeTransitionKind.FAILED

        answer = self._read_answer(request)
        if answer is None:
            return self._pause_at_prompt(leaf)
        return self._apply_answer(leaf, request, answer, invocation)

    def _persist_pending_ask(self, leaf: ExecutionLeafNode, request: InteractionRequest) -> bool:
        """Commit the leaf as PAUSED with pending_ask and the run as PAUSED before any input is read."""
        leaf.pending_ask = request
        leaf.state = NodeState.PAUSED
        return self._commit(f"before asking step '{leaf.id}'", SessionStatus.PAUSED)

    def _read_answer(self, request: InteractionRequest) -> InteractionAnswer | None:
        """Ask the interaction prompter; None for no prompter, an unanswered or invalid answer, or KeyboardInterrupt."""
        prompter = self._context.interaction_prompter
        if prompter is None:
            return None

        try:
            answer = prompter.ask(request)
        except KeyboardInterrupt:
            return None
        if answer is None:
            return None

        validated = Interaction.validate(request, answer)
        if validated.answer is None:
            self._warnings.append(f"Ignored an invalid answer for prompt '{request.step_id}': {validated.errors[0]}")
            return None
        return validated.answer

    def _apply_answer(
        self,
        leaf: ExecutionLeafNode,
        request: InteractionRequest,
        answer: InteractionAnswer,
        invocation: StepInvocation,
    ) -> NodeTransitionKind:
        """Move pending_ask onto the attempt, commit the run back to RUNNING, then settle the answered result through the normal attempt path."""
        result = self._complete_ask(invocation, request, answer)
        leaf.attempts[-1] = leaf.attempts[-1].model_copy(
            update={"span": leaf.attempts[-1].span.close(), "result": result, "ask": request}
        )
        leaf.pending_ask = None
        leaf.state = NodeState.RUNNING
        if not self._commit(f"after prompt '{leaf.id}' answered", SessionStatus.RUNNING):
            return NodeTransitionKind.FAILED
        return self._settle_attempt(leaf, invocation.step, result)

    def _invocation(
        self,
        leaf: ExecutionLeafNode,
        step_def: StepDefinition,
        *,
        idx: int,
        total: int,
        loop: ExecutionLoopNode | None,
    ) -> StepInvocation:
        """Describe leaf's current attempt at 1-based idx of total, attributed to loop's last iteration, with every terminal step so far."""
        steps = tuple(terminal_step_metadata(self._run.state))
        return StepInvocation(
            step=step_def,
            idx=idx,
            total=total,
            attempt=leaf.attempts[-1].number,
            loop_id=loop.id if loop is not None else None,
            loop_iteration=loop.iterations[-1].number if loop is not None else None,
            previous_step=steps[-1] if steps else PreviousStepMetadata(),
            steps=steps,
        )

    def _run_attempt(self, invocation: StepInvocation) -> StepResult:
        """Notify and log the start, run one StepExecution with observer-forwarding callbacks, notify and log the done, and append the result's warnings."""
        self._log_step_start(invocation)
        observer = self._run.settings.observer
        step, idx, total = invocation.step, invocation.idx, invocation.total
        on_output = (
            (
                lambda stream_name, line: safe_notify(
                    observer, "on_step_output", idx, total, step, line, stream=stream_name
                )
            )
            if observer is not None
            else None
        )
        on_progress = (
            (lambda update: safe_notify(observer, "on_step_progress", idx, total, step, update))
            if observer is not None
            else None
        )
        result = StepExecution(self._execution_context(invocation, on_output=on_output, on_progress=on_progress)).run()
        self._log_step_done(invocation, result)
        self._warnings.extend(result.warnings)
        return result

    def _resolve_ask(self, invocation: StepInvocation) -> AskResolution:
        """Notify and log the ask's start and resolve its request; when it settles without a prompt, notify and log the done and append its warnings."""
        self._log_step_start(invocation)
        resolution = resolve_ask_request(self._execution_context(invocation))
        if resolution.result is not None:
            self._log_step_done(invocation, resolution.result)
            self._warnings.extend(resolution.result.warnings)
        return resolution

    def _complete_ask(
        self, invocation: StepInvocation, request: InteractionRequest, answer: InteractionAnswer
    ) -> StepResult:
        """Build the answered ask's result, notify and log the done, and append its warnings."""
        result = complete_ask_step(self._execution_context(invocation), request, answer)
        self._log_step_done(invocation, result)
        self._warnings.extend(result.warnings)
        return result

    def _execution_context(
        self,
        invocation: StepInvocation,
        on_output: Callable[[str, str], None] | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> StepExecutionContext:
        """Build the StepExecutionContext for invocation, with an agent runner for every step type except ask and the run's diagnostics list for the step's records."""
        settings = self._run.settings
        session_dir = self._context.paths.session_dir(self._context.session_id)
        return StepExecutionContext(
            step=invocation.step,
            worktree_path=self._context.target_dir,
            inputs=settings.inputs or None,
            on_output=on_output,
            on_progress=on_progress,
            step_index=invocation.idx,
            initial_attempt=invocation.attempt,
            identity=settings.identity,
            previous_step=invocation.previous_step,
            steps=invocation.steps,
            session_tmp_dir=self._context.session_tmp_dir,
            session_log_dir=self._context.session_log_dir,
            save_attempt_logs=self._context.save_attempt_logs,
            loop_iteration=invocation.loop_iteration,
            loop_id=invocation.loop_id,
            session_id=self._context.session_id,
            artifact_store=self._context.artifact_store,
            paths=settings.paths,
            agent_runner=None
            if invocation.step.type == StepType.ASK
            else build_agent_step_runner(
                settings.agent,
                self._context.worktree is not None,
                session_tmp_dir=self._context.session_tmp_dir,
                main_checkout=settings.paths.root_dir,
                session_log_dir=self._context.session_log_dir,
                session_dir=session_dir,
            ),
            sensitive_variables=settings.sensitive_variables,
            diagnostics=self._diagnostics,
        )

    def _log_step_start(self, invocation: StepInvocation) -> None:
        """Notify the observer and append STEP_START for invocation's attempt."""
        step = invocation.step
        safe_notify(self._run.settings.observer, "on_step_start", invocation.idx, invocation.total, step)
        append_session_log_event(
            self._context.session_log_dir,
            SessionLogEvent(
                event=SessionLogEventType.STEP_START,
                step_index=invocation.idx,
                step_id=step.id,
                step_name=step.name,
                attempt=invocation.attempt,
                loop_id=invocation.loop_id,
                iteration=invocation.loop_iteration,
            ),
        )

    def _log_step_done(self, invocation: StepInvocation, result: StepResult) -> None:
        """Notify the observer and append STEP_DONE for the settled result."""
        step = invocation.step
        safe_notify(self._run.settings.observer, "on_step_done", invocation.idx, invocation.total, step, result)
        append_session_log_event(
            self._context.session_log_dir,
            SessionLogEvent(
                event=SessionLogEventType.STEP_DONE,
                step_index=invocation.idx,
                step_id=step.id,
                step_name=step.name,
                attempt=result.attempts,
                status=result.status,
                exit_code=result.exit_code,
                duration_seconds=result.duration_seconds,
                loop_id=invocation.loop_id,
                iteration=invocation.loop_iteration,
            ),
        )

    def _pause_at_prompt(self, leaf: ExecutionLeafNode) -> NodeTransitionKind:
        """Record the leaf's id as the paused prompt and return PAUSED without recording any result."""
        self._paused_prompt_id = leaf.id
        return NodeTransitionKind.PAUSED

    def _fail_invalid_pending_ask(self, leaf: ExecutionLeafNode) -> NodeTransitionKind:
        """Fail the leaf with "Pending ask for '<id>' is invalid." and commit."""
        self._errors.append(f"Pending ask for '{leaf.id}' is invalid.")
        leaf.pending_ask = None
        leaf.state = NodeState.FAILED
        self._commit(f"after invalid pending ask for step '{leaf.id}'")
        return NodeTransitionKind.FAILED

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
        """Terminate child processes, persist a non-terminal in-flight node as cancelled, and return the CANCELLED outcome."""
        process_registry.terminate_all(grace_seconds=0.5)
        if self._in_flight is not None and self._in_flight.state not in TERMINAL_NODE_STATES:
            self._in_flight.state = NodeState.CANCELLED
            self._commit("while cancelling the run")
        self._errors = ["Execution cancelled by user."]
        return self._outcome(SessionStatus.CANCELLED)

    def _start(self) -> bool:
        """Load state, session row, and snapshot definitions and resolve the run settings; append the failure to _errors and return False when unavailable."""
        session_id = self._context.session_id
        try:
            state = self._state_store.load(self._warnings)
        except EngineError as error:
            self._errors.append(error.message)
            return False

        row = self._state_store.sessions.get(session_id)
        if row is None:
            self._errors.append(f"Session '{session_id}' not found.")
            return False

        try:
            blueprint = load_blueprint_from_snapshot(self._context.paths.session_dir(session_id), state.manifest)
        except (EngineError, BlueprintLoadError, BlueprintValidationError) as exc:
            if isinstance(exc, EngineError) and exc.error_code is not ErrorCode.SESSION_SNAPSHOT_MISSING:
                raise
            self._errors.append(str(exc))
            return False

        structure_errors = validate_loop_structure(state, blueprint)
        if structure_errors:
            self._errors.append(f"Execution state for session '{session_id}' is corrupt: {structure_errors[0]}")
            return False

        settings = RunSettings(
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
            sensitive_variables=self._context.sensitive_variables,
            paths=self._context.paths,
            git=self._context.git,
            clock=self._context.clock,
            new_id=self._context.new_id,
        )
        self._loaded = _LoadedRun(state=state, blueprint=blueprint, settings=settings)
        return True

    def _recover_interrupted(self) -> bool:
        """Replace the open attempt of every RUNNING leaf with a fresh open attempt of the same number, keep the leaf RUNNING so it runs again, and warn once per leaf."""
        recovered = False
        for node in iter_leaves(self._run.state):
            if node.state is not NodeState.RUNNING:
                continue
            if not node.attempts or node.attempts[-1].result is not None:
                continue

            number = node.attempts[-1].number
            node.attempts[-1] = StepAttemptRecord(number=number, span=TimeSpan.open())
            self._warnings.append(_RERUN_WARNING.format(step_id=node.id, number=number))
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
        """Apply a prompt_user ceiling: grant three more iterations (None), continue, abort, or pause the run when the prompt is interrupted."""
        self._in_flight = loop
        ceiling = loop.iteration_ceiling
        if self._prompter is None or not self._interactive:
            diagnostic = f"Loop '{loop.id}' reached max_iterations ({ceiling}) and run is non-interactive."
            return self._finish_loop(loop, iteration, events, failed=True, diagnostic=diagnostic)

        diagnostic = decision.diagnostic or ""
        try:
            answer = self._prompter.prompt_loop_max_iterations(
                loop=loop_block,
                iteration=iteration.number,
                diagnostic=diagnostic,
                grant_count=_CEILING_GRANT_COUNT,
            )
        except KeyboardInterrupt:
            self._errors.append(diagnostic)
            boundary = f"after loop '{loop.id}' ceiling prompt interrupted"
            paused = self._commit(boundary, SessionStatus.PAUSED, diagnostic)
            return NodeTransitionKind.PAUSED if paused else NodeTransitionKind.FAILED
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
        """Append an attempt with an open span, mark the node RUNNING, and commit before the attempt executes."""
        node.attempts.append(StepAttemptRecord(number=number, span=TimeSpan.open()))
        node.state = NodeState.RUNNING
        return self._commit(f"before step '{node.id}' started", run_status)

    def _settle_attempt(
        self,
        node: ExecutionLeafNode,
        step_def: StepDefinition,
        result: StepResult,
    ) -> NodeTransitionKind:
        """Close the attempt's span and record its result, commit terminal success, or hand a failed result to the failure gate."""
        attempt = node.attempts[-1]
        node.attempts[-1] = attempt.model_copy(
            update={"span": attempt.span.close(), "number": result.attempts, "result": result}
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
        interactive = self._interactive
        if interactive and not self._persist_paused(node, result):
            return NodeTransitionKind.FAILED

        try:
            decision, warning = prompt_failure_decision(self._prompter, step_def, result, no_tty=self._context.no_tty)
        except KeyboardInterrupt:
            self._errors.append(failed_step_message(result))
            return NodeTransitionKind.PAUSED

        if warning is not None:
            self._warnings.append(warning)
        action, recorded, _ = apply_prompt_decision(decision, result)
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
            try:
                saved = self._state_store.save(
                    self._run.state,
                    self._warnings,
                    LifecycleUpdate(
                        run_status=run_status,
                        error_message=error_message,
                        worktree_id=worktree.session_id if worktree is not None else None,
                    ),
                )
            except EngineError as error:
                self._errors.append(f"Failed to persist run state {boundary}: {error.message}")
                return False

        self._run.state = saved
        return True

    def _outcome(self, status: SessionStatus) -> RunOutcome:
        """Build the RunOutcome for status from the flattened state, accumulated errors, warnings, recorded diagnostics, and worktree identity."""
        worktree = self._context.worktree
        return RunOutcome(
            status=status,
            step_results=flatten_step_results(self._loaded.state) if self._loaded is not None else [],
            errors=list(self._errors),
            warnings=list(self._warnings),
            diagnostics=list(self._diagnostics),
            worktree_path=worktree.worktree_path if worktree is not None else self._context.target_dir,
            session_id=self._context.session_id,
            worktree_id=worktree.session_id if worktree is not None else None,
            paused_prompt_id=self._paused_prompt_id if status is SessionStatus.PAUSED else None,
        )
