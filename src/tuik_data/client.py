"""SDMX 2.1 REST client for the TÜİK statistical data service.

Wire protocol
-------------
TÜİK publishes its SDMX 2.1 service at ``https://nsiws.tuik.gov.tr/rest`` and
requires a bearer token obtained from the API key issued by the Data Portal.
Two response flavours are supported:

``jsonstat`` (default)
    ``GET /data/{flowRef}/{key}?format=JSONSTAT`` -> JSON-stat 2.0.
``sdmx-json``
    ``GET /data/{flowRef}/{key}?format=JSON`` with
    ``Accept: application/vnd.sdmx.data+json;version=1.0`` -> SDMX-JSON 2.0.

Both are decoded into the same :class:`~tuik_data.parser.Dataset`, so downstream
code never branches on the wire format.

Nothing is fetched at import time and no request is made until you call a method,
which keeps the library usable offline (``TuikClient.load()`` on a downloaded
file) and keeps tests hermetic.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from . import catalogues
from ._http import USER_AGENT, HttpResponse, Transport, build_url, redact, urllib_transport
from .auth import TokenManager
from .errors import (
    TuikAuthError,
    TuikConfigError,
    TuikDimensionError,
    TuikEmptyResult,
    TuikHTTPError,
    TuikNotFoundError,
    TuikParseError,
    TuikRateLimitError,
)
from .parser import Dataset, Dimension, parse_jsonstat, parse_sdmx_json

__all__ = ["DEFAULT_BASE_URL", "TuikClient"]

DEFAULT_BASE_URL = "https://nsiws.tuik.gov.tr/rest"
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "tuik-data"

_FORMATS: dict[str, tuple[str, str]] = {
    "jsonstat": ("JSONSTAT", "application/json"),
    "sdmx-json": ("JSON", "application/vnd.sdmx.data+json;version=1.0"),
}
_RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
_TIME_IDS = frozenset({"TIME_PERIOD", "TIME", "PERIOD"})


def normalise_dataflow(ref: str) -> str:
    """Expand a short dataflow id to a full SDMX ``flowRef``.

    ``DF_X``            -> ``TR,DF_X,1.0``
    ``TR,DF_X``         -> ``TR,DF_X,1.0``
    ``TR,DF_X,1.0``     -> unchanged
    """
    text = ref.strip()
    if not text:
        raise TuikConfigError("dataflow id is empty")
    parts = text.split(",")
    if len(parts) == 1:
        return f"TR,{text},1.0"
    if len(parts) == 2:
        return f"{text},1.0"
    if len(parts) == 3:
        return text
    raise TuikConfigError(f"dataflow id {ref!r} has too many comma-separated parts")


class TuikClient:
    """Client for TÜİK's SDMX REST service (zero runtime dependencies).

    Parameters
    ----------
    api_key:
        SMS-verified API key.  Defaults to the ``TUIK_API_KEY`` environment
        variable.  Only needed for network calls.
    base_url:
        SDMX service root; defaults to ``TUIK_BASE_URL`` or the official endpoint.
    cache_dir / cache_ttl:
        On-disk response cache (default ``~/.cache/tuik-data``, one hour TTL).
        ``cache_ttl=0`` disables caching.
    transport:
        Injectable HTTP callable; tests pass a fake and never touch the network.
    retries:
        Extra attempts for transient failures (429/5xx/timeouts).
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        token_url: str | None = None,
        timeout: float = 30.0,
        retries: int = 2,
        backoff: float = 0.5,
        cache_dir: str | Path | None = None,
        cache_ttl: float | None = 3600.0,
        transport: Transport | None = None,
        auth: TokenManager | None = None,
        sleep: Any = time.sleep,
        user_agent: str = USER_AGENT,
    ) -> None:
        self.base_url = (base_url or os.environ.get("TUIK_BASE_URL") or DEFAULT_BASE_URL).rstrip(
            "/"
        )
        self.timeout = float(timeout)
        self.retries = max(0, int(retries))
        self.backoff = max(0.0, float(backoff))
        self._transport: Transport = transport or urllib_transport
        self._user_agent = user_agent
        self._sleep = sleep
        self._dimension_cache: dict[str, tuple[Dimension, ...]] = {}
        self.cache_ttl = cache_ttl
        if cache_ttl and cache_ttl > 0:
            self.cache_dir: Path | None = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
        else:
            self.cache_dir = None
        token_kwargs: dict[str, Any] = {"transport": self._transport, "timeout": self.timeout}
        if token_url:
            token_kwargs["token_url"] = token_url
        self._auth = auth or TokenManager(api_key, **token_kwargs)

    # ------------------------------------------------------------------
    # context manager / lifecycle
    # ------------------------------------------------------------------
    def __enter__(self) -> TuikClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Release resources (urllib keeps none; present for API symmetry)."""
        self._dimension_cache.clear()

    # ------------------------------------------------------------------
    # HTTP layer
    # ------------------------------------------------------------------
    def _headers(self, accept: str, *, authorise: bool) -> dict[str, str]:
        headers = {"Accept": accept, "User-Agent": self._user_agent}
        if authorise:
            headers.update(self._auth.headers())
        return headers

    def _cache_path(self, url: str) -> Path | None:
        if self.cache_dir is None:
            return None
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, url: str) -> bytes | None:
        path = self._cache_path(url)
        if path is None or not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, Mapping) or payload.get("url") != url:
            return None
        try:
            saved_at = float(payload.get("saved_at", 0))
        except (TypeError, ValueError):
            # A hand-edited or truncated cache entry must never break a fetch.
            return None
        ttl = self.cache_ttl or 0
        if ttl > 0 and time.time() - saved_at > ttl:
            return None
        body = payload.get("body")
        return body.encode("utf-8") if isinstance(body, str) else None

    def _write_cache(self, url: str, body: bytes) -> None:
        path = self._cache_path(url)
        if path is None:
            return
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            # The cache stores JSON text; a non-UTF-8 body is served as-is.
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps({"url": url, "saved_at": time.time(), "body": text}),
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError:  # a read-only cache must never break a fetch
            return

    def _request(
        self,
        path: str,
        params: Mapping[str, str | int | None] | None = None,
        *,
        accept: str = "application/json",
        authorise: bool = True,
        allow_cache: bool = True,
    ) -> HttpResponse:
        url = build_url(self.base_url, path, params or {})
        if allow_cache:
            cached = self._read_cache(url)
            if cached is not None:
                return HttpResponse(200, cached, {"x-tuik-cache": "hit"})

        attempts = self.retries + 1
        refreshed = False
        attempt = 0
        while True:
            headers = self._headers(accept, authorise=authorise)
            response = self._transport(url, headers, None, self.timeout)
            status = response.status

            if status == 200:
                if allow_cache:
                    self._write_cache(url, response.body)
                return response

            if status == 401:
                # One free retry after renewing the token: expiry is expected.
                if authorise and not refreshed and self._auth.has_key:
                    refreshed = True
                    self._auth.invalidate()
                    continue
                raise TuikAuthError(
                    f"TÜİK rejected the request (HTTP 401) at {redact(url)}. "
                    "Check that TUIK_API_KEY is set and still valid."
                )
            if status == 403:
                raise TuikAuthError(
                    f"TÜİK refused access (HTTP 403) at {redact(url)}. "
                    "The API key may lack permission for this dataflow."
                )
            if status == 404:
                raise TuikNotFoundError(404, url, response.text())
            if status == 429:
                error: TuikHTTPError = TuikRateLimitError(429, url, response.text())
            else:
                error = TuikHTTPError(status, url, response.text())

            if status in _RETRY_STATUS and attempt + 1 < attempts:
                self._sleep(self.backoff * (2**attempt))
                attempt += 1
                continue
            raise error

    def _json(self, response: HttpResponse) -> Mapping[str, Any]:
        try:
            document = json.loads(response.text())
        except json.JSONDecodeError as exc:
            snippet = redact(response.text()[:200])
            raise TuikParseError(f"response is not JSON ({exc}): {snippet}") from exc
        if not isinstance(document, Mapping):
            raise TuikParseError(f"expected a JSON object, got {type(document).__name__}")
        return document

    # ------------------------------------------------------------------
    # decoding
    # ------------------------------------------------------------------
    @staticmethod
    def flatten(document: Mapping[str, Any] | str | bytes) -> Dataset:
        """Decode JSON-stat 2.0 *or* SDMX-JSON 2.0 without knowing which it is."""
        if isinstance(document, bytes | bytearray):
            try:
                document = bytes(document).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise TuikParseError("payload is not UTF-8") from exc
        if isinstance(document, str):
            try:
                document = json.loads(document)
            except json.JSONDecodeError as exc:
                raise TuikParseError(f"payload is not JSON: {exc}") from exc
        if not isinstance(document, Mapping):
            raise TuikParseError("payload must be a JSON object")
        if "data" in document or "structure" in document:
            return parse_sdmx_json(document)
        return parse_jsonstat(document)

    @classmethod
    def load(cls, path: str | Path) -> Dataset:
        """Decode a JSON-stat or SDMX-JSON file saved from the Data Portal."""
        raw = Path(path).read_bytes()
        return cls.flatten(raw)

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------
    def get_dataset(
        self,
        dataflow: str,
        key: str | Mapping[str, str] | None = None,
        *,
        start_period: str | None = None,
        end_period: str | None = None,
        start_year: int | None = None,
        end_year: int | None = None,
        filters: Mapping[str, str] | None = None,
        fmt: Literal["jsonstat", "sdmx-json"] = "jsonstat",
        last_n: int | None = None,
        use_cache: bool = True,
    ) -> Dataset:
        """Fetch one dataflow as a :class:`~tuik_data.parser.Dataset`.

        ``filters`` maps dimension ids to category codes; the client expands it into
        an SDMX series key using the dataflow's own dimension order, and raises
        :class:`~tuik_data.errors.TuikDimensionError` listing valid codes when a
        code does not exist.
        """
        if fmt not in _FORMATS:
            raise TuikConfigError(f"unknown format {fmt!r}; choose one of {sorted(_FORMATS)}")
        flow_ref = normalise_dataflow(dataflow)
        if start_year is not None or end_year is not None:
            bounds = catalogues.enrich_period_bounds(start_year, end_year)
            start_period = start_period or bounds["startPeriod"]
            end_period = end_period or bounds["endPeriod"]

        merged_filters = dict(filters or {})
        if isinstance(key, Mapping):
            merged_filters.update(key)
            key = None
        if key is not None:
            series_key = str(key).strip()
        elif merged_filters:
            series_key = self.build_key(flow_ref, merged_filters)
        else:
            series_key = ""

        if last_n is not None and last_n <= 0:
            raise TuikConfigError("last_n must be a positive integer")

        fmt_param, accept = _FORMATS[fmt]
        params = {
            "format": fmt_param,
            "startPeriod": start_period,
            "endPeriod": end_period,
        }
        path = f"data/{flow_ref}"
        if series_key:
            path = f"{path}/{series_key}"
        response = self._request(path, params, accept=accept, allow_cache=use_cache)
        document = self._json(response)
        dataset = parse_sdmx_json(document) if "data" in document else parse_jsonstat(document)

        if len(dataset) == 0 or not any(value is not None for value in dataset.values):
            if merged_filters or series_key:
                raise self._dimension_error(flow_ref, merged_filters, series_key)
            raise TuikEmptyResult(
                f"{flow_ref} returned no observations"
                + (
                    f" between {start_period} and {end_period}"
                    if start_period or end_period
                    else ""
                )
                + ". Try a wider period or `tuik-data dims` to inspect the dataflow."
            )
        if last_n is not None:
            dataset = self._trim_last(dataset, last_n)
        return dataset

    def build_key(self, flow_ref: str, filters: Mapping[str, str]) -> str:
        """Order ``filters`` into an SDMX series key after validating the codes."""
        dimensions = self.dimensions(flow_ref)
        by_id = {dimension.id: dimension for dimension in dimensions}
        unknown = [name for name in filters if name not in by_id]
        if unknown:
            raise TuikDimensionError(
                f"unknown dimension(s) {unknown} for {flow_ref}. Available: {list(by_id)}",
                available={dimension.id: list(dimension.codes) for dimension in dimensions},
            )
        positions: dict[int, str] = {}
        for name, wanted in filters.items():
            dimension = by_id[name]
            try:
                code = dimension.find(wanted)
            except KeyError as exc:
                preview = ", ".join(dimension.codes[:12])
                raise TuikDimensionError(
                    f"{wanted!r} is not a category of dimension {name!r} in {flow_ref}. "
                    f"Valid codes: {preview}{' ...' if len(dimension.codes) > 12 else ''}",
                    available={name: list(dimension.codes)},
                ) from exc
            positions[dimensions.index(dimension)] = code
        parts: list[str] = []
        for position in range(len(dimensions)):
            parts.append(positions.get(position, ""))
        # SDMX lets a key stop early; trailing wildcards are noise in URLs.
        return ".".join(parts).rstrip(".")

    def dimensions(self, dataflow: str) -> tuple[Dimension, ...]:
        """Return the dataflow's dimensions and codelists (``detail=nodata``)."""
        flow_ref = normalise_dataflow(dataflow)
        if flow_ref in self._dimension_cache:
            return self._dimension_cache[flow_ref]
        response = self._request(
            f"data/{flow_ref}",
            {"detail": "nodata", "format": "JSON"},
            accept="application/vnd.sdmx.data+json;version=1.0",
        )
        dataset = parse_sdmx_json(self._json(response))
        self._dimension_cache[flow_ref] = dataset.dimensions
        return dataset.dimensions

    def get_indicator(
        self,
        code: str,
        *,
        start_year: int | None = None,
        end_year: int | None = None,
        start_period: str | None = None,
        end_period: str | None = None,
        filters: Mapping[str, str] | None = None,
        fmt: Literal["jsonstat", "sdmx-json"] = "jsonstat",
        last_n: int | None = None,
        use_cache: bool = True,
    ) -> Dataset:
        """Fetch a catalogued indicator, e.g. ``client.get_indicator("tufe_genel")``."""
        indicator = catalogues.get_indicator(code)
        return self.get_dataset(
            indicator.resolve_dataflow(),
            None,
            start_period=start_period,
            end_period=end_period,
            start_year=start_year,
            end_year=end_year,
            filters=filters,
            fmt=fmt,
            last_n=last_n,
            use_cache=use_cache,
        )

    def get_tufe_annual(self, year: int) -> Dataset:
        """Return the CPI observations TÜİK published for ``year``.

        This is a convenience wrapper: it selects the observations labelled with
        ``year`` and returns them unchanged.  It does **not** compute an annual
        average or a year-on-year rate - use the series' own published measure for
        anything official.
        """
        return self.get_indicator("tufe_genel", start_year=year, end_year=year)

    def search(self, query: str) -> list[catalogues.Indicator]:
        """Search the local indicator catalogue (no network access)."""
        return catalogues.search(query)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _dimension_error(
        self, flow_ref: str, filters: Mapping[str, str], series_key: str
    ) -> TuikDimensionError:
        detail = f"the filter key {series_key!r} matched no observations"
        try:
            dimensions = self.dimensions(flow_ref)
        except Exception:
            return TuikDimensionError(
                f"{detail} in {flow_ref}. Run `tuik-data dims {flow_ref}` to list valid codes.",
                available={name: [] for name in filters},
            )
        lines = [f"{detail} in {flow_ref}.", "Available categories:"]
        available: dict[str, list[str]] = {}
        for dimension in dimensions:
            available[dimension.id] = list(dimension.codes)
            preview = ", ".join(
                f"{category.code} ({category.label})"
                if category.label and category.label != category.code
                else category.code
                for category in dimension.categories[:8]
            )
            more = " ..." if len(dimension.categories) > 8 else ""
            lines.append(f"  - {dimension.id}: {preview}{more}")
        return TuikDimensionError("\n".join(lines), available=available)

    def _trim_last(self, dataset: Dataset, last_n: int) -> Dataset:
        for dimension in dataset.dimensions:
            if dimension.role == "time" or dimension.id.upper() in _TIME_IDS:
                # Natural sort keeps "2024-9" before "2024-10"; a plain string
                # sort would silently return the wrong "newest" window.
                codes = sorted(dimension.codes, key=_period_key)[-last_n:]
                return dataset.take(dimension.id, codes)
        raise TuikConfigError(f"cannot apply last_n: {dataset.dimension_ids} has no time dimension")


def _period_key(code: str) -> tuple[Any, ...]:
    """Sort key that compares digit runs numerically and text case-insensitively."""
    parts = re.split(r"(\d+)", code)
    return tuple((0, int(part)) if part.isdigit() else (1, part.casefold()) for part in parts)
