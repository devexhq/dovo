"""Contract tests for engine/exceptions.py: message and payload construction."""

from __future__ import annotations

from pathlib import Path

from dovo.common.error_codes import ErrorCode
from dovo.common.exceptions import DomainError
from dovo.core.inputs import InputResolveResult
from dovo.engine.exceptions import EngineError, EngineInputError, EngineSnapshotMissingError


class EngineInputErrorTests:
    def test_carries_message_result_and_code(self) -> None:
        result = InputResolveResult(missing=["target"])

        error = EngineInputError(result, "msg")

        assert str(error) == "msg"
        assert error.message == "msg"
        assert error.result is result
        assert error.error_code is ErrorCode.BLUEPRINT_INPUT_INVALID
        assert isinstance(error, DomainError)


class EngineSnapshotMissingErrorTests:
    def test_message_names_the_missing_path(self) -> None:
        path = Path("snapshots/lint.yml")

        error = EngineSnapshotMissingError(path)

        assert str(error) == "Snapshot definition file is missing: 'snapshots/lint.yml'."
        assert error.path == path
        assert error.error_code is ErrorCode.SESSION_SNAPSHOT_MISSING
        assert isinstance(error, EngineError)
        assert isinstance(error, DomainError)
