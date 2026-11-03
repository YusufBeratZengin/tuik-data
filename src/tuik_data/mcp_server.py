"""Model Context Protocol server (stdio, newline-delimited JSON-RPC 2.0).

Implemented with the standard library only - no ``mcp`` package, no asyncio
required.  Point an MCP client at::

    {"command": "tuik-data", "args": ["mcp"], "env": {"TUIK_API_KEY": "xxxxxx"}}

Tools exposed: ``tuik_get_indicator``, ``tuik_list_indicators``, ``tuik_search``
and ``tuik_describe_indicator``.  Responses are capped so a chat context is never
flooded by a 100k-row cube.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Mapping
from typing import Any, TextIO

from . import __version__
from .catalogues import CATALOGUE, Indicator
from .client import TuikClient
from .errors import TuikError

__all__ = ["PROTOCOL_VERSION", "handle_request", "serve", "server_tools"]

PROTOCOL_VERSION = "2024-11-05"
MAX_ROWS = 500

_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "tuik_get_indicator",
        "description": (
            "Fetch published observations for a TÜİK indicator. Returns rows with "
            "dimension codes, value and confidentiality status. Values are returned "
            "exactly as published; no rate is calculated."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Catalogue code, e.g. tufe_genel, yi_ufe, nufus_il",
                },
                "start_year": {"type": "integer", "description": "First year (inclusive)"},
                "end_year": {"type": "integer", "description": "Last year (inclusive)"},
                "last_n": {
                    "type": "integer",
                    "description": "Keep only the newest N periods",
                },
                "filters": {
                    "type": "object",
                    "description": 'Dimension filters, e.g. {"CITIES": "TR34"}',
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["code"],
        },
    },
    {
        "name": "tuik_list_indicators",
        "description": "List every indicator in the local catalogue (no network call).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "tuik_search",
        "description": "Search the catalogue by Turkish or English keyword (no network).",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "tuik_describe_indicator",
        "description": "Show the dimensions and category codes of a dataflow before filtering.",
        "inputSchema": {
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
    },
)


def server_tools() -> list[dict[str, Any]]:
    """Return the tool definitions advertised via ``tools/list``."""
    return [dict(tool) for tool in _TOOLS]


def _indicator_payload(indicator: Indicator) -> dict[str, Any]:
    return {
        "code": indicator.code,
        "title_tr": indicator.title_tr,
        "title_en": indicator.title_en,
        "dataflow": indicator.dataflow,
        "theme": indicator.theme,
        "frequency": indicator.frequency,
        "notes_tr": indicator.notes_tr,
    }


def _text_result(payload: Any, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            }
        ],
        "isError": is_error,
    }


def _call_tool(name: str, arguments: Mapping[str, Any], client: TuikClient) -> dict[str, Any]:
    if name == "tuik_list_indicators":
        return _text_result({"indicators": [_indicator_payload(item) for item in CATALOGUE]})
    if name == "tuik_search":
        query = str(arguments.get("query", "")).strip()
        matches = client.search(query)
        return _text_result(
            {"query": query, "matches": [_indicator_payload(item) for item in matches]}
        )
    if name == "tuik_describe_indicator":
        code = str(arguments.get("code", "")).strip()
        try:
            from .catalogues import get_indicator

            flow = get_indicator(code).resolve_dataflow()
        except TuikError:
            flow = code
        dataset_dims = client.dimensions(flow)
        return _text_result(
            {
                "dataflow": flow,
                "dimensions": [
                    {
                        "id": dimension.id,
                        "label": dimension.label,
                        "codes": [
                            {"code": category.code, "label": category.label}
                            for category in dimension.categories[:200]
                        ],
                        "code_count": len(dimension.categories),
                    }
                    for dimension in dataset_dims
                ],
            }
        )
    if name == "tuik_get_indicator":
        code = str(arguments.get("code", "")).strip()
        if not code:
            return _text_result({"error": "code is required"}, is_error=True)
        filters = arguments.get("filters")
        if filters is None:
            filters = {}
        if not isinstance(filters, Mapping):
            return _text_result({"error": "filters must be an object"}, is_error=True)
        dataset = client.get_indicator(
            code,
            start_year=_optional_int(arguments.get("start_year")),
            end_year=_optional_int(arguments.get("end_year")),
            filters={str(key): str(value) for key, value in filters.items()} or None,
            last_n=_optional_int(arguments.get("last_n")),
        )
        rows = dataset.records(labels="both")
        truncated = len(rows) > MAX_ROWS
        return _text_result(
            {
                "code": code,
                "label": dataset.label,
                "source": dataset.source,
                "updated": dataset.updated,
                "dimensions": list(dataset.dimension_ids),
                "total_rows": len(rows),
                "truncated": truncated,
                "rows": rows[:MAX_ROWS],
                "notice": (
                    "Values are published observations, not computed rates. "
                    "Official bulletins remain authoritative."
                ),
            }
        )
    return _text_result({"error": f"unknown tool {name!r}"}, is_error=True)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def handle_request(
    request: Mapping[str, Any], client: TuikClient | None = None
) -> dict[str, Any] | None:
    """Handle one JSON-RPC message; returns the response or ``None`` for notifications."""
    method = request.get("method")
    request_id = request.get("id")
    is_notification = "id" not in request
    active = client or TuikClient()

    if not isinstance(method, str):
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32600, "message": "invalid request: missing method"},
        }
    if is_notification:
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "tuik-data", "version": __version__},
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": server_tools()}}
    if method == "tools/call":
        params = request.get("params")
        if params is None:
            params = {}
        if not isinstance(params, Mapping):
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32602, "message": "params must be an object"},
            }
        name = str(params.get("name", ""))
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, Mapping):
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32602, "message": "arguments must be an object"},
            }
        try:
            result = _call_tool(name, arguments, active)
        except TuikError as exc:
            result = _text_result({"error": str(exc)}, is_error=True)
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def serve(
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    *,
    client: TuikClient | None = None,
    lines: Iterable[str] | None = None,
) -> int:
    """Run the stdio loop until EOF. Returns 0 on clean shutdown."""
    source = lines if lines is not None else (stdin or sys.stdin)
    sink = stdout or sys.stdout
    active = client or TuikClient()
    for line in source:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            message = json.loads(stripped)
        except json.JSONDecodeError:
            sink.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "parse error"},
                    }
                )
                + "\n"
            )
            sink.flush()
            continue
        if not isinstance(message, Mapping):
            continue
        response = handle_request(message, active)
        if response is not None:
            sink.write(json.dumps(response, ensure_ascii=False) + "\n")
            sink.flush()
    return 0


def main() -> int:  # pragma: no cover - thin wrapper
    return serve()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
