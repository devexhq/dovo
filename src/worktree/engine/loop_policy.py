"""Pure loop policy: decide the next loop transition from durable state without executing steps or writing state."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from worktree.common.models import FailurePolicy
from worktree.core.catalog.definitions import StepDefinition
from worktree.engine.executors import evaluate_condition
from worktree.engine.executors.models import ConditionEvaluationResult, StepResult
from worktree.engine.state_models import (
    TERMINAL_NODE_STATES,
    ExecutionIterationRecord,
    ExecutionLoopNode,
)


class LoopTransitionKind(StrEnum):
    """Next transition the coordinator applies to a loop."""

    ADVANCE_BODY_STEP = "advance_body_step"
    COMPLETE_ITERATION = "complete_iteration"
    REPEAT_NEXT_ITERATION = "repeat_next_iteration"
    TERMINATE_LOOP_PASSED = "terminate_loop_passed"
    TERMINATE_LOOP_CEILING = "terminate_loop_ceiling"
    PROMPT_CEILING = "prompt_ceiling"


@dataclass(frozen=True)
class LoopDecision:
    """A typed loop transition with the data the coordinator needs to apply it."""

    action: LoopTransitionKind
    next_body_step_index: int | None = None
    iteration_number: int | None = None
    diagnostic: str | None = None


class LoopPolicy:
    """Stateless loop decisions over the execution-state tree."""

    @staticmethod
    def evaluate_iteration(
        loop_node: ExecutionLoopNode,
        iteration: ExecutionIterationRecord,
        step_defs: Sequence[StepDefinition],
    ) -> LoopDecision:
        """Determines whether the iteration should advance its next body step, complete, or terminate."""
        for body_index, (_, leaf) in enumerate(zip(step_defs, iteration.steps, strict=False)):
            if leaf.state not in TERMINAL_NODE_STATES:
                return LoopDecision(LoopTransitionKind.ADVANCE_BODY_STEP, next_body_step_index=body_index)

        if iteration.until_passed is None:
            return LoopDecision(LoopTransitionKind.COMPLETE_ITERATION, iteration_number=iteration.number)
        if iteration.until_passed:
            return LoopDecision(LoopTransitionKind.TERMINATE_LOOP_PASSED, iteration_number=iteration.number)
        if iteration.number < loop_node.iteration_ceiling:
            return LoopDecision(LoopTransitionKind.REPEAT_NEXT_ITERATION, iteration_number=iteration.number + 1)
        return LoopPolicy.handle_ceiling(loop_node, loop_node.on_max_iterations)

    @staticmethod
    def evaluate_conditions(
        until_expressions: Sequence[str],
        iteration_results: dict[str, StepResult],
        iteration_index: int,
    ) -> list[ConditionEvaluationResult]:
        """Evaluate every `until` expression against the iteration's step results, in declaration order."""
        return [
            evaluate_condition(expression, iteration_index=iteration_index, step_results=iteration_results)
            for expression in until_expressions
        ]

    @staticmethod
    def handle_ceiling(
        loop_node: ExecutionLoopNode,
        policy: FailurePolicy,
    ) -> LoopDecision:
        """Applies on_max_iterations escalation (abort, continue, or prompt_user)."""
        iteration_number = loop_node.iterations[-1].number
        ceiling = loop_node.iteration_ceiling
        if policy is FailurePolicy.ABORT:
            diagnostic = f"Loop '{loop_node.id}' reached max_iterations ({ceiling}) without meeting 'until' conditions."
            return LoopDecision(
                LoopTransitionKind.TERMINATE_LOOP_CEILING, iteration_number=iteration_number, diagnostic=diagnostic
            )
        if policy is FailurePolicy.CONTINUE:
            diagnostic = (
                f"Loop '{loop_node.id}' reached max_iterations ({ceiling}) "
                "without meeting 'until' conditions; continuing."
            )
            return LoopDecision(
                LoopTransitionKind.TERMINATE_LOOP_CEILING, iteration_number=iteration_number, diagnostic=diagnostic
            )

        diagnostic = f"Reached max_iterations ({ceiling}) without meeting 'until' conditions."
        return LoopDecision(LoopTransitionKind.PROMPT_CEILING, iteration_number=iteration_number, diagnostic=diagnostic)
