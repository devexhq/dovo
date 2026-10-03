"""Contract tests for engine/executors/assertions/json_match.py."""

from __future__ import annotations

from typing import Any

import pytest

from worktree.engine.executors.assertions import evaluate_json_match


def _config(path: str, operator: str, value: object) -> dict[str, Any]:
    return {"path": path, "operator": operator, "value": value}


class EvaluateJsonMatchTests:
    @pytest.mark.parametrize(
        ("config", "stdout"),
        [
            pytest.param(_config("a.b", "eq", 1), '{"a": {"b": 1}}', id="eq-nested"),
            pytest.param(_config("a", "neq", 2), '{"a": 1}', id="neq"),
            pytest.param(_config("tags", "contains", "x"), '{"tags": ["x", "y"]}', id="contains-list"),
            pytest.param(_config("name", "contains", "ell"), '{"name": "hello"}', id="contains-substring"),
            pytest.param(_config("n", "gt", 1), '{"n": 2}', id="gt"),
            pytest.param(_config("n", "gte", 2), '{"n": 2}', id="gte"),
            pytest.param(_config("n", "lt", 3), '{"n": 2}', id="lt"),
            pytest.param(_config("n", "lte", 2.0), '{"n": 2}', id="lte-mixed-numeric"),
        ],
    )
    def test_satisfied_operator_yields_no_failures(self, config: dict[str, Any], stdout: str) -> None:
        """[tier-1/unit] evaluate_json_match: each supported operator returns [] when the value at the dot-path satisfies it."""
        assert evaluate_json_match(config, stdout) == []

    @pytest.mark.parametrize(
        ("config", "stdout", "expected"),
        [
            pytest.param(_config("a", "eq", 2), '{"a": 1}', "json_match: 'a' was 1, expected 2", id="eq"),
            pytest.param(_config("a", "neq", 1), '{"a": 1}', "json_match: 'a' was 1, expected not 1", id="neq"),
            pytest.param(
                _config("tags", "contains", "z"),
                '{"tags": ["x"]}',
                "json_match: 'tags' does not contain 'z' (was ['x'])",
                id="contains",
            ),
            pytest.param(
                _config("n", "contains", 1),
                '{"n": 5}',
                "json_match: 'n' does not contain 1 (was 5)",
                id="contains-type-error",
            ),
            pytest.param(_config("n", "gt", 5), '{"n": 2}', "json_match: 'n' was 2, expected greater than 5", id="gt"),
            pytest.param(_config("n", "gte", 5), '{"n": 2}', "json_match: 'n' was 2, expected at least 5", id="gte"),
            pytest.param(_config("n", "lt", 1), '{"n": 2}', "json_match: 'n' was 2, expected less than 1", id="lt"),
            pytest.param(_config("n", "lte", 1), '{"n": 2}', "json_match: 'n' was 2, expected at most 1", id="lte"),
        ],
    )
    def test_unsatisfied_operator_reports_actual_and_expected(
        self, config: dict[str, Any], stdout: str, expected: str
    ) -> None:
        """[tier-1/unit] evaluate_json_match: an unsatisfied operator returns exactly one failure quoting the actual and expected values."""
        assert evaluate_json_match(config, stdout) == [expected]

    def test_invalid_json_stdout_fails(self) -> None:
        """[tier-1/unit] evaluate_json_match: non-JSON stdout returns the single 'Invalid JSON output' failure."""
        assert evaluate_json_match(_config("a", "eq", 1), "not json") == ["json_match: Invalid JSON output"]

    @pytest.mark.parametrize(
        ("path", "stdout"),
        [
            pytest.param("missing", '{"a": 1}', id="missing-key"),
            pytest.param("a.0", '{"a": [1]}', id="list-index-not-supported"),
            pytest.param("a.b", '{"a": 1}', id="descend-into-scalar"),
        ],
    )
    def test_unresolvable_path_fails(self, path: str, stdout: str) -> None:
        """[tier-1/unit] evaluate_json_match: a path that does not resolve through nested mapping keys reports 'not found' for that path."""
        assert evaluate_json_match(_config(path, "eq", 1), stdout) == [f"json_match: JSON path '{path}' not found"]

    def test_unsupported_operator_fails(self) -> None:
        """[tier-1/unit] evaluate_json_match: an operator outside the supported set reports 'unsupported operator'."""
        assert evaluate_json_match(_config("a", "approx", 1), '{"a": 1}') == [
            "json_match: unsupported operator 'approx'"
        ]

    @pytest.mark.parametrize(
        ("operator", "stdout", "expected_val", "expected_failures"),
        [
            pytest.param(
                "gt",
                '{"flag": true}',
                0,
                ["json_match: operator 'gt' requires numeric values, got bool and int"],
                id="gt_bool_actual",
            ),
            pytest.param(
                "lt",
                '{"flag": 1}',
                False,
                ["json_match: operator 'lt' requires numeric values, got int and bool"],
                id="lt_bool_value",
            ),
            pytest.param(
                "gte",
                '{"flag": true}',
                True,
                ["json_match: operator 'gte' requires numeric values, got bool and bool"],
                id="gte_bool_both",
            ),
            pytest.param(
                "lte",
                '{"flag": false}',
                0,
                ["json_match: operator 'lte' requires numeric values, got bool and int"],
                id="lte_bool_actual",
            ),
        ],
    )
    def test_json_match_ordering_operators_reject_boolean_conversion(
        self,
        operator: str,
        stdout: str,
        expected_val: object,
        expected_failures: list[str],
    ) -> None:
        """[tier-1/unit] evaluate_json_match: numeric ordering operators reject booleans to prevent True == 1 coercion."""
        result = evaluate_json_match({"path": "flag", "operator": operator, "value": expected_val}, stdout)

        assert result == expected_failures
