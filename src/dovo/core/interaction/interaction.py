"""Entrypoint for resolving, validating and summarizing human-input prompts."""

from __future__ import annotations

from collections.abc import Sequence

from dovo.common.redact import SecretRedactor
from dovo.core.artifacts.store import ArtifactStore
from dovo.core.catalog.definitions import CommentPolicy, InteractionSpec
from dovo.core.interaction.models import (
    HeadlessAnswerInput,
    HeadlessArgumentsResult,
    InteractionAnswer,
    InteractionAnswerResult,
    InteractionRequest,
    InteractionResolveResult,
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
from dovo.core.interaction.services.resolve import resolve_request


class Interaction:
    """Entrypoint for resolving, validating and summarizing human-input prompts."""

    def __init__(self, store: ArtifactStore | None, redactor: SecretRedactor) -> None:
        """Bind the session artifact store and the run's redactor."""
        self._store = store
        self._redactor = redactor

    def resolve(self, step_id: str, spec: InteractionSpec, message: str) -> InteractionResolveResult:
        """Resolve spec into a request, loading options and the displayed artifact and masking displayed text."""
        return resolve_request(step_id, spec, message, self._store, self._redactor)

    @staticmethod
    def comment_policy(request: InteractionRequest, selected: Sequence[str]) -> CommentPolicy:
        """Return the comment policy that applies to the selected options."""
        return effective_comment_policy(request, selected)

    @staticmethod
    def validate(request: InteractionRequest, answer: InteractionAnswer) -> InteractionAnswerResult:
        """Validate answer against request and return the normalized answer."""
        return validate_answer(request, answer)

    @staticmethod
    def parse_headless(request: InteractionRequest, supplied: HeadlessAnswerInput) -> InteractionAnswerResult:
        """Convert a headless input into a validated answer for request."""
        return parse_headless_answer(request, supplied)

    @staticmethod
    def parse_arguments(
        answer: str | None, comment: str | None, answers_file_text: str | None
    ) -> HeadlessArgumentsResult:
        """Combine the resume answer flags into one headless input, or report a conflict or malformed value."""
        return parse_answer_arguments(answer, comment, answers_file_text)

    @staticmethod
    def request_errors(request: InteractionRequest) -> list[str]:
        """Return semantic problems of a stored request (mode/option mismatch, duplicate values)."""
        return request_errors(request)

    @staticmethod
    def outputs(request: InteractionRequest, answer: InteractionAnswer) -> dict[str, str]:
        """Return the step outputs for a validated answer."""
        return build_outputs(request, answer)

    @staticmethod
    def failing_option(request: InteractionRequest, answer: InteractionAnswer) -> RequestOption | None:
        """Return the chosen `fail: true` option, or None."""
        return failed_option(request, answer)
