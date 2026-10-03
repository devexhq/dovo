"""Pure assertion evaluators for step results.

Public API is re-exported here so callers import from
``dovo.engine.executors.assertions`` without knowing the internal split.
"""

from dovo.engine.executors.assertions.aggregate import evaluate_assertions
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

__all__ = [
    "AssertionResult",
    "evaluate_assertions",
    "evaluate_exit_code",
    "evaluate_file_exists",
    "evaluate_file_not_empty",
    "evaluate_file_not_exists",
    "evaluate_json_match",
    "evaluate_output_contains",
    "evaluate_output_not_contains",
    "evaluate_regex_match",
]
