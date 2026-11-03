"""MCP server tests: JSON-RPC framing, tool dispatch and error mapping."""

from __future__ import annotations

import io
import json
from typing import Any

from tests.conftest import FakeTransport, json_response, load_document, make_client
from tuik_data._http import HttpResponse
from tuik_data.mcp_server import PROTOCOL_VERSION, handle_request, serve, server_tools


def client_with_fixture() -> Any:
    transport = FakeTransport()
    transport.routes["DF_TUFE_SDMX_TT01"] = json_response(load_document("tufe_example.json"))
    transport.routes["detail=nodata"] = json_response(load_document("adnks_example_sdmx.json"))
    return make_client(transport)


def rpc(method: str, params: dict[str, Any] | None = None, request_id: int = 1) -> dict[str, Any]:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        payload["params"] = params
    return payload


class TestHandshake:
    def test_initialize(self) -> None:
        response = handle_request(rpc("initialize"), client_with_fixture())
        assert response is not None
        result = response["result"]
        assert result["protocolVersion"] == PROTOCOL_VERSION
        assert result["serverInfo"]["name"] == "tuik-data"
        assert result["capabilities"]["tools"]["listChanged"] is False

    def test_notification_has_no_response(self) -> None:
        assert handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None

    def test_ping(self) -> None:
        response = handle_request(rpc("ping"), client_with_fixture())
        assert response is not None
        assert response["result"] == {}

    def test_unknown_method(self) -> None:
        response = handle_request(rpc("nope"), client_with_fixture())
        assert response is not None
        assert response["error"]["code"] == -32601

    def test_missing_method(self) -> None:
        response = handle_request({"jsonrpc": "2.0", "id": 5}, client_with_fixture())
        assert response is not None
        assert response["error"]["code"] == -32600


class TestTools:
    def test_tool_list(self) -> None:
        names = {tool["name"] for tool in server_tools()}
        assert names == {
            "tuik_get_indicator",
            "tuik_list_indicators",
            "tuik_search",
            "tuik_describe_indicator",
        }
        for tool in server_tools():
            assert tool["description"]
            assert tool["inputSchema"]["type"] == "object"

    def test_tools_are_copies(self) -> None:
        first = server_tools()
        first[0]["name"] = "mutated"
        assert server_tools()[0]["name"] == "tuik_get_indicator"

    def test_list_indicators(self) -> None:
        response = handle_request(
            rpc("tools/call", {"name": "tuik_list_indicators", "arguments": {}}),
            client_with_fixture(),
        )
        assert response is not None
        payload = json.loads(response["result"]["content"][0]["text"])
        assert any(item["code"] == "tufe_genel" for item in payload["indicators"])
        assert response["result"]["isError"] is False

    def test_search(self) -> None:
        response = handle_request(
            rpc("tools/call", {"name": "tuik_search", "arguments": {"query": "enflasyon"}}),
            client_with_fixture(),
        )
        assert response is not None
        payload = json.loads(response["result"]["content"][0]["text"])
        assert payload["matches"][0]["code"] == "tufe_genel"

    def test_get_indicator(self) -> None:
        response = handle_request(
            rpc(
                "tools/call",
                {"name": "tuik_get_indicator", "arguments": {"code": "tufe_genel"}},
            ),
            client_with_fixture(),
        )
        assert response is not None
        payload = json.loads(response["result"]["content"][0]["text"])
        assert payload["total_rows"] == 9
        assert payload["rows"][0]["value"] == 1000.0
        assert "not computed rates" in payload["notice"]

    def test_get_indicator_without_code(self) -> None:
        response = handle_request(
            rpc("tools/call", {"name": "tuik_get_indicator", "arguments": {}}),
            client_with_fixture(),
        )
        assert response is not None
        assert response["result"]["isError"] is True

    def test_describe_indicator(self) -> None:
        response = handle_request(
            rpc(
                "tools/call",
                {"name": "tuik_describe_indicator", "arguments": {"code": "nufus_il"}},
            ),
            client_with_fixture(),
        )
        assert response is not None
        payload = json.loads(response["result"]["content"][0]["text"])
        assert payload["dataflow"].startswith("TR,DF_ADNKS_T30")
        assert payload["dimensions"][0]["id"] == "CITIES"

    def test_describe_raw_dataflow(self) -> None:
        response = handle_request(
            rpc(
                "tools/call",
                {"name": "tuik_describe_indicator", "arguments": {"code": "TR,DF_X,1.0"}},
            ),
            client_with_fixture(),
        )
        assert response is not None
        assert json.loads(response["result"]["content"][0]["text"])["dataflow"] == "TR,DF_X,1.0"

    def test_unknown_tool(self) -> None:
        response = handle_request(
            rpc("tools/call", {"name": "nope", "arguments": {}}), client_with_fixture()
        )
        assert response is not None
        assert response["result"]["isError"] is True

    def test_tool_error_becomes_is_error(self) -> None:
        transport = FakeTransport()
        transport.routes["DF_TUFE_SDMX_TT01"] = json_response(load_document("tufe_example.json"))
        view = make_client(transport, api_key=None)
        response = handle_request(
            rpc("tools/call", {"name": "tuik_get_indicator", "arguments": {"code": "tufe_genel"}}),
            view,
        )
        assert response is not None
        assert response["result"]["isError"] is True
        assert "TUIK_API_KEY" in response["result"]["content"][0]["text"]

    def test_bad_arguments_type(self) -> None:
        response = handle_request(
            rpc("tools/call", {"name": "tuik_search", "arguments": []}), client_with_fixture()
        )
        assert response is not None
        assert response["error"]["code"] == -32602

    def test_bad_params_type(self) -> None:
        response = handle_request(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": "nope"},
            client_with_fixture(),
        )
        assert response is not None
        assert response["error"]["code"] == -32602


class TestServeLoop:
    def test_framing_and_flush(self) -> None:
        sink = io.StringIO()
        lines = [
            json.dumps(rpc("initialize")),
            "",
            "not json",
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps(rpc("ping", request_id=2)),
        ]
        assert serve(lines=lines, stdout=sink, client=client_with_fixture()) == 0
        responses = [json.loads(line) for line in sink.getvalue().splitlines()]
        assert len(responses) == 3
        assert responses[0]["result"]["serverInfo"]["name"] == "tuik-data"
        assert responses[1]["error"]["code"] == -32700
        assert responses[2]["id"] == 2

    def test_non_object_message_is_ignored(self) -> None:
        sink = io.StringIO()
        assert serve(lines=["[1, 2]"], stdout=sink, client=client_with_fixture()) == 0
        assert sink.getvalue() == ""

    def test_auth_error_is_reported_inside_the_result(self) -> None:
        transport = FakeTransport({"openid-connect/token": HttpResponse(401, b"no")})
        view = make_client(transport)
        sink = io.StringIO()
        serve(
            lines=[
                json.dumps(rpc("tools/call", {"name": "tuik_search", "arguments": {"query": "x"}}))
            ],
            stdout=sink,
            client=view,
        )
        # search is offline: it must succeed even with a broken token service.
        assert json.loads(sink.getvalue())["result"]["isError"] is False


def test_serve_reads_a_string_stream() -> None:
    sink = io.StringIO()
    source = io.StringIO(json.dumps(rpc("ping")) + "\n")
    assert serve(source, sink, client=client_with_fixture()) == 0
    assert json.loads(sink.getvalue())["result"] == {}
