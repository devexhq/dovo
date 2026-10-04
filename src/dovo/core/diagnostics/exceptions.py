"""Domain exceptions for Dovo doctor subsystem."""


class DiagnosticsError(Exception):
    """Base exception for doctor domain errors."""


class CheckRegistrationError(DiagnosticsError):
    """Raised when registering a check with an existing check_id."""
