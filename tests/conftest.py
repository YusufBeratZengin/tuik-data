"""Shared test helpers: fixture loading and a deterministic fake transport."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tuik_data import TuikClient
from tuik_data._http import HttpResponse
from tuik_data.parser import Dataset, parse_jsonstat, parse_sdmx_json

FIXTURES = Path(__file__).parent / "fixtures"
TOKEN = "fake-token-xxxxxxxxxxxxxxxx"


@dataclass
class Call:
    url: str
    headers: dict[str, str]
    body: bytes | None


class FakeTransport:
    """Routes responses by URL substring, with an optional ordered sequence."""

    def __init__(
        self,
        routes: Mapping[str, HttpResponse | tuple[int, bytes]] | None = None,
        *,
        sequence: Sequence[HttpResponse | tuple[int, bytes]] | None = None,
    ) -> None:
        self.routes = dict(routes or {})
        self.sequence = list(sequence or [])
        self.calls: list[Call] = []

    def __call__(
        self, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float
    ) -> HttpResponse:
        self.calls.append(Call(url, dict(headers), body))
        if "openid-connect/token" in url and "openid-connect/token" not in self.routes:
            return json_response({"access_token": TOKEN, "expires_in": 300})
        if self.sequence:
            return _as_response(self.sequence.pop(0))
        for needle, response in self.routes.items():
            if needle in url:
                return _as_response(response)
        return HttpResponse(404, b'{"error": "no fixture route for this URL"}')

    # -- assertions helpers --------------------------------------------------
    def data_calls(self) -> list[Call]:
        return [call for call in self.calls if "openid-connect/token" not in call.url]

    def urls(self) -> list[str]:
        return [call.url for call in self.calls]


def _as_response(response: HttpResponse | tuple[int, bytes]) -> HttpResponse:
    if isinstance(response, HttpResponse):
        return response
    status, body = response
    return HttpResponse(status, body)


def json_response(document: Any, status: int = 200) -> HttpResponse:
    return HttpResponse(status, json.dumps(document, ensure_ascii=False).encode("utf-8"))


def make_client(transport: FakeTransport | None = None, **kwargs: Any) -> TuikClient:
    """Build a client that never touches the network."""
    options: dict[str, Any] = {
        "api_key": "fake-api-key-xxxxxx",
        "transport": transport or FakeTransport(),
        "cache_ttl": 0,
        "retries": 0,
        "backoff": 0.0,
        "sleep": lambda _seconds: None,
        "base_url": "https://example.invalid/rest",
    }
    options.update(kwargs)
    return TuikClient(**options)


def load_document(name: str) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return document


def load_dataset(name: str) -> Dataset:
    document = load_document(name)
    if "data" in document:
        return parse_sdmx_json(document)
    return parse_jsonstat(document)


@pytest.fixture()
def tufe_document() -> dict[str, Any]:
    return load_document("tufe_example.json")


@pytest.fixture()
def tufe_dataset() -> Dataset:
    return load_dataset("tufe_example.json")


@pytest.fixture()
def sdmx_document() -> dict[str, Any]:
    return load_document("adnks_example_sdmx.json")


@pytest.fixture()
def sdmx_dataset(sdmx_document: dict[str, Any]) -> Dataset:
    return parse_sdmx_json(sdmx_document)


@pytest.fixture()
def transport() -> FakeTransport:
    return FakeTransport()
