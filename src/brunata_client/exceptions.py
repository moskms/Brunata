class BrunataLoginError(Exception):
    """Raised when login fails (wrong credentials, B2C error, etc.)"""


class BrunataDataError(Exception):
    """Raised when consumption data cannot be fetched or parsed."""


class BrunataHttpError(BrunataDataError):
    """A BrunataDataError with the HTTP status code attached.

    Lets callers (e.g. history import progress reporting) distinguish things
    like 429/403 from other failures without parsing the error message.
    """

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(message)


class BrunataSessionError(Exception):
    """Raised when the session is invalid or the token has expired."""
