"""Client tests: URL building, retries, auth, caching and dimension guidance.

Every test uses :class:`tests.conftest.FakeTransport`; the suite never opens a
socket.  The live-service smoke test lives in ``test_network.py`` and is skipped
unless ``TUIK_LIVE=1``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import (
    FakeTransport,
    json_response,
    load_document,
    make_client,
)
from tuik_data import TuikClient
from tuik_data._http import HttpResponse
from tuik_data.errors import (
    TuikAuthError,
    TuikConfigError,
    TuikDimensionError,
    TuikEmptyResult,
    TuikHTTPError,
    TuikNotFoundError,
    TuikParseError,
    TuikRateLimitError,
)
from tuik_data.parser import Dataset

TUFE_FLOW = "TR,DF_TUFE_SDMX_TT01,1.0"


def tufe_response() -> HttpResponse:
    return json_response(load_document("tufe_example.json"))


def structure_response() -> HttpResponse:
    document = load_document("adnks_example_sdmx.json")
    document["data"]["dataSets"] = []
    document["data"]["structure"]["dimensions"]["series"] = [
        {
            "id": "CITIES",
            "name": "İl",
            "values": [
                {"id": "TR34", "name": "İstanbul"},
                {"id": "TR06", "name": "Ankara"},
            ],
        }
    ]
    return json_response(document)


class TestRequestBuilding:
    def test_jsonstat_request_parameters(self, transport: FakeTransport) -> None:
        transport.routes["format=JSONSTAT"] = tufe_response()
        client = make_client(transport)
        dataset = client.get_dataset(TUFE_FLOW, start_year=2024, end_year=2024)
        assert isinstance(dataset, Dataset)
        (call,) = transport.data_calls()
        assert call.url.startswith("https://example.invalid/rest/data/TR,DF_TUFE_SDMX_TT01,1.0?")
        assert "format=JSONSTAT" in call.url
        assert "startPeriod=2024" in call.url
        assert "endPeriod=2024" in call.url
        assert call.headers["Authorization"] == "Bearer fake-token-xxxxxxxxxxxxxxxx"

    def test_sdmx_json_request(self, transport: FakeTransport) -> None:
        transport.routes["format=JSON"] = json_response(load_document("adnks_example_sdmx.json"))
        client = make_client(transport)
        dataset = client.get_dataset(TUFE_FLOW, fmt="sdmx-json")
        assert dataset.dimension_ids == ("CITIES", "SEX", "TIME_PERIOD")
        (call,) = transport.data_calls()
        assert "application/vnd.sdmx.data+json" in call.headers["Accept"]
        assert "format=JSON" in call.url

    def test_unknown_format(self) -> None:
        with pytest.raises(TuikConfigError, match="unknown format"):
            make_client().get_dataset(TUFE_FLOW, fmt="xml")  # type: ignore[arg-type]

    def test_dataflow_short_forms(self, transport: FakeTransport) -> None:
        transport.routes["data/"] = tufe_response()
        client = make_client(transport)
        client.get_dataset("DF_X")
        client.get_dataset("TR,DF_X")
        client.get_dataset("TR,DF_X,1.2")
        urls = transport.data_calls()
        assert "/data/TR,DF_X,1.0?" in urls[0].url
        assert "/data/TR,DF_X,1.0?" in urls[1].url
        assert "/data/TR,DF_X,1.2?" in urls[2].url

    @pytest.mark.parametrize("bad", ["", "a,b,c,d"])
    def test_bad_dataflow_id(self, bad: str) -> None:
        with pytest.raises(TuikConfigError):
            make_client().get_dataset(bad)

    def test_period_bounds_validation(self) -> None:
        with pytest.raises(TuikConfigError, match="before start_year"):
            make_client().get_dataset(TUFE_FLOW, start_year=2025, end_year=2020)
        with pytest.raises(TuikConfigError, match="four-digit"):
            make_client().get_dataset(TUFE_FLOW, start_year=24)

    def test_base_url_from_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TUIK_BASE_URL", "https://staging.invalid/sdmx")
        client = TuikClient(api_key="fake", transport=FakeTransport())
        assert client.base_url == "https://staging.invalid/sdmx"


class TestAuth:
    def test_missing_key_is_explained(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TUIK_API_KEY", raising=False)
        client = TuikClient(transport=FakeTransport(), cache_ttl=0)
        with pytest.raises(TuikConfigError, match="TUIK_API_KEY"):
            client.get_dataset(TUFE_FLOW)

    def test_token_is_cached_between_calls(self, transport: FakeTransport) -> None:
        transport.routes["format=JSONSTAT"] = tufe_response()
        client = make_client(transport)
        client.get_dataset(TUFE_FLOW)
        client.get_dataset(TUFE_FLOW)
        token_calls = [url for url in transport.urls() if "openid-connect" in url]
        assert len(token_calls) == 1

    def test_401_retries_once_with_a_new_token(self, transport: FakeTransport) -> None:
        transport.sequence = [HttpResponse(401, b"{}"), tufe_response()]
        client = make_client(transport, retries=0)
        dataset = client.get_dataset(TUFE_FLOW)
        assert len(dataset) == 9
        token_calls = [url for url in transport.urls() if "openid-connect" in url]
        assert len(token_calls) == 2
        assert client._auth.refresh_count == 2

    def test_persistent_401_is_an_auth_error(self, transport: FakeTransport) -> None:
        transport.sequence = [HttpResponse(401, b"{}"), HttpResponse(401, b"{}")]
        client = make_client(transport, retries=0)
        with pytest.raises(TuikAuthError, match="HTTP 401"):
            client.get_dataset(TUFE_FLOW)

    def test_403_is_an_auth_error(self, transport: FakeTransport) -> None:
        transport.sequence = [HttpResponse(403, b"forbidden")]
        with pytest.raises(TuikAuthError, match="403"):
            make_client(transport).get_dataset(TUFE_FLOW)

    def test_error_message_does_not_leak_the_key(self, transport: FakeTransport) -> None:
        secret = "example/fake/api-key-0000"
        transport.sequence = [HttpResponse(403, secret.encode())]
        client = make_client(transport, api_key=secret)
        with pytest.raises(TuikAuthError) as excinfo:
            client.get_dataset(TUFE_FLOW)
        assert secret not in str(excinfo.value)


class TestFailures:
    def test_404(self, transport: FakeTransport) -> None:
        with pytest.raises(TuikNotFoundError, match="404"):
            make_client(transport).get_dataset("DF_MISSING")

    def test_500_then_success(self, transport: FakeTransport) -> None:
        transport.sequence = [HttpResponse(500, b"boom"), tufe_response()]
        client = make_client(transport, retries=1)
        assert len(client.get_dataset(TUFE_FLOW)) == 9

    def test_retries_are_bounded(self, transport: FakeTransport) -> None:
        sleeps: list[float] = []
        transport.sequence = [HttpResponse(503, b"down"), HttpResponse(503, b"down")]
        client = make_client(transport, retries=1, backoff=0.25, sleep=sleeps.append)
        with pytest.raises(TuikHTTPError, match="503"):
            client.get_dataset(TUFE_FLOW)
        assert sleeps == [0.25]
        assert len(transport.data_calls()) == 2

    def test_429(self, transport: FakeTransport) -> None:
        transport.sequence = [HttpResponse(429, b"slow down")]
        with pytest.raises(TuikRateLimitError, match="429"):
            make_client(transport).get_dataset(TUFE_FLOW)

    def test_non_json_body(self, transport: FakeTransport) -> None:
        transport.sequence = [HttpResponse(200, b"<html>gateway</html>")]
        with pytest.raises(TuikParseError, match="not JSON"):
            make_client(transport).get_dataset(TUFE_FLOW)

    def test_json_array_body(self, transport: FakeTransport) -> None:
        transport.sequence = [HttpResponse(200, b"[1,2]")]
        with pytest.raises(TuikParseError, match="expected a JSON object"):
            make_client(transport).get_dataset(TUFE_FLOW)

    def test_empty_response_without_filters(self, transport: FakeTransport) -> None:
        document = load_document("tufe_example.json")
        document["value"] = []
        document["size"] = [3, 3]
        transport.routes["format=JSONSTAT"] = json_response(document)
        with pytest.raises(TuikEmptyResult, match="no observations"):
            make_client(transport).get_dataset(TUFE_FLOW, start_year=2030)


class TestDimensions:
    def test_build_key_uses_dataflow_order(self, transport: FakeTransport) -> None:
        transport.routes["detail=nodata"] = structure_response()
        transport.routes["format=JSONSTAT"] = tufe_response()
        client = make_client(transport)
        dataset = client.get_dataset(TUFE_FLOW, filters={"CITIES": "TR06"})
        assert len(dataset) == 9
        urls = [call.url for call in transport.data_calls()]
        assert any("detail=nodata" in url for url in urls)
        assert any("/data/TR,DF_TUFE_SDMX_TT01,1.0/TR06?" in url for url in urls)

    def test_dimensions_are_cached(self, transport: FakeTransport) -> None:
        transport.routes["detail=nodata"] = structure_response()
        client = make_client(transport)
        first = client.dimensions(TUFE_FLOW)
        second = client.dimensions(TUFE_FLOW)
        assert first is second
        assert len([url for url in transport.urls() if "detail=nodata" in url]) == 1

    def test_unknown_dimension(self, transport: FakeTransport) -> None:
        transport.routes["detail=nodata"] = structure_response()
        with pytest.raises(TuikDimensionError) as excinfo:
            make_client(transport).build_key(TUFE_FLOW, {"NOPE": "1"})
        assert "unknown dimension" in str(excinfo.value)
        assert "CITIES" in excinfo.value.available

    def test_unknown_category_lists_valid_codes(self, transport: FakeTransport) -> None:
        transport.routes["detail=nodata"] = structure_response()
        with pytest.raises(TuikDimensionError, match="TR34"):
            make_client(transport).build_key(TUFE_FLOW, {"CITIES": "TR99"})

    def test_key_accepts_labels(self, transport: FakeTransport) -> None:
        transport.routes["detail=nodata"] = structure_response()
        key = make_client(transport).build_key(TUFE_FLOW, {"CITIES": "ankara"})
        assert key == "TR06"

    def test_key_pads_missing_dimensions(self, transport: FakeTransport) -> None:
        transport.routes["detail=nodata"] = structure_response()
        transport.routes["format=JSONSTAT"] = tufe_response()
        client = make_client(transport)
        dataset = client.get_dataset(TUFE_FLOW, key={"CITIES": "TR34"})
        assert len(dataset) == 9

    def test_empty_filtered_result_lists_categories(self, transport: FakeTransport) -> None:
        empty = load_document("tufe_example.json")
        empty["value"] = [None] * 9
        transport.routes["detail=nodata"] = structure_response()
        transport.routes["format=JSONSTAT"] = json_response(empty)
        client = make_client(transport)
        with pytest.raises(TuikDimensionError) as excinfo:
            client.get_dataset(TUFE_FLOW, filters={"CITIES": "TR34"})
        message = str(excinfo.value)
        assert "matched no observations" in message
        assert "Available categories" in message
        assert "İstanbul" in message
        assert excinfo.value.available["CITIES"] == ["TR34", "TR06"]

    def test_empty_filtered_result_when_metadata_unavailable(
        self, transport: FakeTransport
    ) -> None:
        empty = load_document("tufe_example.json")
        empty["value"] = [None] * 9
        transport.routes["format=JSONSTAT"] = json_response(empty)
        transport.routes["detail=nodata"] = HttpResponse(500, b"nope")
        client = make_client(transport)
        with pytest.raises(TuikDimensionError, match="tuik-data dims"):
            client.get_dataset(TUFE_FLOW, key="TR34")


class TestIndicators:
    def test_get_indicator(self, transport: FakeTransport) -> None:
        transport.routes["DF_TUFE_SDMX_TT01"] = tufe_response()
        dataset = make_client(transport).get_indicator("tufe_genel", start_year=2024)
        assert len(dataset) == 9
        assert "DF_TUFE_SDMX_TT01" in transport.data_calls()[0].url

    def test_unknown_indicator_suggests_codes(self) -> None:
        with pytest.raises(TuikConfigError, match="Did you mean"):
            make_client().get_indicator("tufe_genel_x")

    def test_env_override_changes_dataflow(
        self, transport: FakeTransport, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TUIK_DATASET_TUFE_GENEL", "TR,DF_FAKE,9.9")
        transport.routes["DF_FAKE"] = tufe_response()
        make_client(transport).get_indicator("tufe_genel")
        assert "/data/TR,DF_FAKE,9.9" in transport.data_calls()[0].url

    def test_last_n_keeps_newest_periods(self, transport: FakeTransport) -> None:
        transport.routes["DF_TUFE_SDMX_TT01"] = tufe_response()
        dataset = make_client(transport).get_indicator("tufe_genel", last_n=2)
        assert dataset.size == (2, 3)
        assert dataset.dimensions[0].codes == ("2024-02", "2024-03")

    def test_last_n_rejects_zero(self) -> None:
        with pytest.raises(TuikConfigError, match="positive"):
            make_client().get_indicator("tufe_genel", last_n=0)

    def test_last_n_without_time_dimension(self, transport: FakeTransport) -> None:
        document = {
            "version": "2.0",
            "class": "dataset",
            "id": ["CITIES"],
            "size": [2],
            "dimension": {
                "CITIES": {
                    "label": "İl",
                    "category": {"index": {"TR34": 0, "TR06": 1}},
                }
            },
            "value": [1, 2],
        }
        transport.routes["format=JSONSTAT"] = json_response(document)
        with pytest.raises(TuikConfigError, match="no time dimension"):
            make_client(transport).get_dataset(TUFE_FLOW, last_n=1)

    def test_get_tufe_annual(self, transport: FakeTransport) -> None:
        transport.routes["DF_TUFE_SDMX_TT01"] = tufe_response()
        dataset = make_client(transport).get_tufe_annual(2024)
        assert dataset.label.startswith("TÜFE")
        assert "startPeriod=2024" in transport.data_calls()[0].url

    def test_search(self) -> None:
        results = make_client().search("enflasyon")
        assert results[0].code == "tufe_genel"


class TestCaching:
    def test_second_call_hits_the_cache(self, transport: FakeTransport, tmp_path: Path) -> None:
        transport.routes["format=JSONSTAT"] = tufe_response()
        client = make_client(transport, cache_dir=tmp_path, cache_ttl=60)
        first = client.get_dataset(TUFE_FLOW)
        second = client.get_dataset(TUFE_FLOW)
        assert first.values == second.values
        assert len(transport.data_calls()) == 1
        assert list(tmp_path.glob("*.json"))

    def test_expired_cache_is_refetched(self, transport: FakeTransport, tmp_path: Path) -> None:
        transport.routes["format=JSONSTAT"] = tufe_response()
        client = make_client(transport, cache_dir=tmp_path, cache_ttl=0.0001)
        client.get_dataset(TUFE_FLOW)
        import time

        time.sleep(0.001)
        client.get_dataset(TUFE_FLOW)
        assert len(transport.data_calls()) == 2

    def test_corrupt_cache_is_ignored(self, transport: FakeTransport, tmp_path: Path) -> None:
        transport.routes["format=JSONSTAT"] = tufe_response()
        client = make_client(transport, cache_dir=tmp_path, cache_ttl=60)
        client.get_dataset(TUFE_FLOW)
        cache_file = next(tmp_path.glob("*.json"))
        cache_file.write_text("{not json", encoding="utf-8")
        client.get_dataset(TUFE_FLOW)
        assert len(transport.data_calls()) == 2

    def test_cache_can_be_disabled(self, transport: FakeTransport, tmp_path: Path) -> None:
        transport.routes["format=JSONSTAT"] = tufe_response()
        client = make_client(transport, cache_dir=tmp_path, cache_ttl=0)
        client.get_dataset(TUFE_FLOW)
        client.get_dataset(TUFE_FLOW)
        assert len(transport.data_calls()) == 2
        assert not list(tmp_path.glob("*.json"))

    def test_use_cache_flag_is_forwarded(self, transport: FakeTransport, tmp_path: Path) -> None:
        transport.routes["format=JSONSTAT"] = tufe_response()
        client = make_client(transport, cache_dir=tmp_path, cache_ttl=3600)
        client.get_dataset(TUFE_FLOW)
        client.get_dataset(TUFE_FLOW, use_cache=False)
        assert len(transport.data_calls()) == 2


class TestOffline:
    def test_load_fixture_file(self) -> None:
        dataset = TuikClient.load(Path(__file__).parent / "fixtures" / "tufe_example.json")
        assert dataset.size == (3, 3)

    def test_flatten_detects_jsonstat(self, tufe_document: dict[str, Any]) -> None:
        assert TuikClient.flatten(tufe_document).dimension_ids == ("TIME_PERIOD", "COICOP")

    def test_flatten_detects_sdmx(self, sdmx_document: dict[str, Any]) -> None:
        assert "CITIES" in TuikClient.flatten(sdmx_document).dimension_ids

    def test_flatten_accepts_bytes_and_text(self, tufe_document: dict[str, Any]) -> None:
        text = json.dumps(tufe_document)
        assert len(TuikClient.flatten(text)) == 9
        assert len(TuikClient.flatten(text.encode("utf-8"))) == 9

    def test_flatten_rejects_bad_payloads(self) -> None:
        with pytest.raises(TuikParseError, match="not JSON"):
            TuikClient.flatten("{oops")
        with pytest.raises(TuikParseError, match="JSON object"):
            TuikClient.flatten("[1, 2]")
        with pytest.raises(TuikParseError, match="UTF-8"):
            TuikClient.flatten(b"\xff\xfe")

    def test_context_manager(self, transport: FakeTransport) -> None:
        with make_client(transport) as client:
            assert isinstance(client, TuikClient)
        assert client._dimension_cache == {}
