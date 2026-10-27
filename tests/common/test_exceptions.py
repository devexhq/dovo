"""Tests for DomainError and the catch_failure entrypoint context manager."""

from __future__ import annotations

import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord
from dovo.common.error_codes import ErrorCode
from dovo.common.exceptions import DomainError, Failure, FailureData, FailureFields, catch_failure
from dovo.common.lock import LockTimeoutError
from dovo.common.models import Remediation
from tests.harness.diagnostic_log import FROZEN_EVENT_ID, FROZEN_TIMESTAMP, read_single_event

REMEDIATION: Final[Remediation] = Remediation(message="Run dovo worktree list.", command="dovo worktree list")


@dataclass(frozen=True, slots=True)
class _Conflict(FailureData):
    """A package's typed failure data: the paths that conflicted."""

    paths: tuple[str, ...]


CONFLICT: Final[_Conflict] = _Conflict(paths=("a.py",))


def _raise_domain_error_from_os_error() -> None:
    """Raise a WORKTREE_NOT_FOUND DomainError from a caught FileNotFoundError."""
    try:
        raise FileNotFoundError("worktree.json")
    except OSError as os_error:
        raise DomainError(ErrorCode.WORKTREE_NOT_FOUND, "Worktree 'x' not found.", [REMEDIATION]) from os_error


def _raise_type_error() -> None:
    """Raise TypeError("bad operand")."""
    raise TypeError("bad operand")


def _expected_record(event_file: Path) -> DiagnosticRecord:
    """Return the error-level DiagnosticRecord for the frozen event file."""
    return DiagnosticRecord(event_id=FROZEN_EVENT_ID, log_path=event_file, level=DiagnosticLevel.ERROR)


class DomainErrorTests:
    @pytest.mark.parametrize(
        ("remediations", "expected"),
        [
            pytest.param(None, (), id="default"),
            pytest.param(
                [Remediation(message="Run dovo init.", command="dovo init")],
                (Remediation(message="Run dovo init.", command="dovo init"),),
                id="list_to_tuple",
            ),
        ],
    )
    def test_carries_code_message_and_remediations(
        self, remediations: list[Remediation] | None, expected: tuple[Remediation, ...]
    ) -> None:
        """Expose error_code, message and remediations as a tuple, with str() equal to message (None means the argument is omitted)."""
        if remediations is None:
            error = DomainError(ErrorCode.CONFIG_NOT_FOUND, "Config not found.")
        else:
            error = DomainError(ErrorCode.CONFIG_NOT_FOUND, "Config not found.", remediations)

        assert error.error_code == ErrorCode.CONFIG_NOT_FOUND
        assert error.message == "Config not found."
        assert error.remediations == expected
        assert error.data is None
        assert str(error) == "Config not found."

    def test_carries_data(self) -> None:
        """Expose the data passed as data= unchanged."""
        error = DomainError(ErrorCode.WORKTREE_APPLY_CONFLICT, "Conflicts detected.", data=CONFLICT)

        assert error.data == CONFLICT


class CatchFailureTests:
    def test_completed_block_leaves_failure_none_and_records_nothing(self, frozen_diagnostic_clock: Path) -> None:
        """Leave caught.failure None and create no <DOVO_HOME>/logs directory when the block completes."""
        with catch_failure("worktree.apply") as caught:
            pass

        assert caught.failure is None
        assert not frozen_diagnostic_clock.parent.parent.exists()

    @pytest.mark.parametrize(
        "cause", [pytest.param(None, id="no_cause"), pytest.param(ValueError("bad"), id="value_error_cause")]
    )
    def test_domain_error_without_os_cause_is_copied_unrecorded(
        self, frozen_diagnostic_clock: Path, cause: ValueError | None
    ) -> None:
        """Suppress the DomainError, set caught.failure to Failure(WORKTREE_NOT_FOUND, message, remediations tuple, None) and create no logs directory."""
        with catch_failure("worktree.apply") as caught:
            raise DomainError(ErrorCode.WORKTREE_NOT_FOUND, "Worktree 'x' not found.", [REMEDIATION]) from cause

        assert caught.failure == Failure(ErrorCode.WORKTREE_NOT_FOUND, "Worktree 'x' not found.", (REMEDIATION,), None)
        assert not frozen_diagnostic_clock.parent.parent.exists()

    def test_domain_error_data_is_carried_onto_the_failure(self, frozen_diagnostic_clock: Path) -> None:
        """Set caught.failure.data to the data the DomainError was raised with."""
        with catch_failure("worktree.apply") as caught:
            raise DomainError(ErrorCode.WORKTREE_APPLY_CONFLICT, "Conflicts detected.", data=CONFLICT)

        assert caught.failure == Failure(ErrorCode.WORKTREE_APPLY_CONFLICT, "Conflicts detected.", (), None, CONFLICT)

    def test_domain_error_from_os_error_records_the_cause(self, frozen_diagnostic_clock: Path) -> None:
        """Write one error-level event for the FileNotFoundError cause with operation worktree.apply and context {"worktree_id": "wt1"}, and set caught.failure.diagnostic to its DiagnosticRecord."""
        cause: OSError | None = None
        with catch_failure("worktree.apply", worktree_id="wt1") as caught:
            try:
                raise FileNotFoundError("worktree.json")
            except OSError as os_error:
                cause = os_error
                raise DomainError(ErrorCode.WORKTREE_NOT_FOUND, "Worktree 'x' not found.", [REMEDIATION]) from os_error

        assert cause is not None
        assert caught.failure == Failure(
            ErrorCode.WORKTREE_NOT_FOUND,
            "Worktree 'x' not found.",
            (REMEDIATION,),
            _expected_record(frozen_diagnostic_clock),
        )
        assert read_single_event(frozen_diagnostic_clock) == {
            "event_id": "exc_3f9a1c07b2de",
            "timestamp": FROZEN_TIMESTAMP,
            "level": "error",
            "operation": "worktree.apply",
            "command": None,
            "context": {"worktree_id": "wt1"},
            "exception_type": "FileNotFoundError",
            "exception_message": "worktree.json",
            "formatted_traceback": "".join(traceback.format_exception(cause)),
        }

    def test_unexpected_exception_is_internal_error_and_recorded(self, frozen_diagnostic_clock: Path) -> None:
        """Suppress TypeError("bad operand"), write its error-level event and set caught.failure to Failure(INTERNAL_ERROR, "Unexpected error during worktree.apply: bad operand", (), record)."""
        error = TypeError("bad operand")
        with catch_failure("worktree.apply") as caught:
            raise error

        assert caught.failure == Failure(
            ErrorCode.INTERNAL_ERROR,
            "Unexpected error during worktree.apply: bad operand",
            (),
            _expected_record(frozen_diagnostic_clock),
        )
        assert read_single_event(frozen_diagnostic_clock) == {
            "event_id": "exc_3f9a1c07b2de",
            "timestamp": FROZEN_TIMESTAMP,
            "level": "error",
            "operation": "worktree.apply",
            "command": None,
            "context": {},
            "exception_type": "TypeError",
            "exception_message": "bad operand",
            "formatted_traceback": "".join(traceback.format_exception(error)),
        }

    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(LockTimeoutError("lock held"), id="lock_timeout"),
            pytest.param(KeyboardInterrupt(), id="keyboard_interrupt"),
        ],
    )
    def test_lock_timeout_and_base_exceptions_propagate_unrecorded(
        self, frozen_diagnostic_clock: Path, error: BaseException
    ) -> None:
        """Re-raise the exception out of the with block and create no logs directory."""
        with pytest.raises(type(error)) as raised, catch_failure("worktree.apply"):
            raise error

        assert raised.value is error
        assert not frozen_diagnostic_clock.parent.parent.exists()

    @pytest.mark.parametrize(
        ("raise_in_block", "expected"),
        [
            pytest.param(
                _raise_domain_error_from_os_error,
                Failure(ErrorCode.WORKTREE_NOT_FOUND, "Worktree 'x' not found.", (REMEDIATION,), None),
                id="domain",
            ),
            pytest.param(
                _raise_type_error,
                Failure(ErrorCode.INTERNAL_ERROR, "Unexpected error during worktree.apply: bad operand", (), None),
                id="unexpected",
            ),
        ],
    )
    def test_unwritable_logs_dir_sets_failure_without_diagnostic(
        self, frozen_diagnostic_clock: Path, raise_in_block: Callable[[], None], expected: Failure
    ) -> None:
        """With <DOVO_HOME>/logs a regular file, call raise_in_block inside catch_failure, set caught.failure == expected and let no exception escape."""
        logs_dir = frozen_diagnostic_clock.parent.parent
        logs_dir.parent.mkdir(parents=True, exist_ok=True)
        logs_dir.write_text("not a directory", encoding="utf-8")

        with catch_failure("worktree.apply") as caught:
            raise_in_block()

        assert caught.failure == expected
        assert logs_dir.read_text(encoding="utf-8") == "not a directory"


ERROR_RECORD: Final[DiagnosticRecord] = DiagnosticRecord(
    event_id="exc_error", log_path=Path("/logs/exc_error.json"), level=DiagnosticLevel.ERROR
)
PRIOR_RECORD: Final[DiagnosticRecord] = DiagnosticRecord(
    event_id="exc_prior", log_path=Path("/logs/exc_prior.json"), level=DiagnosticLevel.ERROR
)
DEBUG_RECORD: Final[DiagnosticRecord] = DiagnosticRecord(
    event_id="exc_debug", log_path=Path("/logs/exc_debug.json"), level=DiagnosticLevel.DEBUG
)


class FailureResultFieldsTests:
    @pytest.mark.parametrize(
        ("diagnostic", "prior", "expected_diagnostics"),
        [
            pytest.param(None, (), [], id="no_record"),
            pytest.param(ERROR_RECORD, (), [ERROR_RECORD], id="error_record"),
            pytest.param(DEBUG_RECORD, (), [], id="debug_record_dropped"),
            pytest.param(ERROR_RECORD, (PRIOR_RECORD,), [PRIOR_RECORD, ERROR_RECORD], id="prior_first"),
            pytest.param(None, (PRIOR_RECORD,), [PRIOR_RECORD], id="prior_only"),
        ],
    )
    def test_returns_code_message_remediations_and_diagnostics(
        self,
        diagnostic: DiagnosticRecord | None,
        prior: tuple[DiagnosticRecord, ...],
        expected_diagnostics: list[DiagnosticRecord],
    ) -> None:
        """Return the code, [message], the remediations as a list, and prior_diagnostics followed by the record only when it is error-level."""
        failure = Failure(ErrorCode.WORKTREE_NOT_FOUND, "Worktree 'x' not found.", (REMEDIATION,), diagnostic)

        assert failure.result_fields(prior_diagnostics=prior) == FailureFields(
            error_code=ErrorCode.WORKTREE_NOT_FOUND,
            errors=["Worktree 'x' not found."],
            remediations=[REMEDIATION],
            diagnostics=expected_diagnostics,
        )
