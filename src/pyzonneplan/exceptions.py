"""Asynchronous Python client for Zonneplan."""


class ZonneplanError(Exception):
    """Generic exception for Zonneplan errors."""


class ZonneplanConnectionError(ZonneplanError):
    """Exception raised for connection errors."""


class ZonneplanTimeoutError(ZonneplanError):
    """Exception raised for timeout errors."""


class ZonneplanRequestError(ZonneplanError):
    """Exception raised when the API rejects an authenticated request (HTTP 4xx other than 401/403/429).

    Not retried: sending the same request again would fail the same way.
    """


class ZonneplanNotFoundError(ZonneplanRequestError):
    """Exception raised when the requested resource doesn't exist (HTTP 404), e.g. a device the account doesn't have."""


class ZonneplanRateLimitError(ZonneplanError):
    """Exception raised when the API rate limit is hit (HTTP 429).

    Not a ZonneplanConnectionError, so the client doesn't retry it: retrying
    would only use up the limit further. ``retry_after`` holds the seconds from
    the ``Retry-After`` header, or ``None`` when it was missing or not a number.
    """

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        """Initialize the exception with the server's Retry-After delay."""
        super().__init__(message)
        self.retry_after = retry_after


class ZonneplanAuthenticationError(ZonneplanError):
    """Exception raised for authentication errors."""


class ZonneplanInvalidOtpError(ZonneplanAuthenticationError):
    """Exception raised when the submitted one-time password is rejected."""
