class BrunataLoginError(Exception):
    """Raised when login fails (wrong credentials, B2C error, etc.)"""


class BrunataDataError(Exception):
    """Raised when consumption data cannot be fetched or parsed."""


class BrunataSessionError(Exception):
    """Raised when the session is invalid or the token has expired."""
