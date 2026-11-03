"""Transport tests against a throwaway localhost server (no internet access)."""

from __future__ import annotations

import gzip
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.conftest import load_document
from tuik_data._http import HttpResponse, build_url, redact, urllib_transport
from tuik_data.auth import TokenManager
from tuik_data.client import TuikClient
from tuik_data.errors import TuikNetworkError


class Handler(BaseHTTPRequestHandler):
    """Tiny router: /ok, /gzip, /missing, /html, /slow."""

    server_version = "fixture-server"

    def do_GET(self) -> None:
        if "DF_TUFE" in self.path or self.path.startswith("/ok"):
            payload = json.dumps(load_document("tufe_example.json"), ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif "DF_HTML" in self.path or self.path.startswith("/html"):
            payload = b"<html>gateway</html>"
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif self.path.startswith("/gzip"):
            payload = gzip.compress(b'{"class": "dataset"}')
            self.send_response(200)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            payload = b'{"error": "missing"}'
            self.send_response(404)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    def log_message(self, *_args: object) -> None:
        return


@pytest.fixture(scope="module")
def server() -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = str(httpd.server_address[0]), int(httpd.server_address[1])
    try:
        yield f"http://{host}:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


class TestUrllibTransport:
    def test_ok(self, server: str) -> None:
        response = urllib_transport(f"{server}/ok", {}, None, 5.0)
        assert response.status == 200
        assert json.loads(response.text())["class"] == "dataset"

    def test_gzip_is_decoded(self, server: str) -> None:
        response = urllib_transport(f"{server}/gzip", {}, None, 5.0)
        assert response.body == b'{"class": "dataset"}'

    def test_error_body_is_returned_not_raised(self, server: str) -> None:
        response = urllib_transport(f"{server}/missing", {}, None, 5.0)
        assert response.status == 404
        assert b"missing" in response.body

    def test_unreachable_host(self) -> None:
        with pytest.raises(TuikNetworkError, match="could not reach"):
            urllib_transport("http://127.0.0.1:1/nope", {}, None, 0.5)


class TestEndToEnd:
    def test_client_over_real_http(self, server: str) -> None:
        auth = TokenManager("fake-api-key-xxxxxx", transport=_fixed_token)
        client = TuikClient(
            base_url=f"{server}",
            auth=auth,
            cache_ttl=0,
            timeout=5.0,
        )
        dataset = client.get_dataset("TR,DF_TUFE_SDMX_TT01,1.0")
        assert dataset.size == (3, 3)
        assert dataset.dimensions[1].categories[0].label == "Gıda ve Alkolsüz İçecekler"

    def test_client_reports_non_json(self, server: str) -> None:
        auth = TokenManager("fake-api-key-xxxxxx", transport=_fixed_token)
        client = TuikClient(base_url=f"{server}", auth=auth, cache_ttl=0, timeout=5.0)
        from tuik_data.errors import TuikParseError

        with pytest.raises(TuikParseError, match="not JSON"):
            client.get_dataset("TR,DF_HTML,1.0")


def _fixed_token(url: str, headers: object, body: object, timeout: float) -> HttpResponse:
    if "openid-connect/token" in url:
        return HttpResponse(200, b'{"access_token": "local-token", "expires_in": 300}')
    return HttpResponse(404, b"unexpected")


def test_build_url_skips_empty_parameters() -> None:
    assert build_url("https://x.test/rest", "data/TR,DF_X,1.0", {"a": 1, "b": None, "c": ""}) == (
        "https://x.test/rest/data/TR,DF_X,1.0?a=1"
    )
    assert build_url("https://x.test", "/data", {}) == "https://x.test/data"


def test_redact_hides_credentials() -> None:
    token = "abc123def456"
    assert token not in redact(f"Authorization: Bearer {token}")
    assert "***" in redact("api_key=deadbeef")
    assert "x" * 40 not in redact("x" * 40)
    assert redact("plain words") == "plain words"
