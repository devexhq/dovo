"""Condition expression evaluator shared by loop 'until', step 'when' and 'if' block conditions."""

from __future__ import annotations

from dataclasses import dataclass

from dovo.core.expressions import ABSENT, InvalidExpression, Path, evaluate_expression, parse_expression
from dovo.core.inputs import resolve_step_output
from dovo.engine.executors.models import ConditionEvaluationResult, StepResult

_STEPS_ROOT = "steps"
_ITERATION_ROOT = "iteration"
_ITERATION_FIELDS = frozenset({"index", "attempt"})


def _resolve_step_field(result: StepResult, field: str, subpath: tuple[str | int, ...]) -> object:
    """Extract a step attribute or its `outputs.<key>[.<path>]` value from a StepResult, or ABSENT when it has none.

    An int segment, an unquoted bracket index, finds nothing.
    """
    if field in ("exit_code", "status", "stdout"):
        return getattr(result, field)
    if field == "outputs" and subpath and isinstance(key := subpath[0], str):
        found = resolve_step_output(result.outputs, result.stdout, key, subpath[1:])
        return ABSENT if found is None else found.value
    return ABSENT


@dataclass(frozen=True, slots=True)
class _StepResultsResolver:
    """Reads a condition's paths: the iteration number, a step's recorded result, and a lone name as its own text."""

    iteration_index: int
    step_results: dict[str, StepResult]

    def resolve(self, path: Path) -> object:
        """Return the value path names in a condition, or ABSENT when it names none."""
        if path.root == _ITERATION_ROOT:
            return self._iteration(path.segments)
        if path.root == _STEPS_ROOT and len(path.segments) >= 2:
            return self._step(path.segments)
        if not path.segments:
            return path.text
        return ABSENT

    def _iteration(self, segments: tuple[str | int, ...]) -> object:
        """Return the iteration index for 'iteration', 'iteration.index' and 'iteration.attempt', else ABSENT."""
        if not segments or (len(segments) == 1 and segments[0] in _ITERATION_FIELDS):
            return self.iteration_index
        return ABSENT

    def _step(self, segments: tuple[str | int, ...]) -> object:
        """Return the field segments name on a step's result, or ABSENT when the step has no result.

        A step id or field that is an int segment, an unquoted bracket index, finds nothing.
        """
        step_id, field = segments[0], segments[1]
        result = self.step_results.get(step_id) if isinstance(step_id, str) else None
        if result is None or not isinstance(field, str):
            return ABSENT
        return _resolve_step_field(result, field, segments[2:])


def evaluate_condition(
    expression: str,
    *,
    iteration_index: int = 1,
    step_results: dict[str, StepResult] | None = None,
) -> ConditionEvaluationResult:
    """Parse and evaluate one condition entry: a comparison, several combined with '&&', '||' and '!', or a value."""
    parsed = parse_expression(expression)
    if isinstance(parsed, InvalidExpression):
        return ConditionEvaluationResult(
            expression=expression,
            passed=False,
            actual=None,
            expected=None,
            detail="FALSE (syntax error)",
        )

    evaluation = evaluate_expression(parsed, _StepResultsResolver(iteration_index, step_results or {}))
    return ConditionEvaluationResult(
        expression=expression,
        passed=evaluation.passed,
        resolved=evaluation.resolved,
        actual=evaluation.actual,
        expected=evaluation.expected,
        detail=evaluation.detail,
    )
