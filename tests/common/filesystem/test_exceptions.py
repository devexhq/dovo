"""Contract tests for common filesystem exceptions."""

from __future__ import annotations

from dovo.common.filesystem import InvalidGlobalRootError


class InvalidGlobalRootErrorTests:
    """Contract tests for invalid global-root failures."""

    def test_invalid_global_root_error_preserves_diagnostic_message(self) -> None:
        message = "Global root contains a .git directory"

        error = InvalidGlobalRootError(message)

        assert isinstance(error, ValueError)
        assert str(error) == message
