class BrunataLoginError(Exception):
    """Raised when login fails for any reason — wrong credentials, an
    unexpected/transient response at any of the 5 Keycloak PKCE steps
    (network hiccup, Brunata server error, etc.). See
    BrunataInvalidCredentialsError for the one case that's CONFIRMED to be
    wrong credentials specifically, as opposed to everything else here,
    which is not.
    """


class BrunataInvalidCredentialsError(BrunataLoginError):
    """Raised specifically when Brunata's own login page confirms wrong
    username/password (the Keycloak login form re-renders with an error
    instead of redirecting onward — see client.py's login(), step 3).

    Deliberately a distinct type from the general BrunataLoginError above:
    callers (custom_components/brunata/__init__.py, config_flow.py) use
    this to decide whether to demand the user re-enter credentials
    (ConfigEntryAuthFailed / "invalid_auth") or just retry later
    (ConfigEntryNotReady / "unknown") — conflating the two meant a
    transient 503 from Brunata's own authorize endpoint was previously
    mis-reported to the user as a wrong password (confirmed real case,
    2026-08-12).
    """


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
