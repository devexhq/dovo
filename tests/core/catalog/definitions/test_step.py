"""Contract tests for authored step definition models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dovo.common.tool_policy import LEGACY_TOOLS_MESSAGE, ToolPolicy
from dovo.core.catalog.definitions import (
    ArtifactPublishSpec,
    InteractionSpec,
    LoopStepBlock,
    StepDefinition,
    StepType,
)
from dovo.core.catalog.definitions.step import interaction_spec_errors
from dovo.core.catalog.exceptions import AskStepInvalidError


class StepDefinitionArtifactsFieldTests:
    def test_artifacts_field_defaults_to_empty_list(self) -> None:
        step = StepDefinition(id="s1", type=StepType.COMMAND, command="echo hi")

        assert step.artifacts == []

    @pytest.mark.parametrize(
        "artifacts_payload",
        [
            pytest.param([{"path": "dist/*.whl"}], id="missing_name"),
            pytest.param([{"name": "dist-packages"}], id="missing_path"),
        ],
    )
    def test_artifacts_field_rejects_missing_name_or_path(self, artifacts_payload: list[dict[str, str]]) -> None:
        with pytest.raises(ValidationError):
            StepDefinition(
                id="s1",
                type=StepType.COMMAND,
                command="echo hi",
                artifacts=artifacts_payload,  # ty: ignore[invalid-argument-type]  # intentional ill-typed input: the raised ValidationError is this test's subject
            )

    def test_internal_type_without_command_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            StepDefinition(id="s1", type=StepType.INTERNAL)


class StepDefinitionMaxFilesTests:
    def test_agent_max_files_accepts_positive_integer(self) -> None:
        step = StepDefinition(id="s1", type=StepType.AGENT, prompt="go", max_files=1)

        assert step.max_files == 1

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({"run": "echo hi"}, id="run"),
            pytest.param({"type": "ask", "interaction": {"mode": "confirm", "message": "Go?"}}, id="ask"),
            pytest.param({"type": "command", "command": "echo hi"}, id="command"),
            pytest.param({"type": "script", "script_path": "scripts/check.py"}, id="script"),
            pytest.param({"type": "internal", "command": "noop"}, id="internal"),
        ],
    )
    def test_non_agent_max_files_raises_validation_error(self, payload: dict[str, object]) -> None:
        with pytest.raises(ValidationError, match="max_files"):
            StepDefinition.model_validate({"id": "s1", "max_files": 3, **payload})

    @pytest.mark.parametrize("value", [pytest.param(0, id="zero"), pytest.param(-1, id="negative")])
    def test_non_positive_max_files_raises_validation_error(self, value: int) -> None:
        with pytest.raises(ValidationError, match="max_files"):
            StepDefinition.model_validate({"id": "s1", "type": "agent", "prompt": "go", "max_files": value})


class ArtifactPublishSpecTests:
    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            pytest.param({}, True, id="defaults_to_true"),
            pytest.param({"required": False}, False, id="accepts_false"),
        ],
    )
    def test_required_flag_value(self, payload: dict[str, bool], expected: bool) -> None:
        spec = ArtifactPublishSpec.model_validate({"name": "plan", "path": "PLAN.md", **payload})

        assert spec.required is expected

    def test_required_rejects_non_boolean_in_strict_mode(self) -> None:
        with pytest.raises(ValidationError):
            ArtifactPublishSpec.model_validate({"name": "plan", "path": "PLAN.md", "required": "no"})


class StepToolsContractTests:
    def test_legacy_string_list_raises_with_migration_message(self) -> None:
        with pytest.raises(ValidationError, match=LEGACY_TOOLS_MESSAGE):
            StepDefinition.model_validate({"id": "s", "type": "agent", "prompt": "p", "tools": ["shell"]})

    def test_omitted_tools_is_none_and_unset_while_empty_object_is_set_empty_policy(self) -> None:
        omitted = StepDefinition.model_validate({"id": "s", "type": "agent", "prompt": "p"})
        explicit = StepDefinition.model_validate({"id": "s", "type": "agent", "prompt": "p", "tools": {}})

        assert (omitted.tools, "tools" in omitted.model_fields_set) == (None, False)
        assert (explicit.tools, "tools" in explicit.model_fields_set) == (ToolPolicy(), True)

    def test_run_step_with_empty_tools_object_is_rejected_but_omitted_is_accepted(self) -> None:
        assert StepDefinition.model_validate({"id": "s", "run": "r"}).run == "r"
        with pytest.raises(ValidationError, match="tools"):
            StepDefinition.model_validate({"id": "s", "run": "r", "tools": {}})


def _ask_error(payload: dict[str, object]) -> str:
    """Return the AskStepInvalidError message raised while validating payload as a step."""
    with pytest.raises(ValidationError) as excinfo:
        StepDefinition.model_validate(payload)
    cause = excinfo.value.errors()[0].get("ctx", {}).get("error")
    assert isinstance(cause, AskStepInvalidError)
    return str(cause)


def _confirm(**overrides: object) -> dict[str, object]:
    """Return a valid confirm interaction block with overrides applied."""
    return {"mode": "confirm", "message": "Go?", **overrides}


def _option(value: str, **overrides: object) -> dict[str, object]:
    """Return an option dict with a label derived from value."""
    return {"value": value, "label": value.title(), **overrides}


class StepAskShapeTests:
    def test_ask_without_interaction_is_rejected(self) -> None:
        assert _ask_error({"id": "a", "type": "ask"}) == "Step 'a': 'type: ask' requires 'interaction'."

    @pytest.mark.parametrize(
        ("fields", "expected"),
        [
            pytest.param({"run": "echo"}, "run", id="run"),
            pytest.param({"uses": "tpl"}, "uses", id="uses"),
            pytest.param({"command": "echo"}, "command", id="command"),
            pytest.param({"prompt": "p"}, "prompt", id="prompt"),
            pytest.param({"script_path": "s.sh"}, "script_path", id="script_path"),
            pytest.param({"tools": {"allow": [{"capability": "read"}]}}, "tools", id="tools"),
            pytest.param({"artifacts": [{"name": "n", "path": "p"}]}, "artifacts", id="artifacts"),
            pytest.param({"assert": {"exit_code": 0}}, "assert", id="assert"),
            pytest.param({"assert": {"exit_code": 0}, "prompt": "p", "run": "x"}, "run, prompt, assert", id="several"),
        ],
    )
    def test_ask_with_incompatible_fields_names_them_in_declaration_order(
        self, fields: dict[str, object], expected: str
    ) -> None:
        message = _ask_error({"id": "a", "type": "ask", "interaction": _confirm(), **fields})

        assert message == f"Step 'a': 'type: ask' cannot be combined with {expected}."

    @pytest.mark.parametrize(
        "step_fields",
        [
            pytest.param({"type": "command", "command": "echo"}, id="command"),
            pytest.param({"type": "agent", "prompt": "p"}, id="agent"),
            pytest.param({"type": "script", "script_path": "s.sh"}, id="script"),
            pytest.param({"run": "echo"}, id="run"),
        ],
    )
    def test_interaction_on_non_ask_step_is_rejected(self, step_fields: dict[str, object]) -> None:
        message = _ask_error({"id": "a", "interaction": _confirm(), **step_fields})

        assert message == "Step 'a': 'interaction' is only valid with 'type: ask'."

    @pytest.mark.parametrize(
        ("interaction", "expected_error"),
        [
            pytest.param(
                _confirm(options=[_option("a")]),
                "'confirm' interaction needs exactly 2 options or none, got 1.",
                id="confirm_one_option",
            ),
            pytest.param(
                _confirm(options=[_option("a"), _option("b"), _option("c")]),
                "'confirm' interaction needs exactly 2 options or none, got 3.",
                id="confirm_three_options",
            ),
            pytest.param(
                _confirm(
                    options_from={
                        "artifact": "r",
                        "file": "r.json",
                        "items_path": "i",
                        "value_field": "v",
                        "label_field": "l",
                    }
                ),
                "'confirm' interaction cannot use 'options_from'.",
                id="confirm_options_from",
            ),
            pytest.param(
                {"mode": "select", "message": "m", "options": [_option("a")]},
                "'select' interaction needs at least 2 option(s), got 1.",
                id="select_one_option",
            ),
            pytest.param(
                {"mode": "multiselect", "message": "m", "options": []},
                "'multiselect' interaction needs at least 1 option(s), got 0.",
                id="multiselect_no_options",
            ),
            pytest.param(
                {"mode": "select", "message": "m"},
                "'select' interaction requires 'options' or 'options_from'.",
                id="select_neither_source",
            ),
            pytest.param(
                {
                    "mode": "select",
                    "message": "m",
                    "options": [_option("a"), _option("b")],
                    "options_from": {
                        "artifact": "r",
                        "file": "r.json",
                        "items_path": "i",
                        "value_field": "v",
                        "label_field": "l",
                    },
                },
                "Specify either 'options' or 'options_from', not both.",
                id="select_both_sources",
            ),
            pytest.param(
                {"mode": "text", "message": "m", "options": [_option("a")]},
                "'text' interaction cannot use 'options'.",
                id="text_with_options",
            ),
            pytest.param(
                {"mode": "text", "message": "m", "comment": {"policy": "optional"}},
                "'text' interaction cannot use 'comment'.",
                id="text_with_comment",
            ),
        ],
    )
    def test_option_count_and_source_rules_reject_invalid_modes(
        self, interaction: dict[str, object], expected_error: str
    ) -> None:
        spec = InteractionSpec.model_validate(interaction)

        assert interaction_spec_errors(spec) == [expected_error]

    @pytest.mark.parametrize(
        ("mode", "option", "expected_error"),
        [
            pytest.param(
                "select",
                _option("bad value"),
                "Option value 'bad value' must match ^[A-Za-z0-9_.-]{1,64}$.",
                id="bad_pattern",
            ),
            pytest.param(
                "select",
                _option("x" * 65),
                f"Option value '{'x' * 65}' must match ^[A-Za-z0-9_.-]{{1,64}}$.",
                id="too_long",
            ),
            pytest.param("select", _option("dup"), "Option value 'dup' appears more than once.", id="duplicate"),
            pytest.param(
                "select", {"value": "lbl", "label": "  "}, "Option 'lbl' must have a non-empty label.", id="empty_label"
            ),
            pytest.param(
                "multiselect",
                _option("bad", fail=True),
                "Option 'bad' cannot set 'fail' in a 'multiselect' interaction.",
                id="fail_on_multiselect",
            ),
        ],
    )
    def test_option_field_rules_reject_invalid_options(
        self, mode: str, option: dict[str, object], expected_error: str
    ) -> None:
        spec = InteractionSpec.model_validate({"mode": mode, "message": "m", "options": [_option("dup"), option]})

        assert expected_error in interaction_spec_errors(spec)

    @pytest.mark.parametrize(
        "file_path",
        [
            pytest.param("/etc/x", id="absolute"),
            pytest.param("../x.json", id="parent"),
            pytest.param("a/../b.json", id="embedded_parent"),
            pytest.param("C:/x.json", id="drive"),
        ],
    )
    def test_unsafe_options_from_file_is_rejected(self, file_path: str) -> None:
        spec = InteractionSpec.model_validate(
            {
                "mode": "multiselect",
                "message": "m",
                "options_from": {
                    "artifact": "r",
                    "file": file_path,
                    "items_path": "i",
                    "value_field": "v",
                    "label_field": "l",
                },
            }
        )

        assert interaction_spec_errors(spec) == [
            f"options_from.file '{file_path}' must be a relative path without '..' segments."
        ]

    @pytest.mark.parametrize(
        "interaction",
        [
            pytest.param(_confirm(), id="confirm_default_options"),
            pytest.param(_confirm(options=[_option("yes"), _option("no", fail=True)]), id="confirm_two_options"),
            pytest.param({"mode": "select", "message": "m", "options": [_option("a"), _option("b")]}, id="select"),
            pytest.param(
                {
                    "mode": "multiselect",
                    "message": "m",
                    "options_from": {
                        "artifact": "r",
                        "file": "r.json",
                        "items_path": "findings",
                        "value_field": "id",
                        "label_field": "summary",
                    },
                },
                id="multiselect_options_from",
            ),
            pytest.param({"mode": "text", "message": "m"}, id="text"),
        ],
    )
    def test_valid_interactions_load_without_normalizing_options(self, interaction: dict[str, object]) -> None:
        step = StepDefinition.model_validate({"id": "a", "type": "ask", "interaction": interaction})

        assert step.interaction is not None
        assert (step.interaction.options is None) == ("options" not in interaction)


_LOOP_PAYLOAD: dict[str, object] = {
    "id": "fix",
    "type": "loop",
    "until": ["steps.check.exit_code == 0"],
    "do": [{"id": "check", "run": "true"}],
}


class StepWhenTests:
    @pytest.mark.parametrize(
        ("model", "payload", "message"),
        [
            pytest.param(StepDefinition, {"id": "s1", "run": "echo hi"}, "Step 's1': 'when' must list", id="step"),
            pytest.param(LoopStepBlock, _LOOP_PAYLOAD, "Loop 'fix': 'when' must list", id="loop"),
        ],
    )
    def test_empty_when_fails_naming_the_step(
        self, model: type[StepDefinition | LoopStepBlock], payload: dict[str, object], message: str
    ) -> None:
        with pytest.raises(ValidationError, match=message):
            model.model_validate({**payload, "when": []})

    @pytest.mark.parametrize(
        ("model", "payload"),
        [
            pytest.param(StepDefinition, {"id": "s1", "run": "echo hi"}, id="step"),
            pytest.param(LoopStepBlock, _LOOP_PAYLOAD, id="loop"),
        ],
    )
    def test_when_list_is_kept(self, model: type[StepDefinition | LoopStepBlock], payload: dict[str, object]) -> None:
        parsed = model.model_validate({**payload, "when": ["steps.a.exit_code == 0"]})

        assert parsed.when == ["steps.a.exit_code == 0"]
