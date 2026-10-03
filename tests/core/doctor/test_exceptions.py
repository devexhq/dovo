"""Unit tests for dovo.core.doctor.exceptions."""

from dovo.core.doctor.exceptions import CheckRegistrationError, DoctorError


class DoctorExceptionsTests:
    """Tests for doctor domain exception inheritance contracts."""

    def test_check_registration_error_inherits_doctor_error(self) -> None:
        """[tier-1/unit] CheckRegistrationError: verifies inheritance from DoctorError and Exception."""
        assert issubclass(CheckRegistrationError, DoctorError)
        assert issubclass(DoctorError, Exception)

    def test_check_registration_error_instantiation_message(self) -> None:
        """[tier-1/unit] CheckRegistrationError: correctly carries message string."""
        exc = CheckRegistrationError("Check 'git.repo' is already registered.")
        assert str(exc) == "Check 'git.repo' is already registered."
