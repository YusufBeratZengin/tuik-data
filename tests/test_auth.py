"""Token lifecycle tests (no network, no real keys)."""

from __future__ import annotations

import json

import pytest

from tests.conftest import FakeTransport, json_response
from tuik_data._http import HttpResponse
from tuik_data.auth import API_KEY_ENV, TokenManager
from tuik_data.errors import TuikAuthError, TuikConfigError, TuikParseError


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def valid(token_manager: TokenManager) -> bool:
    """Read ``is_valid`` through a function so mypy does not narrow the property."""
    return token_manager.is_valid


def manager(transport: FakeTransport, clock: Clock | None = None, **kwargs: object) -> TokenManager:
    return TokenManager(
        "fake-api-key-xxxxxx",
        transport=transport,
        clock=clock or Clock(),
        timeout=1.0,
        **kwargs,  # type: ignore[arg-type]
    )


class TestTokenLifecycle:
    def test_token_is_exchanged_for_a_bearer_header(self) -> None:
        transport = FakeTransport()
        token = manager(transport).token()
        assert token.startswith("fake-token")
        call = transport.calls[0]
        assert "openid-connect/token" in call.url
        assert b"grant_type=password" in (call.body or b"")
        assert b"api_key=fake-api-key-xxxxxx" in (call.body or b"")

    def test_headers(self) -> None:
        token_manager = manager(FakeTransport())
        assert token_manager.headers()["Authorization"].startswith("Bearer ")

    def test_token_is_cached(self) -> None:
        transport = FakeTransport()
        token_manager = manager(transport)
        token_manager.token()
        token_manager.token()
        assert len(transport.calls) == 1
        assert token_manager.refresh_count == 1

    def test_expiry_triggers_refresh(self) -> None:
        clock = Clock()
        transport = FakeTransport()
        token_manager = manager(transport, clock, refresh_margin=10.0)
        token_manager.token()
        assert valid(token_manager)
        clock.now = 1289.0  # inside 1000 + (300 - 10)
        token_manager.token()
        assert len(transport.calls) == 1
        clock.now = 1291.0  # past the refresh margin
        assert not valid(token_manager)
        token_manager.token()
        assert len(transport.calls) == 2

    def test_invalidate_forces_refresh(self) -> None:
        transport = FakeTransport()
        token_manager = manager(transport)
        token_manager.token()
        token_manager.invalidate()
        token_manager.token()
        assert len(transport.calls) == 2

    def test_short_lifetime_never_stays_valid(self) -> None:
        transport = FakeTransport(
            {"openid-connect/token": json_response({"access_token": "t", "expires_in": 5})}
        )
        clock = Clock()
        token_manager = manager(transport, clock, refresh_margin=30.0)
        token_manager.token()
        assert not valid(token_manager)  # 5s lifetime minus 30s margin = already stale


class TestAuthErrors:
    def test_missing_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(API_KEY_ENV, raising=False)
        token_manager = TokenManager(transport=FakeTransport())
        assert not token_manager.has_key
        with pytest.raises(TuikConfigError, match=API_KEY_ENV):
            token_manager.token()

    def test_key_from_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(API_KEY_ENV, "env-key-xxxxxx")
        token_manager = TokenManager(transport=FakeTransport())
        assert token_manager.has_key
        assert token_manager.token()

    def test_rejected_key(self) -> None:
        transport = FakeTransport({"openid-connect/token": HttpResponse(401, b"unauthorized")})
        with pytest.raises(TuikAuthError, match="rejected"):
            manager(transport).token()

    def test_token_service_outage(self) -> None:
        transport = FakeTransport({"openid-connect/token": HttpResponse(503, b"down")})
        with pytest.raises(TuikAuthError, match="temporary"):
            manager(transport).token()

    def test_non_json_response(self) -> None:
        transport = FakeTransport({"openid-connect/token": HttpResponse(200, b"<html>")})
        with pytest.raises(TuikParseError, match="not return JSON"):
            manager(transport).token()

    def test_missing_access_token(self) -> None:
        transport = FakeTransport({"openid-connect/token": json_response({"token_type": "Bearer"})})
        with pytest.raises(TuikAuthError, match="access_token"):
            manager(transport).token()

    def test_non_object_payload(self) -> None:
        transport = FakeTransport({"openid-connect/token": HttpResponse(200, b"[1]")})
        with pytest.raises(TuikParseError, match="unexpected payload"):
            manager(transport).token()

    def test_bad_expires_in_falls_back_to_default(self) -> None:
        transport = FakeTransport(
            {"openid-connect/token": json_response({"access_token": "t", "expires_in": "soon"})}
        )
        token_manager = manager(transport)
        token_manager.token()
        assert valid(token_manager)

    def test_error_never_contains_the_key(self) -> None:
        key = "very-secret-key-1234567890"
        transport = FakeTransport({"openid-connect/token": HttpResponse(401, b"no")})
        token_manager = TokenManager(key, transport=transport)
        with pytest.raises(TuikAuthError) as excinfo:
            token_manager.token()
        assert key not in str(excinfo.value)

    def test_has_key_is_trimmed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(API_KEY_ENV, "  ")
        assert not TokenManager(transport=FakeTransport()).has_key


def test_token_payload_is_urlencoded() -> None:
    transport = FakeTransport()
    manager(transport).token()
    body = transport.calls[0].body or b""
    parsed = dict(item.split("=", 1) for item in body.decode().split("&"))
    assert parsed["client_id"] == "veriportali"
    assert json.dumps(parsed)  # body is complete
