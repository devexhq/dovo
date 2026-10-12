"""Validate, parse and summarize human answers to interaction requests."""

from __future__ import annotations

import json
from collections.abc import Sequence

from pydantic import ValidationError

from dovo.core.catalog.definitions import CommentPolicy
from dovo.core.interaction.models import (
    COMMENT_MAX_CHARACTERS,
    HeadlessAnswerInput,
    HeadlessArgumentsResult,
    InteractionAnswer,
    InteractionAnswerResult,
    InteractionAnswerStatus,
    InteractionRequest,
    RequestOption,
)

_STRICTEST_FIRST: tuple[CommentPolicy, ...] = ("required", "optional")


def effective_comment_policy(request: InteractionRequest, selected: Sequence[str]) -> CommentPolicy:
    """Return the chosen option's policy, the strictest among several chosen options, else the interaction default."""
    chosen = [option.comment for option in request.options if option.value in selected]
    if not chosen:
        return request.comment.policy
    for policy in _STRICTEST_FIRST:
        if policy in chosen:
            return policy
    return "disabled"


def validate_answer(request: InteractionRequest, answer: InteractionAnswer) -> InteractionAnswerResult:
    """Check choice, text and comment rules for the request's mode and return the normalized answer."""
    if request.mode == "text":
        error = _text_error(request, answer)
        selected: list[str] = []
        text = (answer.text or "").strip()
    else:
        error = _choice_error(request, answer.selected)
        selected = [option.value for option in request.options if option.value in answer.selected]
        text = ""
    if error is not None:
        return _invalid(error)

    comment_error, comment = _normalize_comment(request, selected, answer.comment)
    if comment_error is not None:
        return _invalid(comment_error)

    return InteractionAnswerResult(
        status=InteractionAnswerStatus.OK,
        answer=InteractionAnswer(selected=selected, comment=comment, text=text or None),
    )


def _invalid(message: str) -> InteractionAnswerResult:
    """Return an INVALID answer result carrying message."""
    return InteractionAnswerResult(status=InteractionAnswerStatus.INVALID, errors=[message])


def _text_error(request: InteractionRequest, answer: InteractionAnswer) -> str | None:
    """Return a violation of the text-mode answer rules, or None."""
    if answer.selected:
        return f"'{answer.selected[0]}' is not a valid answer for prompt '{request.step_id}'.\nFix: pass text only."
    if not (answer.text or "").strip():
        return f"Prompt '{request.step_id}' requires non-empty text.\nFix: pass --answer {request.step_id}=<text>."
    return None


def _choice_error(request: InteractionRequest, selected: Sequence[str]) -> str | None:
    """Return a violation of the option-choice rules for the request's mode, or None."""
    values = [option.value for option in request.options]
    unknown = next((value for value in selected if value not in values), None)
    wrong_count = request.mode != "multiselect" and len(selected) != 1
    if unknown is None and not wrong_count:
        return None
    shown = unknown if unknown is not None else ",".join(selected)
    return f"'{shown}' is not a valid answer for prompt '{request.step_id}'.\nFix: use one of: {', '.join(values)}"


def _normalize_comment(
    request: InteractionRequest, selected: Sequence[str], comment: str | None
) -> tuple[str | None, str | None]:
    """Return (error, normalized comment) after applying the effective comment policy."""
    policy = effective_comment_policy(request, selected)
    if policy == "disabled":
        return None, None
    stripped = (comment or "").rstrip()
    if len(stripped) > COMMENT_MAX_CHARACTERS:
        return f"Comment is too long (max {COMMENT_MAX_CHARACTERS} characters).", None
    if not stripped.strip():
        if policy == "required":
            return (
                f"Prompt '{request.step_id}' answered '{','.join(selected)}' requires a comment.\n"
                "Fix: pass --comment or --comment-file.",
                None,
            )
        return None, None
    return None, stripped


def parse_headless_answer(request: InteractionRequest, supplied: HeadlessAnswerInput) -> InteractionAnswerResult:
    """Parse the raw `--answer` value by mode (multiselect JSON array or comma list, text verbatim) and validate it."""
    if supplied.step_id != request.step_id:
        return _invalid(
            f"Prompt '{supplied.step_id}' is not pending in this session.\nFix: use one of: {request.step_id}"
        )
    if supplied.value is not None and supplied.selected is not None:
        return _invalid(f"Prompt '{request.step_id}' takes either a value or selected, not both.")

    answer = _answer_from_input(request, supplied)
    if isinstance(answer, str):
        return _invalid(answer)

    warnings: list[str] = []
    if answer.comment and effective_comment_policy(request, answer.selected) == "disabled":
        warnings.append(f"Comment for prompt '{request.step_id}' was ignored because comments are disabled.")
        answer = answer.model_copy(update={"comment": None})

    result = validate_answer(request, answer)
    return result.model_copy(update={"warnings": warnings})


def _answer_from_input(request: InteractionRequest, supplied: HeadlessAnswerInput) -> InteractionAnswer | str:
    """Build an unvalidated answer from headless input, or return the reason it cannot be parsed."""
    if request.mode == "text":
        text = supplied.text if supplied.text is not None else supplied.value
        return InteractionAnswer(text=text, comment=supplied.comment)
    if supplied.selected is not None:
        return InteractionAnswer(selected=supplied.selected, comment=supplied.comment)

    raw = (supplied.value or "").strip()
    if request.mode != "multiselect":
        return InteractionAnswer(selected=[raw] if supplied.value is not None else [], comment=supplied.comment)

    selected = _multiselect_values(raw)
    if isinstance(selected, str):
        return selected
    return InteractionAnswer(selected=selected, comment=supplied.comment)


def _multiselect_values(raw: str) -> list[str] | str:
    """Return the values of a JSON array or comma list, or the reason a JSON array is malformed."""
    if not raw.startswith("["):
        return [entry.strip() for entry in raw.split(",") if entry.strip()]
    try:
        decoded = json.loads(raw)
    except ValueError as error:
        return f"'{raw}' is not a valid JSON array: {error}"
    if not isinstance(decoded, list) or not all(isinstance(entry, str) for entry in decoded):
        return f"'{raw}' must be a JSON array of strings."
    return list(decoded)


def parse_answer_arguments(
    answer: str | None, comment: str | None, answers_file_text: str | None
) -> HeadlessArgumentsResult:
    """Split `<step_id>=<value>` on the first `=`, or decode the answers-file JSON object; reject conflicting flags."""
    if answers_file_text is not None:
        if answer is not None or comment is not None:
            return _invalid_arguments("--answers-file cannot be combined with --answer, --comment or --comment-file.")
        return _from_answers_file(answers_file_text)
    if answer is None:
        if comment is not None:
            return _invalid_arguments("--comment and --comment-file need --answer.")
        return HeadlessArgumentsResult(status=InteractionAnswerStatus.OK)

    step_id, separator, value = answer.partition("=")
    if not separator or not step_id:
        return _invalid_arguments(f"'{answer}' is not valid.\nFix: use --answer <step_id>=<value>.")
    return HeadlessArgumentsResult(
        status=InteractionAnswerStatus.OK,
        supplied=HeadlessAnswerInput(step_id=step_id, value=value, comment=comment),
    )


def _invalid_arguments(message: str) -> HeadlessArgumentsResult:
    """Return an INVALID arguments result carrying message."""
    return HeadlessArgumentsResult(status=InteractionAnswerStatus.INVALID, errors=[message])


def _from_answers_file(text: str) -> HeadlessArgumentsResult:
    """Decode the answers-file JSON object into a headless input."""
    try:
        document = json.loads(text)
    except ValueError as error:
        return _invalid_arguments(f"The answers file is not valid JSON: {error}")
    if not isinstance(document, dict):
        return _invalid_arguments("The answers file must contain a JSON object.")
    try:
        supplied = HeadlessAnswerInput.model_validate(document)
    except ValidationError as error:
        return _invalid_arguments(f"The answers file is invalid: {error.errors()[0]['msg']}")
    return HeadlessArgumentsResult(status=InteractionAnswerStatus.OK, supplied=supplied)


def request_errors(request: InteractionRequest) -> list[str]:
    """Return semantic violations of a stored request."""
    errors: list[str] = []
    count = len(request.options)
    values = [option.value for option in request.options]
    minimum = {"confirm": 2, "select": 2, "multiselect": 1, "text": 0}[request.mode]
    if request.mode == "text" and count:
        errors.append("a 'text' request cannot have options")
    if request.mode != "text" and count < minimum:
        errors.append(f"a '{request.mode}' request needs at least {minimum} options")
    if request.mode == "confirm" and count > 2:
        errors.append("a 'confirm' request needs exactly 2 options")
    if len(set(values)) != len(values):
        errors.append("option values are not unique")
    if request.mode == "multiselect" and any(option.fail for option in request.options):
        errors.append("a 'multiselect' request cannot have failing options")
    return errors


def build_outputs(request: InteractionRequest, answer: InteractionAnswer) -> dict[str, str]:
    """Return selected, selected_count, comment, text, artifact_revision and artifact_digest as strings."""
    selected, count = _selection(request, answer)
    artifact = request.artifact
    return {
        "selected": selected,
        "selected_count": str(count),
        "comment": answer.comment or "",
        "text": answer.text or "",
        "artifact_revision": str(artifact.revision) if artifact is not None else "",
        "artifact_digest": artifact.digest if artifact is not None else "",
    }


def _selection(request: InteractionRequest, answer: InteractionAnswer) -> tuple[str, int]:
    """Return the `selected` output string and the selected count for the request's mode."""
    if request.mode == "text":
        return "", 0
    if request.mode == "multiselect":
        chosen = [option.value for option in request.options if option.value in answer.selected]
        return json.dumps(chosen), len(chosen)
    return (answer.selected[0] if answer.selected else ""), 1


def failed_option(request: InteractionRequest, answer: InteractionAnswer) -> RequestOption | None:
    """Return the first chosen option with `fail` set, or None."""
    if request.mode not in ("confirm", "select"):
        return None
    return next((option for option in request.options if option.value in answer.selected and option.fail), None)
