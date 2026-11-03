"""API-key to bearer-token lifecycle for the TÜİK SDMX service.

TÜİK protects ``nsiws.tuik.gov.tr`` with short-lived (<5 min) bearer tokens issued
by the TÜİK login realm.  You generate an API key once in the Data Portal
(SMS-verified) and keep it in the ``TUIK_API_KEY`` environment variable; this
module exchanges it for a token, caches the token in memory and renews it shortly
before it expires.

Nothing here writes the key to disk, and error messages never echo it.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
from collections.abc import Callable, Mapping

from ._http import HttpResponse, Transport, redact, urllib_transport
from .errors import TuikAuthError, TuikConfigError, TuikParseError

__all__ = [
    "API_KEY_ENV",
    "DEFAULT_CLIENT_ID",
    "DEFAULT_TOKEN_URL",
    "TokenManager",
]

API_KEY_ENV = "TUIK_API_KEY"
DEFAULT_TOKEN_URL = "https://giris.tuik.gov.tr/realms/web/protocol/openid-connect/token"
DEFAULT_CLIENT_ID = "veriportali"
DEFAULT_EXPIRES_IN = 300.0
REFRESH_MARGIN = 30.0


class TokenManager:
    """Exchange an API key for a bearer token and refresh it before expiry.

    Parameters
    ----------
    api_key:
        The SMS-verified key from the TÜİK Data Portal.  When omitted, the
        ``TUIK_API_KEY`` environment variable is used.
    transport:
        Injectable HTTP callable, mainly for tests.
    clock:
        Monotonic clock, injectable for expiry tests.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        token_url: str = DEFAULT_TOKEN_URL,
        client_id: str = DEFAULT_CLIENT_ID,
        transport: Transport | None = None,
        clock: Callable[[], float] = time.monotonic,
        refresh_margin: float = REFRESH_MARGIN,
        timeout: float = 30.0,
    ) -> None:
        self._api_key = (api_key or os.environ.get(API_KEY_ENV) or "").strip()
        self._token_url = token_url
        self._client_id = client_id
        self._transport: Transport = transport or urllib_transport
        self._clock = clock
        self._refresh_margin = refresh_margin
        self._timeout = timeout
        self._token: str = ""
        self._expires_at: float = 0.0
        self.refresh_count = 0

    # -- state ---------------------------------------------------------------
    @property
    def has_key(self) -> bool:
        """True when an API key is configured (the key itself is never exposed)."""
        return bool(self._api_key)

    @property
    def is_valid(self) -> bool:
        """True while the cached token is still usable."""
        return bool(self._token) and self._clock() < self._expires_at

    def invalidate(self) -> None:
        """Forget the cached token so the next :meth:`token` call refreshes it."""
        self._token = ""
        self._expires_at = 0.0

    # -- token ---------------------------------------------------------------
    def token(self) -> str:
        """Return a valid bearer token, refreshing it when necessary."""
        if self.is_valid:
            return self._token
        return self._refresh()

    def headers(self) -> dict[str, str]:
        """Authorization header for an SDMX request."""
        return {"Authorization": f"Bearer {self.token()}"}

    def _refresh(self) -> str:
        if not self._api_key:
            raise TuikConfigError(
                "No API key configured. Generate an SMS-verified key at "
                "https://veriportali.tuik.gov.tr and export it as "
                f"{API_KEY_ENV}=xxxxxx (the key is never written to disk)."
            )
        form = urllib.parse.urlencode(
            {
                "grant_type": "password",
                "client_id": self._client_id,
                "api_key": self._api_key,
            }
        ).encode("ascii")
        response: HttpResponse = self._transport(
            self._token_url,
            {
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            form,
            self._timeout,
        )
        if response.status in (400, 401, 403):
            raise TuikAuthError(
                f"TÜİK token service rejected the API key (HTTP {response.status}). "
                "The key may be expired or revoked; generate a new one in the Data Portal."
            )
        if response.status != 200:
            raise TuikAuthError(
                f"TÜİK token service answered HTTP {response.status}; "
                "this is usually temporary, retry in a minute."
            )
        try:
            payload = json.loads(response.text())
        except json.JSONDecodeError as exc:
            raise TuikParseError(
                f"token service did not return JSON: {redact(response.text()[:120])}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise TuikParseError("token service returned an unexpected payload")
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise TuikAuthError(
                "token service did not return 'access_token'; "
                "the API key is probably not activated for SDMX access"
            )
        expires_in = payload.get("expires_in", DEFAULT_EXPIRES_IN)
        try:
            lifetime = float(expires_in)
        except (TypeError, ValueError):
            lifetime = DEFAULT_EXPIRES_IN
        self._token = token
        self._expires_at = self._clock() + max(0.0, lifetime - self._refresh_margin)
        self.refresh_count += 1
        return token
