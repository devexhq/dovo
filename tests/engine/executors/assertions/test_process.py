"""Contract tests for engine/executors/assertions/process.py."""

from __future__ import annotations

import pytest

from dovo.engine.executors.assertions.process import (
    evaluate_exit_code,
    evaluate_output_contains,
    evaluate_output_not_contains,
    evaluate_regex_match,
)


class EvaluateExitCodeTests:
    @pytest.mark.parametrize(
        ("expected", "actual", "failures"),
        [
            pytest.param(0, 0, [], id="scalar-match"),
            pytest.param([0, 2], 2, [], id="list-match"),
            pytest.param(0, 1, ["exit_code: expected [0], got 1"], id="scalar-mismatch"),
            pytest.param([0, 2], 1, ["exit_code: expected [0, 2], got 1"], id="list-mismatch"),
        ],
    )
    def test_exit_code_is_checked_against_the_expected_set(
        self, expected: int | list[int], actual: int, failures: list[str]
    ) -> None:
        """[tier-1/unit] evaluate_exit_code: a scalar or list of accepted codes passes when it contains the actual code and otherwise reports the normalized list."""
        assert evaluate_exit_code(expected, actual) == failures


class EvaluateOutputContainsTests:
    def test_present_substrings_pass(self) -> None:
        """[tier-1/unit] evaluate_output_contains: every required substring present yields no failures."""
        assert evaluate_output_contains(["alpha", "beta"], "alpha beta") == []

    def test_each_missing_substring_reports_its_own_failure(self) -> None:
        """[tier-1/unit] evaluate_output_contains: each missing substring is reported separately, in list order."""
        assert evaluate_output_contains(["alpha", "beta", "gamma"], "beta") == [
            "output_contains: substring 'alpha' not found in output",
            "output_contains: substring 'gamma' not found in output",
        ]


class EvaluateOutputNotContainsTests:
    def test_absent_substring_passes(self) -> None:
        """[tier-1/unit] evaluate_output_not_contains: a forbidden substring that is absent yields no failures."""
        assert evaluate_output_not_contains("secret", "clean output") == []

    def test_each_forbidden_substring_found_reports_its_own_failure(self) -> None:
        """[tier-1/unit] evaluate_output_not_contains: each forbidden substring found is reported separately."""
        assert evaluate_output_not_contains(["a", "b"], "a and b") == [
            "output_not_contains: forbidden substring 'a' found in output",
            "output_not_contains: forbidden substring 'b' found in output",
        ]


class EvaluateRegexMatchTests:
    def test_matching_pattern_passes(self) -> None:
        """[tier-1/unit] evaluate_regex_match: a pattern found anywhere in the output (search semantics) yields no failures."""
        assert evaluate_regex_match(r"v\d+\.\d+", "release v1.2 ready") == []

    def test_non_matching_pattern_fails(self) -> None:
        """[tier-1/unit] evaluate_regex_match: a pattern with no match reports 'did not match output'."""
        assert evaluate_regex_match(r"^done$", "not done yet") == ["regex_match: pattern '^done$' did not match output"]

    def test_invalid_pattern_reports_a_failure_instead_of_raising(self) -> None:
        """[tier-1/unit] evaluate_regex_match: an uncompilable pattern returns one 'invalid regex pattern' failure naming it."""
        failures = evaluate_regex_match("(", "anything")

        assert len(failures) == 1
        assert failures[0].startswith("regex_match: invalid regex pattern '(': ")
