"""Command line interface: ``tuik-data``.

Commands
--------
``fetch``       download an indicator and print table / JSON / CSV
``indicators``  list the curated catalogue
``search``      search the catalogue offline
``dims``        show a dataflow's dimensions and codes
``flatten``     turn a downloaded JSON-stat / SDMX-JSON file into a table
``save``        store a series in the local SQLite store
``mcp``         run the MCP stdio server

Exit codes: ``0`` success, ``2`` a TÜİK/parse/config error, ``130`` interrupted.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, cast

from . import __version__
from .catalogues import CATALOGUE, Indicator
from .client import TuikClient
from .errors import TuikError
from .parser import Dataset
from .storage import SeriesStore

__all__ = ["build_parser", "main", "render_table"]

LabelMode = Literal["code", "label", "both"]

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_TUIK = 2


def _utf8_stdout() -> None:
    """Force UTF-8 on stdout so Turkish labels survive a cp1254/cp437 console."""
    stream = sys.stdout
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        with contextlib.suppress(OSError, ValueError):  # pragma: no cover - exotic stream
            reconfigure(encoding="utf-8", errors="replace")


def render_table(
    rows: Sequence[dict[str, Any]], columns: Sequence[str], *, max_width: int = 48
) -> str:
    """Render rows as a plain-text table with UTF-8 aware padding."""
    if not rows:
        return "(no rows)"
    cells: list[list[str]] = []
    for row in rows:
        line = []
        for column in columns:
            value = row.get(column, "")
            text = "" if value is None else str(value)
            if len(text) > max_width:
                text = text[: max_width - 1] + "…"
            line.append(text)
        cells.append(line)
    widths = [
        max(len(column), *(len(line[index]) for line in cells))
        for index, column in enumerate(columns)
    ]
    header = "  ".join(column.ljust(widths[index]) for index, column in enumerate(columns))
    rule = "  ".join("-" * width for width in widths)
    body = [
        "  ".join(cell.ljust(widths[index]) for index, cell in enumerate(line)) for line in cells
    ]
    return "\n".join([header, rule, *body])


def _emit(dataset: Dataset, args: argparse.Namespace) -> None:
    fmt = args.format
    labels = cast(LabelMode, getattr(args, "labels", "code"))
    if fmt == "csv":
        text = dataset.to_csv(labels=labels)
    elif fmt == "json":
        rows = dataset.records(labels=labels, include_status=True)
        text = json.dumps(
            {
                "label": dataset.label,
                "source": dataset.source,
                "updated": dataset.updated,
                "dimensions": list(dataset.dimension_ids),
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
    else:
        rows = dataset.records(labels=labels, include_status=True)
        columns = [*dataset.dimension_ids, "value", "status"]
        text = render_table(rows, columns)
    out = getattr(args, "out", None)
    if out:
        Path(out).write_text(text + ("\n" if not text.endswith("\n") else ""), encoding="utf-8")
        print(f"wrote {out} ({len(dataset)} rows)")
    else:
        print(text)


def _indicator_line(indicator: Indicator) -> dict[str, str]:
    return {
        "code": indicator.code,
        "title": indicator.title_tr,
        "dataflow": indicator.dataflow,
        "frequency": indicator.frequency,
        "theme": indicator.theme,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tuik-data",
        description="Zero-dependency client for TÜİK open statistics (SDMX / JSON-stat).",
    )
    parser.add_argument("--version", action="version", version=f"tuik-data {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="command")

    fetch = sub.add_parser("fetch", help="fetch an indicator")
    fetch.add_argument("indicator", help="catalogue code, e.g. tufe_genel")
    fetch.add_argument("--format", choices=("table", "json", "csv"), default="table")
    fetch.add_argument("--out", help="write to this file instead of stdout")
    fetch.add_argument("--start-year", type=int, help="first year (inclusive)")
    fetch.add_argument("--end-year", type=int, help="last year (inclusive)")
    fetch.add_argument("--start-period", help="SDMX startPeriod, e.g. 2023-06")
    fetch.add_argument("--end-period", help="SDMX endPeriod, e.g. 2024-05")
    fetch.add_argument(
        "--son-12-ay",
        "--last-12",
        dest="last_n",
        action="store_const",
        const=12,
        help="keep only the newest 12 periods",
    )
    fetch.add_argument("--last-n", dest="last_n", type=int, help="keep only the newest N periods")
    fetch.add_argument(
        "--key",
        action="append",
        default=[],
        metavar="DIM=CODE",
        help="dimension filter, repeatable (e.g. --key CITIES=TR34)",
    )
    fetch.add_argument("--labels", choices=("code", "label", "both"), default="code")
    fetch.add_argument("--dataset", help="override the dataflow id for this call")
    fetch.add_argument("--sdmx-json", action="store_true", help="request SDMX-JSON instead")
    fetch.add_argument("--no-cache", action="store_true")
    fetch.add_argument("--timeout", type=float, default=30.0)

    indicators = sub.add_parser("indicators", help="list curated indicators")
    indicators.add_argument("--json", action="store_true", help="machine readable output")

    search = sub.add_parser("search", help="search the catalogue offline")
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--json", action="store_true")

    dims = sub.add_parser("dims", help="show dimensions and codes of a dataflow")
    dims.add_argument("indicator", help="catalogue code or dataflow id")
    dims.add_argument("--json", action="store_true")
    dims.add_argument("--timeout", type=float, default=30.0)

    flatten = sub.add_parser("flatten", help="flatten a downloaded JSON-stat/SDMX-JSON file")
    flatten.add_argument("file")
    flatten.add_argument("--format", choices=("table", "json", "csv"), default="table")
    flatten.add_argument("--out")
    flatten.add_argument("--labels", choices=("code", "label", "both"), default="code")

    save = sub.add_parser("save", help="store a series locally (SQLite)")
    save.add_argument("indicator")
    save.add_argument("--name", help="series name in the store (default: indicator code)")
    save.add_argument("--store", help="database path")
    save.add_argument("--start-year", type=int)
    save.add_argument("--end-year", type=int)

    sub.add_parser("mcp", help="run the Model Context Protocol stdio server")
    return parser


def _parse_filters(pairs: Sequence[str]) -> dict[str, str]:
    filters: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise TuikError(f"--key expects DIM=CODE, got {pair!r}")
        name, _, value = pair.partition("=")
        if not name.strip() or not value.strip():
            raise TuikError(f"--key expects DIM=CODE, got {pair!r}")
        filters[name.strip()] = value.strip()
    return filters


def main(argv: Sequence[str] | None = None, *, client: TuikClient | None = None) -> int:
    """Entry point used by the console script; returns the process exit code."""
    _utf8_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_OK

    try:
        if args.command == "indicators":
            return _cmd_indicators(args)
        if args.command == "search":
            return _cmd_search(args)
        if args.command == "flatten":
            dataset = TuikClient.load(args.file)
            _emit(dataset, args)
            return EXIT_OK
        if args.command == "mcp":
            from .mcp_server import serve

            return serve()
        if args.command == "dims":
            active = client or TuikClient(timeout=args.timeout)
            flow = _resolve_flow(args.indicator)
            dimensions = active.dimensions(flow)
            if args.json:
                print(
                    json.dumps(
                        [
                            {
                                "id": dimension.id,
                                "label": dimension.label,
                                "codes": [
                                    {"code": category.code, "label": category.label}
                                    for category in dimension.categories
                                ],
                            }
                            for dimension in dimensions
                        ],
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                for dimension in dimensions:
                    print(f"{dimension.id}  ({dimension.label})")
                    for category in dimension.categories:
                        print(f"  {category.code:<16} {category.label}")
            return EXIT_OK
        if args.command == "save":
            active = client or TuikClient()
            dataset = active.get_indicator(
                args.indicator, start_year=args.start_year, end_year=args.end_year
            )
            name = args.name or args.indicator
            with SeriesStore(args.store) as store:
                store.save(name, dataset, indicator=args.indicator)
                info = store.info(name)
            print(f"saved {info.name}: {info.rows} rows -> {store.path}")
            return EXIT_OK
        if args.command == "fetch":
            active = client or TuikClient(timeout=args.timeout)
            filters = _parse_filters(args.key)
            if args.dataset:
                dataset = active.get_dataset(
                    args.dataset,
                    start_year=args.start_year,
                    end_year=args.end_year,
                    start_period=args.start_period,
                    end_period=args.end_period,
                    filters=filters,
                    fmt="sdmx-json" if args.sdmx_json else "jsonstat",
                    last_n=args.last_n,
                    use_cache=not args.no_cache,
                )
            else:
                dataset = active.get_indicator(
                    args.indicator,
                    start_year=args.start_year,
                    end_year=args.end_year,
                    start_period=args.start_period,
                    end_period=args.end_period,
                    filters=filters,
                    fmt="sdmx-json" if args.sdmx_json else "jsonstat",
                    last_n=args.last_n,
                    use_cache=not args.no_cache,
                )
            _emit(dataset, args)
            return EXIT_OK
    except TuikError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_TUIK
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        print("interrupted", file=sys.stderr)
        return 130
    parser.error(f"unknown command {args.command!r}")  # pragma: no cover - argparse guards


def _resolve_flow(name: str) -> str:
    """Accept either a catalogue code or a raw dataflow id."""
    try:
        from .catalogues import get_indicator

        return get_indicator(name).resolve_dataflow()
    except TuikError:
        if "," in name or name.upper().startswith("DF_"):
            return name
        raise


def _cmd_indicators(args: argparse.Namespace) -> int:
    rows = [_indicator_line(indicator) for indicator in CATALOGUE]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(render_table(rows, ["code", "title", "frequency", "dataflow"], max_width=60))
    return EXIT_OK


def _cmd_search(args: argparse.Namespace) -> int:
    from .catalogues import search as search_catalogue

    matches = search_catalogue(args.query)
    if not matches:
        print(f"no indicator matched {args.query!r}", file=sys.stderr)
        return EXIT_TUIK
    rows = [_indicator_line(indicator) for indicator in matches]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(render_table(rows, ["code", "title", "frequency", "dataflow"], max_width=60))
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
