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
from dovo.common.exceptions import (
    DomainError,
    Failure,
    FailureData,
    FailureFields,
    RecordedDiagnostics,
    WorkspaceError,
    catch_failure,
    catch_unexpected,
    escaped_failure_fields,
    fatal_failure_fields,
    workspace_not_initialized_error,
)
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
            error = DomainError(ErrorCode.WORKTREE_NOT_FOUND, "Worktree not found.")
        else:
            error = DomainError(ErrorCode.WORKTREE_NOT_FOUND, "Worktree not found.", remediations)

        assert error.error_code == ErrorCode.WORKTREE_NOT_FOUND
        assert error.message == "Worktree not found."
        assert error.remediations == expected
        assert error.data is None
        assert str(error) == "Worktree not found."

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


class CatchUnexpectedTests:
    def test_unexpected_exception_is_internal_error_and_recorded(self, frozen_diagnostic_clock: Path) -> None:
        """Suppress TypeError("bad operand") and set caught.failure to Failure(INTERNAL_ERROR, "Unexpected error during engine.step: bad operand", (), record)."""
        with catch_unexpected("engine.step", step="a") as caught:
            raise TypeError("bad operand")

        assert caught.failure == Failure(
            ErrorCode.INTERNAL_ERROR,
            "Unexpected error during engine.step: bad operand",
            (),
            _expected_record(frozen_diagnostic_clock),
        )
        assert read_single_event(frozen_diagnostic_clock)["context"] == {"step": "a"}

    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(
                DomainError(ErrorCode.WORKTREE_NOT_FOUND, "Worktree 'x' not found.", [REMEDIATION]), id="domain"
            ),
            pytest.param(LockTimeoutError("lock held"), id="lock_timeout"),
            pytest.param(KeyboardInterrupt(), id="keyboard_interrupt"),
        ],
    )
    def test_domain_error_lock_timeout_and_base_exceptions_propagate_unrecorded(
        self, frozen_diagnostic_clock: Path, error: BaseException
    ) -> None:
        """Re-raise the exception out of the with block and create no logs directory."""
        with pytest.raises(type(error)) as raised, catch_unexpected("engine.step"):
            raise error

        assert raised.value is error
        assert not frozen_diagnostic_clock.parent.parent.exists()


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


class EscapedFailureFieldsTests:
    """Contract tests for the header of a failure no command result carries."""

    def test_domain_error_reports_its_own_header(self, tmp_path: Path) -> None:
        """Give the code, the message then each note, the remediations, and the carried records."""
        record = DiagnosticRecord(event_id=FROZEN_EVENT_ID, log_path=tmp_path / "e.json", level=DiagnosticLevel.ERROR)
        error = DomainError(
            ErrorCode.CONFIG_UNREADABLE, "unreadable", [REMEDIATION], data=RecordedDiagnostics((record,))
        )
        error.add_note("first note")
        error.add_note("second note")

        assert escaped_failure_fields(error) == FailureFields(
            error_code=ErrorCode.CONFIG_UNREADABLE,
            errors=["unreadable", "first note", "second note"],
            remediations=[REMEDIATION],
            diagnostics=[record],
        )

    def test_lock_timeout_reports_workspace_lock_timeout(self) -> None:
        error = LockTimeoutError("Timed out waiting for workspace lock.")
        error.add_note("Rollback failed.")

        assert escaped_failure_fields(error) == FailureFields(
            error_code=ErrorCode.WORKSPACE_LOCK_TIMEOUT,
            errors=["Timed out waiting for workspace lock.", "Rollback failed."],
            remediations=[
                Remediation(message="Wait for the other dovo process to release the workspace lock, then retry.")
            ],
            diagnostics=[],
        )


class FatalFailureFieldsTests:
    def test_unexpected_exception_is_internal_error_with_its_record(self, tmp_path: Path) -> None:
        record = DiagnosticRecord(event_id=FROZEN_EVENT_ID, log_path=tmp_path / "e.json", level=DiagnosticLevel.ERROR)
        error = RuntimeError("missing record.id")
        error.add_note("a note")

        assert fatal_failure_fields(error, record) == FailureFields(
            error_code=ErrorCode.INTERNAL_ERROR,
            errors=["A fatal unexpected error occurred. Details: missing record.id", "a note"],
            remediations=[],
            diagnostics=[record],
        )


class WorkspaceNotInitializedErrorTests:
    @pytest.mark.parametrize(
        ("identity_present", "expected"),
        [
            pytest.param(
                False,
                (Remediation(message="Run 'dovo init' to initialize the workspace.", command="dovo init"),),
                id="absent",
            ),
            pytest.param(
                True,
                (
                    Remediation(message="Run 'dovo init' to initialize the workspace.", command="dovo init"),
                    Remediation(
                        message="Run 'dovo init --id <project-id> --force' to replace the unusable identity.",
                        command="dovo init --id <project-id> --force",
                    ),
                ),
                id="present",
            ),
        ],
    )
    def test_factory_builds_code_message_and_remediations(
        self, tmp_path: Path, identity_present: bool, expected: tuple[Remediation, ...]
    ) -> None:
        identity_path = tmp_path / ".dovo" / "project.json"

        error = workspace_not_initialized_error(identity_path, identity_present=identity_present, diagnostic=None)

        assert isinstance(error, WorkspaceError)
        assert (error.error_code, error.message, error.remediations, error.data) == (
            ErrorCode.WORKSPACE_NOT_INITIALIZED,
            f"Workspace is not initialized: no valid project identity at '{identity_path}'.",
            expected,
            RecordedDiagnostics(records=()),
        )
