"""Contract tests for validating, parsing and summarizing interaction answers."""

from __future__ import annotations

import json

import pytest

from dovo.core.catalog.definitions import CommentPolicy
from dovo.core.interaction import (
    HeadlessAnswerInput,
    InteractionAnswer,
    InteractionAnswerStatus,
    InteractionRequest,
    RequestArtifact,
    RequestComment,
    RequestOption,
)
from dovo.core.interaction.services.answer import (
    build_outputs,
    effective_comment_policy,
    failed_option,
    parse_answer_arguments,
    parse_headless_answer,
    request_errors,
    validate_answer,
)


def _options(*values: str, policy: CommentPolicy = "disabled", failing: str | None = None) -> list[RequestOption]:
    """Return options for values, all with policy, marking `failing` as a fail option."""
    return [RequestOption(value=value, label=value.title(), comment=policy, fail=value == failing) for value in values]


def _request(
    mode: str = "select",
    options: list[RequestOption] | None = None,
    policy: CommentPolicy = "disabled",
    artifact: RequestArtifact | None = None,
) -> InteractionRequest:
    """Build a request for step 'approve' with approve/revise/deny options by default."""
    resolved = options if options is not None else _options("approve", "revise", "deny")
    return InteractionRequest.model_validate(
        {
            "step_id": "approve",
            "mode": mode,
            "message": "Go?",
            "options": [option.model_dump() for option in resolved],
            "comment": RequestComment(policy=policy).model_dump(),
            "artifact": artifact.model_dump() if artifact is not None else None,
        }
    )


_CHOICE_ERROR = "'{value}' is not a valid answer for prompt 'approve'.\nFix: use one of: approve, revise, deny"


class ValidateAnswerTests:
    """Contract tests for validate_answer and effective_comment_policy."""

    @pytest.mark.parametrize(
        ("mode", "answer", "expected_selected"),
        [
            pytest.param("select", InteractionAnswer(selected=["revise"]), ["revise"], id="select_valid"),
            pytest.param("confirm", InteractionAnswer(selected=["deny"]), ["deny"], id="confirm_valid"),
            pytest.param("multiselect", InteractionAnswer(selected=[]), [], id="multiselect_none"),
            pytest.param(
                "multiselect",
                InteractionAnswer(selected=["deny", "approve", "deny"]),
                ["approve", "deny"],
                id="multiselect_option_order_unique",
            ),
        ],
    )
    def test_valid_choice_is_normalized_by_mode(
        self, mode: str, answer: InteractionAnswer, expected_selected: list[str]
    ) -> None:
        result = validate_answer(_request(mode), answer)

        assert result.ok is True
        assert result.answer is not None
        assert result.answer.selected == expected_selected

    @pytest.mark.parametrize(
        ("mode", "answer", "expected_error"),
        [
            pytest.param(
                "select",
                InteractionAnswer(selected=["maybe"]),
                _CHOICE_ERROR.format(value="maybe"),
                id="select_unknown",
            ),
            pytest.param("select", InteractionAnswer(selected=[]), _CHOICE_ERROR.format(value=""), id="select_empty"),
            pytest.param(
                "select",
                InteractionAnswer(selected=["approve", "deny"]),
                _CHOICE_ERROR.format(value="approve,deny"),
                id="select_two",
            ),
            pytest.param(
                "multiselect",
                InteractionAnswer(selected=["x"]),
                _CHOICE_ERROR.format(value="x"),
                id="multiselect_unknown",
            ),
        ],
    )
    def test_invalid_choice_is_rejected_by_mode(
        self, mode: str, answer: InteractionAnswer, expected_error: str
    ) -> None:
        result = validate_answer(_request(mode), answer)

        assert result.status == InteractionAnswerStatus.INVALID
        assert result.errors == [expected_error]

    def test_text_mode_trims_text(self) -> None:
        result = validate_answer(_request("text", options=[]), InteractionAnswer(text="  use sqlite \n"))

        assert result.answer is not None
        assert result.answer.text == "use sqlite"

    @pytest.mark.parametrize(
        "answer",
        [pytest.param(InteractionAnswer(text="   "), id="blank"), pytest.param(InteractionAnswer(), id="missing")],
    )
    def test_text_mode_requires_non_empty_text(self, answer: InteractionAnswer) -> None:
        result = validate_answer(_request("text", options=[]), answer)

        assert result.errors == ["Prompt 'approve' requires non-empty text.\nFix: pass --answer approve=<text>."]

    @pytest.mark.parametrize(
        ("policy", "comment", "expected_comment"),
        [
            pytest.param("optional", "", None, id="optional_empty"),
            pytest.param("optional", "keep it  \n", "keep it", id="trailing_whitespace_stripped"),
            pytest.param("optional", "x" * 8000, "x" * 8000, id="at_limit"),
            pytest.param("disabled", "ignored", None, id="disabled_drops_comment"),
        ],
    )
    def test_accepted_comment_is_normalized_by_policy(
        self, policy: CommentPolicy, comment: str, expected_comment: str | None
    ) -> None:
        request = _request(options=_options("approve", "revise", policy=policy))

        result = validate_answer(request, InteractionAnswer(selected=["revise"], comment=comment))

        assert result.answer is not None
        assert result.answer.comment == expected_comment

    @pytest.mark.parametrize(
        ("policy", "comment", "expected_error"),
        [
            pytest.param(
                "required",
                None,
                "Prompt 'approve' answered 'revise' requires a comment.\nFix: pass --comment or --comment-file.",
                id="required_missing",
            ),
            pytest.param(
                "required",
                "  ",
                "Prompt 'approve' answered 'revise' requires a comment.\nFix: pass --comment or --comment-file.",
                id="required_blank",
            ),
            pytest.param("optional", "x" * 8001, "Comment is too long (max 8000 characters).", id="too_long"),
        ],
    )
    def test_rejected_comment_reports_the_policy_error(
        self, policy: CommentPolicy, comment: str | None, expected_error: str
    ) -> None:
        request = _request(options=_options("approve", "revise", policy=policy))

        result = validate_answer(request, InteractionAnswer(selected=["revise"], comment=comment))

        assert result.errors == [expected_error]

    def test_effective_policy_picks_chosen_option_then_strictest_then_default(self) -> None:
        options = [
            RequestOption(value="a", label="A", comment="optional"),
            RequestOption(value="b", label="B", comment="required"),
            RequestOption(value="c", label="C", comment="disabled"),
        ]
        request = _request("multiselect", options=options, policy="optional")

        assert effective_comment_policy(request, ["c"]) == "disabled"
        assert effective_comment_policy(request, ["a", "b"]) == "required"
        assert effective_comment_policy(request, ["a", "c"]) == "optional"
        assert effective_comment_policy(request, []) == "optional"


class ParseHeadlessAnswerTests:
    """Contract tests for parse_headless_answer."""

    @pytest.mark.parametrize(
        "value", [pytest.param('["B1","B3"]', id="json_array"), pytest.param("B1, B3", id="comma_list")]
    )
    def test_multiselect_accepts_json_array_and_comma_list(self, value: str) -> None:
        request = _request("multiselect", options=_options("B1", "B2", "B3"))

        result = parse_headless_answer(request, HeadlessAnswerInput(step_id="approve", value=value))

        assert result.answer is not None
        assert result.answer.selected == ["B1", "B3"]

    def test_text_value_is_taken_verbatim(self) -> None:
        request = _request("text", options=[])

        result = parse_headless_answer(request, HeadlessAnswerInput(step_id="approve", value="a=b, c"))

        assert result.answer is not None
        assert result.answer.text == "a=b, c"

    @pytest.mark.parametrize(
        ("supplied", "expected_error"),
        [
            pytest.param(
                HeadlessAnswerInput(step_id="approve", value="maybe"),
                _CHOICE_ERROR.format(value="maybe"),
                id="unknown_value",
            ),
            pytest.param(
                HeadlessAnswerInput(step_id="other", value="approve"),
                "Prompt 'other' is not pending in this session.\nFix: use one of: approve",
                id="unknown_step",
            ),
            pytest.param(
                HeadlessAnswerInput(step_id="approve", value="approve", selected=["approve"]),
                "Prompt 'approve' takes either a value or selected, not both.",
                id="value_and_selected",
            ),
        ],
    )
    def test_invalid_input_is_rejected_with_valid_choices(
        self, supplied: HeadlessAnswerInput, expected_error: str
    ) -> None:
        result = parse_headless_answer(_request(), supplied)

        assert result.status == InteractionAnswerStatus.INVALID
        assert result.errors == [expected_error]

    def test_comment_on_disabled_policy_is_discarded_with_warning(self) -> None:
        result = parse_headless_answer(
            _request(), HeadlessAnswerInput(step_id="approve", value="approve", comment="hi")
        )

        assert result.ok is True
        assert result.answer is not None
        assert result.answer.comment is None
        assert len(result.warnings) == 1


class ParseAnswerArgumentsTests:
    """Contract tests for parse_answer_arguments."""

    def test_answer_flag_splits_on_first_equals(self) -> None:
        result = parse_answer_arguments("approve=revise=x", "x", None)

        assert result.supplied == HeadlessAnswerInput(step_id="approve", value="revise=x", comment="x")

    def test_answers_file_decodes_object(self) -> None:
        document = json.dumps({"step_id": "triage", "selected": ["B1"], "comment": "c", "text": "t"})

        result = parse_answer_arguments(None, None, document)

        assert result.supplied == HeadlessAnswerInput(step_id="triage", selected=["B1"], comment="c", text="t")

    def test_no_flags_return_ok_without_input(self) -> None:
        result = parse_answer_arguments(None, None, None)

        assert result.ok is True
        assert result.supplied is None

    @pytest.mark.parametrize(
        ("answer", "comment", "answers_file_text"),
        [
            pytest.param("a=b", None, "{}", id="answers_file_with_answer"),
            pytest.param(None, "c", '{"step_id": "a"}', id="answers_file_with_comment"),
            pytest.param(None, "c", None, id="comment_without_answer"),
            pytest.param("no-equals", None, None, id="answer_without_equals"),
            pytest.param("=value", None, None, id="answer_without_step_id"),
            pytest.param(None, None, "[1]", id="non_object_json"),
            pytest.param(None, None, "{broken", id="malformed_json"),
            pytest.param(None, None, '{"selected": ["a"]}', id="missing_step_id"),
        ],
    )
    def test_invalid_argument_combinations_are_rejected(
        self, answer: str | None, comment: str | None, answers_file_text: str | None
    ) -> None:
        result = parse_answer_arguments(answer, comment, answers_file_text)

        assert result.status == InteractionAnswerStatus.INVALID
        assert result.supplied is None
        assert len(result.errors) == 1

    def test_comment_without_answer_names_the_flag_rule(self) -> None:
        result = parse_answer_arguments(None, "c", None)

        assert result.errors == ["--comment and --comment-file need --answer."]


class BuildOutputsTests:
    """Contract tests for build_outputs and failed_option."""

    @pytest.mark.parametrize(
        ("mode", "answer", "expected"),
        [
            pytest.param(
                "select",
                InteractionAnswer(selected=["revise"], comment="why"),
                {"selected": "revise", "selected_count": "1", "comment": "why", "text": ""},
                id="select",
            ),
            pytest.param(
                "multiselect",
                InteractionAnswer(selected=["approve", "deny"]),
                {"selected": '["approve", "deny"]', "selected_count": "2", "comment": "", "text": ""},
                id="multiselect",
            ),
            pytest.param(
                "multiselect",
                InteractionAnswer(selected=[]),
                {"selected": "[]", "selected_count": "0", "comment": "", "text": ""},
                id="multiselect_empty",
            ),
            pytest.param(
                "text",
                InteractionAnswer(text="use sqlite"),
                {"selected": "", "selected_count": "0", "comment": "", "text": "use sqlite"},
                id="text",
            ),
        ],
    )
    def test_outputs_by_mode(self, mode: str, answer: InteractionAnswer, expected: dict[str, str]) -> None:
        request = _request(mode, options=[] if mode == "text" else None)

        outputs = build_outputs(request, answer)

        assert outputs == {**expected, "artifact_revision": "", "artifact_digest": ""}

    def test_artifact_outputs_carry_revision_and_digest(self) -> None:
        request = _request(artifact=RequestArtifact(name="plan", revision=2, digest="b07d44aa"))

        outputs = build_outputs(request, InteractionAnswer(selected=["approve"]))

        assert (outputs["artifact_revision"], outputs["artifact_digest"]) == ("2", "b07d44aa")

    def test_failing_option_is_reported_for_confirm_and_select_only(self) -> None:
        options = _options("approve", "deny", failing="deny")

        assert failed_option(_request("select", options), InteractionAnswer(selected=["deny"])) == options[1]
        assert failed_option(_request("select", options), InteractionAnswer(selected=["approve"])) is None
        assert failed_option(_request("multiselect", options), InteractionAnswer(selected=["deny"])) is None


class RequestErrorsTests:
    """Contract tests for request_errors."""

    @pytest.mark.parametrize(
        ("mode", "options"),
        [
            pytest.param("select", _options("only"), id="select_one_option"),
            pytest.param("select", _options("a", "a"), id="duplicate_values"),
            pytest.param("text", _options("a", "b"), id="text_with_options"),
            pytest.param("multiselect", _options("a", "b", failing="b"), id="failing_multiselect"),
            pytest.param("confirm", _options("a", "b", "c"), id="confirm_three"),
            pytest.param("multiselect", [], id="multiselect_empty"),
        ],
    )
    def test_inconsistent_stored_request_reports_errors(self, mode: str, options: list[RequestOption]) -> None:
        assert request_errors(_request(mode, options=options)) != []

    def test_well_formed_request_has_no_errors(self) -> None:
        assert request_errors(_request()) == []
