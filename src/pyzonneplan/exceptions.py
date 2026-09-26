"""Asynchronous Python client for Zonneplan."""


class ZonneplanError(Exception):
    """Generic exception for Zonneplan errors."""


class ZonneplanConnectionError(ZonneplanError):
    """Exception raised for connection errors."""


class ZonneplanTimeoutError(ZonneplanError):
    """Exception raised for timeout errors."""


class ZonneplanRequestError(ZonneplanError):
    """Exception raised when the API rejects an authenticated request as invalid (HTTP 400)."""


class ZonneplanAuthenticationError(ZonneplanError):
    """Exception raised for authentication errors."""


class ZonneplanInvalidOtpError(ZonneplanAuthenticationError):
    """Exception raised when the submitted one-time password is rejected."""
