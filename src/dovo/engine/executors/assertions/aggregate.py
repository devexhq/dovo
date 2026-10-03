"""Aggregate evaluator for a step's full ``assert`` block."""

from __future__ import annotations

from pathlib import Path

from dovo.core.catalog.definitions import StepAssert
from dovo.engine.executors.assertions.filesystem import (
    evaluate_file_exists,
    evaluate_file_not_empty,
    evaluate_file_not_exists,
)
from dovo.engine.executors.assertions.json_match import evaluate_json_match
from dovo.engine.executors.assertions.process import (
    evaluate_exit_code,
    evaluate_output_contains,
    evaluate_output_not_contains,
    evaluate_regex_match,
)
from dovo.engine.executors.models import AssertionResult


def evaluate_assertions(
    assert_config: StepAssert,
    *,
    exit_code: int,
    stdout: str,
    stderr: str,
    worktree_path: Path,
) -> AssertionResult:
    """Run every configured assertion and return one structured result.

    ``exit_code`` is always evaluated; when ``assert_config.exit_code`` is
    ``None``, expected exit code defaults to ``0``. Other keys run only when set.
    """
    failed_conditions: list[str] = []
    expected_exit = assert_config.exit_code if assert_config.exit_code is not None else 0
    failed_conditions.extend(evaluate_exit_code(expected_exit, exit_code))

    combined_output = f"{stdout}\n{stderr}"
    if assert_config.output_contains is not None:
        failed_conditions.extend(evaluate_output_contains(assert_config.output_contains, combined_output))
    if assert_config.output_not_contains is not None:
        failed_conditions.extend(evaluate_output_not_contains(assert_config.output_not_contains, combined_output))
    if assert_config.regex_match is not None:
        failed_conditions.extend(evaluate_regex_match(assert_config.regex_match, combined_output))
    if assert_config.json_match is not None:
        failed_conditions.extend(evaluate_json_match(assert_config.json_match, stdout))
    if assert_config.file_exists is not None:
        failed_conditions.extend(evaluate_file_exists(assert_config.file_exists, worktree_path))
    if assert_config.file_not_exists is not None:
        failed_conditions.extend(evaluate_file_not_exists(assert_config.file_not_exists, worktree_path))
    if assert_config.file_not_empty is not None:
        failed_conditions.extend(evaluate_file_not_empty(assert_config.file_not_empty, worktree_path))

    passed = len(failed_conditions) == 0
    return AssertionResult(
        passed=passed,
        failed_conditions=failed_conditions,
        message="" if passed else "\n".join(failed_conditions),
    )
