"""Minimal HTTP plumbing on top of :mod:`urllib` (no third-party runtime deps).

The transport is a plain callable so tests can inject a fake without monkeypatching
sockets::

    def fake(url, headers, body, timeout):
        return HttpResponse(200, b'{"value": []}', {})
"""

from __future__ import annotations

import contextlib
import gzip
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from .errors import TuikNetworkError

__all__ = [
    "HttpResponse",
    "Transport",
    "build_url",
    "redact",
    "urllib_transport",
]

USER_AGENT = "tuik-data/0.1.0 (+https://github.com/YusufBeratZengin/tuik-data)"


@dataclass(frozen=True)
class HttpResponse:
    """Status, body and headers of one HTTP exchange."""

    status: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json_text(self) -> str:
        return self.text()


class Transport(Protocol):
    """Callable transport signature used by the client and the token manager."""

    def __call__(
        self,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse: ...


def build_url(base: str, path: str, params: Mapping[str, str | int | None]) -> str:
    """Join ``base`` + ``path`` and append non-empty query parameters."""
    url = f"{base.rstrip('/')}/{path.lstrip('/')}"
    clean = {key: str(value) for key, value in params.items() if value not in (None, "")}
    if not clean:
        return url
    return f"{url}?{urllib.parse.urlencode(clean, safe=',:')}"


def urllib_transport(
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: float,
) -> HttpResponse:
    """Default transport: one GET/POST through :mod:`urllib` with gzip support."""
    request = urllib.request.Request(url, data=body, method="POST" if body else "GET")
    for name, value in headers.items():
        request.add_header(name, value)
    request.add_header("User-Agent", headers.get("User-Agent", USER_AGENT))
    request.add_header("Accept-Encoding", "gzip")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
            if response.headers.get("Content-Encoding") == "gzip":
                payload = gzip.decompress(payload)
            return HttpResponse(
                status=response.status,
                body=payload,
                headers={key.lower(): value for key, value in response.headers.items()},
            )
    except urllib.error.HTTPError as exc:  # 4xx/5xx still carry a body we want to show
        payload = exc.read() or b""
        if exc.headers is not None and exc.headers.get("Content-Encoding") == "gzip":
            with contextlib.suppress(OSError):  # pragma: no cover - corrupt gzip body
                payload = gzip.decompress(payload)
        return HttpResponse(
            status=exc.code,
            body=payload,
            headers={key.lower(): value for key, value in (exc.headers or {}).items()},
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TuikNetworkError(f"could not reach {url}: {exc}") from exc


_SECRET_PATTERN = re.compile(
    r"(?i)\b(bearer|api[_-]?key|access[_-]?token)\b\s*[:=]?\s*[A-Za-z0-9._~+/=-]{4,}"
)
_LONG_TOKEN = re.compile(r"\b[A-Za-z0-9_-]{33,}\b")


def redact(text: str) -> str:
    """Mask bearer tokens, API keys and long opaque tokens in ``text``.

    Error messages may echo a URL or a server body; neither should ever carry a
    credential into a log file.
    """
    masked = _SECRET_PATTERN.sub(lambda match: f"{match.group(1)} ***", text)
    return _LONG_TOKEN.sub("***", masked)
