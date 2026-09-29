"""Contract tests for LoopPolicy: pure loop transition decisions over the execution-state tree."""

from __future__ import annotations

import pytest

from tests.harness.builders import StepBuilder
from worktree.common.models import FailurePolicy
from worktree.core.engine.loop_policy import LoopDecision, LoopPolicy, LoopTransitionKind
from worktree.core.engine.state_models import (
    ExecutionIterationRecord,
    ExecutionLeafNode,
    ExecutionLoopNode,
    NodeState,
)
from worktree.core.step.models import StepDefinition, StepResult

_BODY: list[StepDefinition] = [StepBuilder.command("true").with_id(step_id).build() for step_id in ("a", "b", "c")]


def _result(step_id: str, exit_code: int = 0) -> StepResult:
    return StepResult(
        step_id=step_id,
        status="completed" if exit_code == 0 else "failed",
        exit_code=exit_code,
        stdout="",
        stderr="",
        duration_seconds=0.0,
    )


def _iteration(
    states: list[NodeState],
    *,
    number: int = 1,
    until_passed: bool | None = None,
) -> ExecutionIterationRecord:
    return ExecutionIterationRecord(
        number=number,
        state=NodeState.RUNNING,
        steps=[ExecutionLeafNode(id=step.id, state=state) for step, state in zip(_BODY, states, strict=True)],
        until_passed=until_passed,
    )


def _loop(
    iterations: list[ExecutionIterationRecord],
    *,
    max_iterations: int = 3,
    granted_iterations: int = 0,
    on_max_iterations: FailurePolicy = FailurePolicy.PROMPT_USER,
) -> ExecutionLoopNode:
    return ExecutionLoopNode(
        id="l",
        max_iterations=max_iterations,
        granted_iterations=granted_iterations,
        on_max_iterations=on_max_iterations,
        state=NodeState.RUNNING,
        iterations=iterations,
    )


_DONE = [NodeState.COMPLETED, NodeState.COMPLETED, NodeState.COMPLETED]


class LoopPolicyEvaluateIterationTests:
    """[tier-1/unit] LoopPolicy.evaluate_iteration: choose the next loop transition from the last iteration."""

    @pytest.mark.parametrize(
        ("states", "expected_index"),
        [
            pytest.param([NodeState.COMPLETED, NodeState.PAUSED, NodeState.PENDING], 1, id="paused-second"),
            pytest.param([NodeState.COMPLETED, NodeState.IGNORED, NodeState.RUNNING], 2, id="running-third"),
            pytest.param([NodeState.PENDING, NodeState.PENDING, NodeState.PENDING], 0, id="all-pending"),
        ],
    )
    def test_first_non_terminal_body_leaf_returns_advance_with_its_index(
        self, states: list[NodeState], expected_index: int
    ) -> None:
        """[tier-1/unit] LoopPolicy.evaluate_iteration: body leaf states [completed, paused, pending] -> ADVANCE_BODY_STEP index 1; [completed, ignored, running] -> index 2; [pending, pending, pending] -> index 0."""
        iteration = _iteration(states)

        decision = LoopPolicy.evaluate_iteration(_loop([iteration]), iteration, _BODY)

        assert decision == LoopDecision(LoopTransitionKind.ADVANCE_BODY_STEP, next_body_step_index=expected_index)

    def test_all_terminal_without_until_result_returns_complete_iteration(self) -> None:
        """[tier-1/unit] LoopPolicy.evaluate_iteration: every leaf terminal and until_passed None -> COMPLETE_ITERATION with iteration_number == iteration.number."""
        iteration = _iteration(_DONE, number=2)

        decision = LoopPolicy.evaluate_iteration(_loop([iteration]), iteration, _BODY)

        assert decision == LoopDecision(LoopTransitionKind.COMPLETE_ITERATION, iteration_number=2)

    def test_until_passed_true_returns_terminate_loop_passed(self) -> None:
        """[tier-1/unit] LoopPolicy.evaluate_iteration: until_passed True -> TERMINATE_LOOP_PASSED, no next_body_step_index."""
        iteration = _iteration(_DONE, until_passed=True)

        decision = LoopPolicy.evaluate_iteration(_loop([iteration]), iteration, _BODY)

        assert decision == LoopDecision(LoopTransitionKind.TERMINATE_LOOP_PASSED, iteration_number=1)

    def test_until_failed_below_ceiling_returns_repeat_with_next_number(self) -> None:
        """[tier-1/unit] LoopPolicy.evaluate_iteration: until_passed False, iteration.number 1 of max_iterations 3 (granted_iterations 0) -> REPEAT_NEXT_ITERATION with iteration_number 2."""
        iteration = _iteration(_DONE, until_passed=False)

        decision = LoopPolicy.evaluate_iteration(_loop([iteration]), iteration, _BODY)

        assert decision == LoopDecision(LoopTransitionKind.REPEAT_NEXT_ITERATION, iteration_number=2)

    def test_until_failed_at_ceiling_delegates_to_handle_ceiling(self) -> None:
        """[tier-1/unit] LoopPolicy.evaluate_iteration: until_passed False on iteration 3 of max_iterations 2 with granted_iterations 1 and on_max_iterations prompt_user -> PROMPT_CEILING; on iteration 2 of the same loop -> REPEAT_NEXT_ITERATION."""
        second = _iteration(_DONE, number=2, until_passed=False)
        third = _iteration(_DONE, number=3, until_passed=False)
        loop = _loop([second, third], max_iterations=2, granted_iterations=1)

        at_ceiling = LoopPolicy.evaluate_iteration(loop, third, _BODY)
        below_ceiling = LoopPolicy.evaluate_iteration(loop, second, _BODY)

        assert at_ceiling.action is LoopTransitionKind.PROMPT_CEILING
        assert below_ceiling.action is LoopTransitionKind.REPEAT_NEXT_ITERATION


class LoopPolicyHandleCeilingTests:
    """[tier-1/unit] LoopPolicy.handle_ceiling: map the on_max_iterations policy to a ceiling decision."""

    @pytest.mark.parametrize(
        ("policy", "action", "diagnostic"),
        [
            pytest.param(
                FailurePolicy.ABORT,
                LoopTransitionKind.TERMINATE_LOOP_CEILING,
                "Loop 'l' reached max_iterations (2) without meeting 'until' conditions.",
                id="abort",
            ),
            pytest.param(
                FailurePolicy.CONTINUE,
                LoopTransitionKind.TERMINATE_LOOP_CEILING,
                "Loop 'l' reached max_iterations (2) without meeting 'until' conditions; continuing.",
                id="continue",
            ),
            pytest.param(
                FailurePolicy.PROMPT_USER,
                LoopTransitionKind.PROMPT_CEILING,
                "Reached max_iterations (2) without meeting 'until' conditions.",
                id="prompt-user",
            ),
        ],
    )
    def test_policy_maps_to_ceiling_decision_with_exact_diagnostic(
        self, policy: FailurePolicy, action: LoopTransitionKind, diagnostic: str
    ) -> None:
        """[tier-1/unit] LoopPolicy.handle_ceiling: abort -> TERMINATE_LOOP_CEILING with "Loop 'l' reached max_iterations (2) without meeting 'until' conditions."; continue -> TERMINATE_LOOP_CEILING with the same text ending "; continuing."; prompt_user -> PROMPT_CEILING with "Reached max_iterations (2) without meeting 'until' conditions."; iteration_number == last iteration number."""
        loop = _loop(
            [_iteration(_DONE, number=1, until_passed=False), _iteration(_DONE, number=2, until_passed=False)],
            max_iterations=2,
        )

        decision = LoopPolicy.handle_ceiling(loop, policy)

        assert decision == LoopDecision(action, iteration_number=2, diagnostic=diagnostic)


class LoopPolicyEvaluateConditionsTests:
    """[tier-1/unit] LoopPolicy.evaluate_conditions: per-expression until results over an iteration's step results."""

    def test_evaluate_conditions_returns_one_result_per_expression_in_order(self) -> None:
        """[tier-1/unit] LoopPolicy.evaluate_conditions: two expressions over results {a: exit 0, b: exit 1} return [passed True, passed False] with matching `expression` fields."""
        expressions = ["steps.a.exit_code == 0", "steps.b.exit_code == 0"]

        results = LoopPolicy.evaluate_conditions(expressions, {"a": _result("a"), "b": _result("b", 1)}, 1)

        assert [(result.expression, result.passed) for result in results] == [
            ("steps.a.exit_code == 0", True),
            ("steps.b.exit_code == 0", False),
        ]

    def test_expression_referencing_absent_step_returns_failed_result(self) -> None:
        """[tier-1/unit] LoopPolicy.evaluate_conditions: an expression referencing a step id missing from the results returns one result with passed False; `iteration.index >= 2` with iteration_index 2 returns passed True."""
        missing = LoopPolicy.evaluate_conditions(["steps.ghost.exit_code == 0"], {"a": _result("a")}, 1)
        by_index = LoopPolicy.evaluate_conditions(["iteration.index >= 2"], {}, 2)

        assert [result.passed for result in missing] == [False]
        assert [result.passed for result in by_index] == [True]
