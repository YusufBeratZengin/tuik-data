"""Exception hierarchy for :mod:`tuik_data`.

Every error raised by this package derives from :class:`TuikError`, so callers can
catch one type. Messages are written for humans first: they name the problem, the
failing request (without secrets) and the next thing to try.
"""

from __future__ import annotations

__all__ = [
    "TuikAuthError",
    "TuikConfigError",
    "TuikDimensionError",
    "TuikEmptyResult",
    "TuikError",
    "TuikHTTPError",
    "TuikNetworkError",
    "TuikNotFoundError",
    "TuikParseError",
    "TuikRateLimitError",
]


class TuikError(Exception):
    """Base class for all errors raised by tuik-data."""


class TuikConfigError(TuikError):
    """The client is misconfigured (missing API key, unknown indicator code, ...)."""


class TuikAuthError(TuikError):
    """Authentication failed: missing, invalid or expired API key / bearer token."""


class TuikNetworkError(TuikError):
    """The request never produced an HTTP response (DNS, TLS, timeout, reset)."""


class TuikHTTPError(TuikError):
    """The server answered with an unexpected status code."""

    def __init__(self, status: int, url: str, body: str = "") -> None:
        self.status = status
        self.url = url
        self.body = body
        snippet = " ".join(body.split())[:300]
        message = f"HTTP {status} for {url}"
        if snippet:
            message = f"{message}: {snippet}"
        super().__init__(message)


class TuikNotFoundError(TuikHTTPError):
    """The requested dataflow, key or resource does not exist (HTTP 404)."""


class TuikRateLimitError(TuikHTTPError):
    """The server asked us to slow down (HTTP 429)."""


class TuikParseError(TuikError):
    """A response could not be parsed as JSON-stat 2.0 or SDMX-JSON."""


class TuikEmptyResult(TuikError):
    """The query succeeded but returned no observations."""


class TuikDimensionError(TuikEmptyResult):
    """A dimension filter produced no data; the message lists valid codes."""

    def __init__(self, message: str, available: dict[str, list[str]] | None = None) -> None:
        self.available = available or {}
        super().__init__(message)
