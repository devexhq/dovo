"""Exceptions of the doctor subsystem."""


class CheckRegistrationError(ValueError):
    """Raised when registering a check with an existing check_id, a programmer error."""
