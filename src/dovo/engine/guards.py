"""Pure guard decisions: whether a `when` list skips a step or loop, and which branch an `if` block takes."""

from __future__ import annotations

from collections.abc import Sequence

from dovo.engine.executors import evaluate_condition
from dovo.engine.executors.models import ConditionEvaluationResult, StepResult
from dovo.engine.models import IfBranch, SkipCause
from dovo.engine.state_models import StepSkip


def evaluate_guard(
    expressions: Sequence[str], step_results: dict[str, StepResult], iteration_index: int = 1
) -> list[ConditionEvaluationResult]:
    """Evaluate every expression against step_results, in the order written, with the evaluator loop `until` uses."""
    return [
        evaluate_condition(expression, iteration_index=iteration_index, step_results=step_results)
        for expression in expressions
    ]


def guard_skip(results: Sequence[ConditionEvaluationResult]) -> StepSkip | None:
    """Return the skip the first failing expression causes, or None when every expression passed."""
    failing = next((result for result in results if not result.passed), None)
    if failing is None:
        return None
    cause = SkipCause.WHEN_FALSE if failing.resolved else SkipCause.WHEN_UNRESOLVED
    return StepSkip(cause=cause, expression=failing.expression)


def choose_branch(results: Sequence[ConditionEvaluationResult], *, has_else: bool) -> IfBranch:
    """Return `then` when every expression passed, else `else` when the block has one, else `none`."""
    if all(result.passed for result in results):
        return IfBranch.THEN
    return IfBranch.ELSE if has_else else IfBranch.NONE
