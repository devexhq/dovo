"""Condition expression evaluator shared by loop 'until', step 'when' and 'if' block conditions.

An `artifacts.<name>.<field>[.<path>]` operand reads the latest revision of the artifact in the session, every field
as a string; a path after `content` reads the content as JSON, as a recorded output is read. It has no value, never
an error, when the run has no artifact store, the artifact is not yet published, its content cannot be inlined, the
content is not JSON under a path, or a path follows `revision`, `digest` or `dir`. A function given such a value of
the wrong type finds nothing too.
"""

from __future__ import annotations

from dataclasses import dataclass

from dovo.core.artifacts.store import ArtifactStore
from dovo.core.expressions import ABSENT, InvalidExpression, Path, evaluate_expression, parse_expression
from dovo.core.inputs import ArtifactReference, artifact_reference, read_json_path, resolve_step_output
from dovo.engine.executors.models import ConditionEvaluationResult, StepRecord

_STEPS_ROOT = "steps"
_ITERATION_ROOT = "iteration"
_ARTIFACTS_ROOT = "artifacts"
_CONTENT_FIELD = "content"
_ITERATION_FIELDS = frozenset({"index", "attempt"})


def _resolve_step_field(result: StepRecord, field: str, subpath: tuple[str | int, ...]) -> object:
    """Extract a step attribute or its `outputs.<key>[.<path>]` value from a StepRecord, or ABSENT when it has none.

    error_code is the recorded code's text, '' when the step recorded none. An int segment, an unquoted bracket index,
    finds nothing.
    """
    if field in ("exit_code", "status", "stdout"):
        return getattr(result, field)
    if field == "error_code":
        return result.error_code.value if result.error_code is not None else ""
    if field == "outputs" and subpath and isinstance(key := subpath[0], str):
        found = resolve_step_output(result.outputs, result.stdout, key, subpath[1:])
        return ABSENT if found is None else found.value
    return ABSENT


def _read_field(store: ArtifactStore, reference: ArtifactReference) -> str | None:
    """Return the field of the artifact's latest revision in the session, or None when it is unpublished or cannot be inlined."""
    references = store.resolve_references({reference.name: {reference.field}})
    if not references.ok:
        return None
    return references.references.get(reference.name, {}).get(reference.field)


@dataclass(frozen=True, slots=True)
class _ConditionResolver:
    """Reads a condition's paths: the iteration number, a step's recorded result, an artifact, and a lone name as its own text."""

    iteration_index: int
    step_results: dict[str, StepRecord]
    artifact_store: ArtifactStore | None

    def resolve(self, path: Path) -> object:
        """Return the value path names in a condition, or ABSENT when it names none."""
        if path.root == _ITERATION_ROOT:
            return self._iteration(path.segments)
        if path.root == _STEPS_ROOT and len(path.segments) >= 2:
            return self._step(path.segments)
        if path.root == _ARTIFACTS_ROOT:
            return self._artifact(path.segments)
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

    def _artifact(self, segments: tuple[str | int, ...]) -> object:
        """Return the artifact field segments name, read through its path when the field is `content`, or ABSENT when it has none."""
        reference = artifact_reference(segments)
        if reference is None or self.artifact_store is None:
            return ABSENT
        if reference.path and reference.field != _CONTENT_FIELD:
            return ABSENT
        value = _read_field(self.artifact_store, reference)
        if value is None:
            return ABSENT
        if not reference.path:
            return value
        found = read_json_path(value, reference.path)
        return ABSENT if found is None else found.value


def evaluate_condition(
    expression: str,
    *,
    iteration_index: int = 1,
    step_results: dict[str, StepRecord] | None = None,
    artifact_store: ArtifactStore | None = None,
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

    evaluation = evaluate_expression(parsed, _ConditionResolver(iteration_index, step_results or {}, artifact_store))
    return ConditionEvaluationResult(
        expression=expression,
        passed=evaluation.passed,
        resolved=evaluation.resolved,
        actual=evaluation.actual,
        expected=evaluation.expected,
        detail=evaluation.detail,
    )
