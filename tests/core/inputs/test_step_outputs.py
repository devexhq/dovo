"""Contract tests for resolving and rendering `steps.<id>.outputs.<key>` values."""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

from dovo.core.inputs import StepOutputValue, read_json_path, render_step_output, resolve_step_output


class StepOutputResolverTests:
    @pytest.mark.parametrize(
        ("outputs", "stdout", "key", "expected"),
        [
            pytest.param({"verdict": "ok"}, "done\n", "verdict", StepOutputValue("ok"), id="recorded_key"),
            pytest.param({}, '{"count": 3}\n', "count", StepOutputValue(3), id="stdout_only_key"),
            pytest.param(
                {"verdict": "recorded"},
                json.dumps({"verdict": "stdout"}),
                "verdict",
                StepOutputValue("recorded"),
                id="recorded_wins_over_stdout",
            ),
            pytest.param(
                {}, json.dumps({"reason": None}), "reason", StepOutputValue(None), id="stdout_json_null_is_found"
            ),
            pytest.param({"verdict": "ok"}, json.dumps({"verdict": "ok"}), "missing", None, id="missing_key"),
            pytest.param({}, "plain text\n", "verdict", None, id="stdout_not_json"),
            pytest.param({}, json.dumps(["verdict"]), "verdict", None, id="stdout_not_an_object"),
        ],
    )
    def test_resolve_step_output(
        self, outputs: dict[str, str], stdout: str, key: str, expected: StepOutputValue | None
    ) -> None:
        assert resolve_step_output(outputs, stdout, key) == expected

    @pytest.mark.parametrize(
        ("outputs", "stdout", "key", "path", "expected"),
        [
            pytest.param(
                {"report": '{"score": 85}'}, "", "report", ["score"], StepOutputValue(85), id="recorded_json_string"
            ),
            pytest.param(
                {},
                json.dumps({"metrics": {"coverage": 85}}),
                "metrics",
                ["coverage"],
                StepOutputValue(85),
                id="stdout_object",
            ),
            pytest.param({"report": "not json"}, "", "report", ["score"], None, id="unparsable_recorded_value"),
            pytest.param({}, json.dumps({"metrics": {"coverage": 85}}), "metrics", ["lines"], None, id="missing_path"),
            pytest.param({}, json.dumps({"verdict": "ok"}), "verdict", ["deeper"], None, id="path_through_a_scalar"),
            pytest.param({"a": '{"0": 1}'}, "", "a", (0,), None, id="integer_segment"),
        ],
    )
    def test_resolve_step_output_follows_a_further_path(
        self,
        outputs: dict[str, str],
        stdout: str,
        key: str,
        path: Sequence[str | int],
        expected: StepOutputValue | None,
    ) -> None:
        assert resolve_step_output(outputs, stdout, key, path) == expected

    @pytest.mark.parametrize(
        ("value", "rendered"),
        [
            pytest.param("x", "x", id="string"),
            pytest.param(None, "", id="null"),
            pytest.param(True, "true", id="true"),
            pytest.param(False, "false", id="false"),
            pytest.param(["a.py", "b.py"], '["a.py", "b.py"]', id="list"),
            pytest.param({"a": 1}, '{"a": 1}', id="object"),
            pytest.param(3, "3", id="integer"),
        ],
    )
    def test_render_step_output(self, value: object, rendered: str) -> None:
        assert render_step_output(value) == rendered


class ReadJsonPathTests:
    @pytest.mark.parametrize(
        ("text", "path", "expected"),
        [
            pytest.param('{"done": true}', ("done",), StepOutputValue(True), id="object_key"),
            pytest.param('{"report": {"score": 85}}', ("report", "score"), StepOutputValue(85), id="nested_key"),
            pytest.param("[1, 2]", (), StepOutputValue([1, 2]), id="empty_path_is_the_parsed_value"),
            pytest.param('{"done": true}', ("missing",), None, id="missing_key"),
            pytest.param('{"0": "zero"}', (0,), None, id="int_segment"),
            pytest.param("plain text", ("done",), None, id="not_json"),
        ],
    )
    def test_path_reaches_into_parsed_json(
        self, text: str, path: Sequence[str | int], expected: StepOutputValue | None
    ) -> None:
        assert read_json_path(text, path) == expected
