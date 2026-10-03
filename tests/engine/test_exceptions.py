"""Contract tests for engine/exceptions.py: message and payload construction."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.core.inputs import InputResolveResult
from dovo.engine.exceptions import EngineError, EngineInputError, EngineResumeError, EngineSnapshotMissingError
from dovo.engine.models import EngineResumeStatus


class EngineInputErrorTests:
    @pytest.mark.parametrize(
        ("result", "expected"),
        [
            pytest.param(
                InputResolveResult(errors=["bad flag"], missing=["target"]), "bad flag", id="first-error-wins"
            ),
            pytest.param(
                InputResolveResult(missing=["target", "env"]), "Missing required input 'target'.", id="first-missing"
            ),
            pytest.param(InputResolveResult(), "Failed to resolve blueprint inputs.", id="fallback"),
        ],
    )
    def test_message_is_derived_from_the_resolve_result(self, result: InputResolveResult, expected: str) -> None:
        """[tier-1/unit] EngineInputError: the message is errors[0], else the first missing input, else the generic text, and the result is kept on .result."""
        error = EngineInputError(result)

        assert str(error) == expected
        assert error.result is result


class EngineResumeErrorTests:
    def test_error_carries_status_and_message(self) -> None:
        """[tier-1/unit] EngineResumeError: str() is the given message and .status is the given EngineResumeStatus."""
        error = EngineResumeError(EngineResumeStatus.NOT_FOUND, "no such run")

        assert str(error) == "no such run"
        assert error.status is EngineResumeStatus.NOT_FOUND


class EngineSnapshotMissingErrorTests:
    def test_message_names_the_missing_path(self) -> None:
        """[tier-1/unit] EngineSnapshotMissingError: the message embeds the path and .path is the exact Path passed in."""
        path = Path("snapshots/lint.yml")

        error = EngineSnapshotMissingError(path)

        assert str(error) == "Snapshot definition file is missing: 'snapshots/lint.yml'."
        assert error.path == path
        assert isinstance(error, EngineError)
