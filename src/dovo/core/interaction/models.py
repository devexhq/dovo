"""Request, answer and result models for human-input prompts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from dovo.common.models import BaseResult
from dovo.core.catalog.definitions import CommentPolicy, InteractionMode

COMMENT_MAX_CHARACTERS = 8000
PREVIEW_MAX_LINES = 200
PREVIEW_MAX_BYTES = 1_048_576


class RequestOption(BaseModel):
    """One option of a resolved request; `comment` is the effective policy after the interaction default is applied."""

    model_config = {"extra": "forbid", "strict": True}

    value: str
    label: str
    fail: bool = False
    comment: CommentPolicy = "disabled"


class RequestComment(BaseModel):
    """Resolved comment configuration of a request."""

    model_config = {"extra": "forbid", "strict": True}

    policy: CommentPolicy = "disabled"
    message: str = "Comment"


class RequestArtifact(BaseModel):
    """Artifact shown with a request, pinned to the revision and digest read at ask time."""

    model_config = {"extra": "forbid", "strict": True}

    name: str
    revision: int = Field(ge=1)
    digest: str = ""
    preview: list[str] = Field(default_factory=list)


class InteractionRequest(BaseModel):
    """Fully resolved prompt; this is also what a leaf persists as `pending_ask`."""

    model_config = {"extra": "forbid", "strict": True}

    step_id: str
    mode: InteractionMode
    message: str
    options: list[RequestOption] = Field(default_factory=list)
    comment: RequestComment = Field(default_factory=RequestComment)
    artifact: RequestArtifact | None = None


class InteractionAnswer(BaseModel):
    """A human's answer to an InteractionRequest."""

    model_config = {"extra": "forbid", "strict": True}

    selected: list[str] = []
    comment: str | None = None
    text: str | None = None


class HeadlessAnswerInput(BaseModel):
    """Answer supplied to `dovo resume` before the stored request's mode is known."""

    model_config = {"extra": "forbid", "strict": True}

    step_id: str
    value: str | None = None
    selected: list[str] | None = None
    comment: str | None = None
    text: str | None = None


class InteractionResolveStatus(StrEnum):
    """Classified outcomes for resolving an interaction block into a request."""

    OK = "ok"
    NO_OPTIONS = "no_options"
    ARTIFACT_NOT_FOUND = "artifact_not_found"
    OPTIONS_ARTIFACT_NOT_FOUND = "options_artifact_not_found"
    OPTIONS_INVALID = "options_invalid"
    OPTIONS_DUPLICATE = "options_duplicate"
    OPTIONS_EMPTY = "options_empty"


class InteractionResolveResult(BaseResult):
    """Result of resolving an interaction block; `request` is set for OK and NO_OPTIONS."""

    status: InteractionResolveStatus
    request: InteractionRequest | None = None

    @property
    def ok(self) -> bool:
        """Return True when a request (possibly with no options) was resolved."""
        return self.status in (InteractionResolveStatus.OK, InteractionResolveStatus.NO_OPTIONS)


class InteractionAnswerStatus(StrEnum):
    """Classified outcomes for validating or parsing an answer."""

    OK = "ok"
    INVALID = "invalid"


class InteractionAnswerResult(BaseResult):
    """Result of validating or parsing an answer; `answer` is the normalized answer when OK."""

    status: InteractionAnswerStatus
    answer: InteractionAnswer | None = None

    @property
    def ok(self) -> bool:
        """Return True when the answer is valid."""
        return self.status == InteractionAnswerStatus.OK


class HeadlessArgumentsResult(BaseResult):
    """Result of combining `--answer`, `--comment` and `--answers-file` into one HeadlessAnswerInput."""

    status: InteractionAnswerStatus
    supplied: HeadlessAnswerInput | None = None

    @property
    def ok(self) -> bool:
        """Return True when the arguments form one answer."""
        return self.status == InteractionAnswerStatus.OK
