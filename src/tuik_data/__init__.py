"""tuik-data - a zero-dependency Python client for TÜİK open statistics.

Quick start
-----------
>>> from tuik_data import TuikClient                 # doctest: +SKIP
>>> client = TuikClient()                            # doctest: +SKIP
>>> data = client.get_indicator("tufe_genel", last_n=12)   # doctest: +SKIP
>>> data.records(labels="both")[0]                   # doctest: +SKIP

Offline, without an API key::

>>> from tuik_data import TuikClient                 # doctest: +SKIP
>>> data = TuikClient.load("tufe.json")              # doctest: +SKIP
>>> data.to_csv("tufe.csv")                          # doctest: +SKIP

This library ships identifiers, never values.  Every figure comes from TÜİK at
request time, and official bulletins remain the authoritative source.
"""

from __future__ import annotations

from .auth import TokenManager
from .catalogues import CATALOGUE, Indicator, get_indicator, list_indicators, search
from .client import DEFAULT_BASE_URL, TuikClient
from .errors import (
    TuikAuthError,
    TuikConfigError,
    TuikDimensionError,
    TuikEmptyResult,
    TuikError,
    TuikHTTPError,
    TuikNetworkError,
    TuikNotFoundError,
    TuikParseError,
    TuikRateLimitError,
)
from .parser import Category, Dataset, Dimension, parse_jsonstat, parse_sdmx_json
from .storage import SeriesStore, StoredSeries

__version__ = "0.1.0"

__all__ = [
    "CATALOGUE",
    "DEFAULT_BASE_URL",
    "Category",
    "Dataset",
    "Dimension",
    "Indicator",
    "SeriesStore",
    "StoredSeries",
    "TokenManager",
    "TuikAuthError",
    "TuikClient",
    "TuikConfigError",
    "TuikDimensionError",
    "TuikEmptyResult",
    "TuikError",
    "TuikHTTPError",
    "TuikNetworkError",
    "TuikNotFoundError",
    "TuikParseError",
    "TuikRateLimitError",
    "__version__",
    "get_indicator",
    "list_indicators",
    "parse_jsonstat",
    "parse_sdmx_json",
    "search",
]
