"""Unit tests for dovo.core.diagnostics.exceptions."""

from dovo.core.diagnostics.exceptions import CheckRegistrationError, DiagnosticsError


class DiagnosticsExceptionsTests:
    """Tests for doctor domain exception inheritance contracts."""

    def test_check_registration_error_inherits_doctor_error(self) -> None:
        """[tier-1/unit] CheckRegistrationError: verifies inheritance from DiagnosticsError and Exception."""
        assert issubclass(CheckRegistrationError, DiagnosticsError)
        assert issubclass(DiagnosticsError, Exception)

    def test_check_registration_error_instantiation_message(self) -> None:
        """[tier-1/unit] CheckRegistrationError: correctly carries message string."""
        exc = CheckRegistrationError("Check 'git.repo' is already registered.")
        assert str(exc) == "Check 'git.repo' is already registered."
