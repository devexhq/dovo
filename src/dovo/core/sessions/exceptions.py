"""The sessions package's domain error and its step-logs failure data."""

from dataclasses import dataclass

from dovo.common.exceptions import DomainError, FailureData


class SessionError(DomainError):
    """Business failure looking up a session or reading its persisted diff or logs."""


@dataclass(frozen=True, slots=True)
class SessionStepLogsAvailable(FailureData):
    """A SESSION_STEP_NOT_FOUND or SESSION_ATTEMPT_NOT_FOUND failure's data: the step ids or attempts that were captured."""

    steps: tuple[str, ...] = ()
    attempts: tuple[int, ...] = ()
