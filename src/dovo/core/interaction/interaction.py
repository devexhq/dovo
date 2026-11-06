"""Entrypoint for resolving, validating and summarizing human-input prompts."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from dovo.common.exceptions import catch_failure
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
        """Resolve spec into a request, loading options and the displayed artifact and masking displayed text.

        An artifact whose manifest cannot be read or is not a valid manifest fails with
        ASK_ARTIFACT_MANIFEST_UNREADABLE, or ASK_OPTIONS_ARTIFACT_MANIFEST_UNREADABLE for options_from, and an
        operating-system read failure is recorded in the result's diagnostics.
        """
        with catch_failure("interaction.resolve", step=step_id) as caught:
            return resolve_request(step_id, spec, message, self._store, self._redactor)
        failure = caught.failure
        if failure is None:
            raise RuntimeError(f"Resolving the interaction of '{step_id}' neither returned a result nor raised.")
        return InteractionResolveResult(**failure.result_fields())

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
        answer: str | None, comment: str | None, comment_file: Path | None, answers_file: Path | None
    ) -> HeadlessArgumentsResult:
        """Read the comment and answers files and combine the resume answer flags into one headless input.

        A conflict, a malformed value or an invalid answers file is reported as INTERACTION_ARGUMENTS_INVALID, and a file
        that cannot be read as INTERACTION_ARGUMENT_FILE_UNREADABLE.
        """
        return parse_answer_arguments(answer, comment, comment_file, answers_file)

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
