"""Domain exceptions for Dovo doctor subsystem."""


class DoctorError(Exception):
    """Base exception for doctor domain errors."""


class CheckRegistrationError(DoctorError):
    """Raised when registering a check with an existing check_id."""
