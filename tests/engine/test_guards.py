"""Contract tests for the pure `when` guard decision."""

from __future__ import annotations

import json

import pytest

from dovo.engine.executors.models import StepResult
from dovo.engine.guards import evaluate_guard, guard_skip
from dovo.engine.models import SkipCause
from dovo.engine.state_models import StepSkip

_CHECK = StepResult(
    step_id="check",
    status="completed",
    exit_code=0,
    stdout=json.dumps({"verdict": "ready", "note": None}),
    stderr="",
    duration_seconds=0.0,
)


class GuardSkipTests:
    @pytest.mark.parametrize(
        ("expressions", "skip"),
        [
            pytest.param(["steps.check.exit_code == 0", 'steps.check.outputs.verdict == "ready"'], None, id="all_true"),
            pytest.param(
                ["steps.check.exit_code == 0", "steps.check.exit_code == 1", "steps.gone.exit_code == 0"],
                StepSkip(cause=SkipCause.WHEN_FALSE, expression="steps.check.exit_code == 1"),
                id="first_false",
            ),
            pytest.param(
                ["steps.gone.exit_code == 0"],
                StepSkip(cause=SkipCause.WHEN_UNRESOLVED, expression="steps.gone.exit_code == 0"),
                id="unresolved_missing_step",
            ),
            pytest.param(
                ["steps.check.outputs.count > 0"],
                StepSkip(cause=SkipCause.WHEN_UNRESOLVED, expression="steps.check.outputs.count > 0"),
                id="unresolved_missing_field",
            ),
            pytest.param(
                ['steps.check.outputs.note == "x"'],
                StepSkip(cause=SkipCause.WHEN_FALSE, expression='steps.check.outputs.note == "x"'),
                id="json_null_is_false_not_unresolved",
            ),
        ],
    )
    def test_skip_names_the_first_failing_expression_and_its_cause(
        self, expressions: list[str], skip: StepSkip | None
    ) -> None:
        assert guard_skip(evaluate_guard(expressions, {"check": _CHECK})) == skip

    def test_iteration_operand_uses_the_given_iteration_index(self) -> None:
        results = evaluate_guard(["iteration.index > 1"], {}, iteration_index=2)

        assert [result.passed for result in results] == [True]
