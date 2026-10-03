"""Process/output assertion evaluators for step results.

Each function never raises for well-formed inputs and returns a list of failure
strings (empty means the check passed). Callers that inspect process output must
pass ``combined_output`` built as stdout, a newline, then stderr.
"""

from __future__ import annotations

import re


def evaluate_exit_code(expected: int | list[int], actual_exit_code: int) -> list[str]:
    """Return failure strings when ``actual_exit_code`` is not in ``expected``."""
    expected_list = expected if isinstance(expected, list) else [expected]
    if actual_exit_code in expected_list:
        return []
    return [f"exit_code: expected {expected_list!r}, got {actual_exit_code}"]


def evaluate_output_contains(required: str | list[str], combined_output: str) -> list[str]:
    """Return failure strings for each required substring missing from output."""
    required_list = required if isinstance(required, list) else [required]
    failures: list[str] = []
    for entry in required_list:
        if entry not in combined_output:
            failures.append(f"output_contains: substring '{entry}' not found in output")
    return failures


def evaluate_output_not_contains(forbidden: str | list[str], combined_output: str) -> list[str]:
    """Return failure strings for each forbidden substring found in output."""
    forbidden_list = forbidden if isinstance(forbidden, list) else [forbidden]
    failures: list[str] = []
    for entry in forbidden_list:
        if entry in combined_output:
            failures.append(f"output_not_contains: forbidden substring '{entry}' found in output")
    return failures


def evaluate_regex_match(pattern: str, combined_output: str) -> list[str]:
    """Return failure strings when ``pattern`` is invalid or does not match output."""
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        return [f"regex_match: invalid regex pattern '{pattern}': {exc}"]
    if compiled.search(combined_output) is None:
        return [f"regex_match: pattern '{pattern}' did not match output"]
    return []
