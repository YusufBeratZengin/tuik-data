"""Offline series storage on top of :mod:`sqlite3` (standard library only).

Every fetched dataset can be kept locally in long form, so notebooks and reports
keep working without a network connection or an API key.  One file holds many
series; a rebuild of the same series replaces it atomically.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import TuikConfigError, TuikError
from .parser import Dataset

__all__ = ["SeriesStore", "StoredSeries", "default_store_path"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS series (
    name        TEXT PRIMARY KEY,
    indicator   TEXT NOT NULL DEFAULT '',
    label       TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT '',
    updated     TEXT NOT NULL DEFAULT '',
    dimensions  TEXT NOT NULL,
    value_columns TEXT NOT NULL,
    saved_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS observations (
    name     TEXT NOT NULL REFERENCES series(name) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    coords   TEXT NOT NULL,
    value    REAL,
    status   TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (name, position)
);
CREATE INDEX IF NOT EXISTS observations_name ON observations(name);
"""


def default_store_path() -> Path:
    """Default database location (``$TUIK_STORE`` overrides)."""
    import os

    override = os.environ.get("TUIK_STORE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "share" / "tuik-data" / "series.sqlite3"


@dataclass(frozen=True)
class StoredSeries:
    """Metadata about one saved series."""

    name: str
    indicator: str
    label: str
    source: str
    updated: str
    dimensions: tuple[str, ...]
    rows: int
    saved_at: str


class SeriesStore:
    """SQLite-backed store for flattened series.

    >>> store = SeriesStore(":memory:")               # doctest: +SKIP
    >>> store.save("tufe", dataset, indicator="tufe_genel")   # doctest: +SKIP
    >>> store.load("tufe").rows()                     # doctest: +SKIP
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_store_path()
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.path))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.executescript(_SCHEMA)
        self._connection.commit()

    def __enter__(self) -> SeriesStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying connection."""
        self._connection.close()

    # -- writing ------------------------------------------------------------
    def save(self, name: str, dataset: Dataset, *, indicator: str = "") -> None:
        """Insert or replace a series in one transaction."""
        clean = name.strip()
        if not clean:
            raise TuikConfigError("series name must not be empty")
        rows = list(dataset.rows(labels="code"))
        payload = [
            (
                clean,
                position,
                json.dumps(
                    [row[dimension] for dimension in dataset.dimension_ids],
                    ensure_ascii=False,
                ),
                row["value"],
                row.get("status", ""),
            )
            for position, row in enumerate(rows)
        ]
        with self._connection:
            self._connection.execute("DELETE FROM series WHERE name = ?", (clean,))
            self._connection.execute(
                "INSERT INTO series (name, indicator, label, source, updated, dimensions,"
                " value_columns) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    clean,
                    indicator,
                    dataset.label,
                    dataset.source,
                    dataset.updated,
                    json.dumps(list(dataset.dimension_ids), ensure_ascii=False),
                    json.dumps(["value", "status"]),
                ),
            )
            self._connection.executemany(
                "INSERT INTO observations (name, position, coords, value, status)"
                " VALUES (?, ?, ?, ?, ?)",
                payload,
            )

    def delete(self, name: str) -> bool:
        """Delete a series; returns True when something was removed."""
        with self._connection:
            cursor = self._connection.execute("DELETE FROM series WHERE name = ?", (name,))
        return cursor.rowcount > 0

    # -- reading ------------------------------------------------------------
    def names(self) -> list[str]:
        """Sorted list of stored series names."""
        cursor = self._connection.execute("SELECT name FROM series ORDER BY name")
        return [row["name"] for row in cursor]

    def info(self, name: str) -> StoredSeries:
        """Metadata for one series; raises :class:`TuikError` when absent."""
        row = self._connection.execute(
            "SELECT name, indicator, label, source, updated, dimensions, saved_at FROM series"
            " WHERE name = ?",
            (name,),
        ).fetchone()
        if row is None:
            raise TuikError(f"no stored series named {name!r}; available: {self.names()}")
        count = self._connection.execute(
            "SELECT COUNT(*) AS n FROM observations WHERE name = ?", (name,)
        ).fetchone()["n"]
        return StoredSeries(
            name=row["name"],
            indicator=row["indicator"],
            label=row["label"],
            source=row["source"],
            updated=row["updated"],
            dimensions=tuple(json.loads(row["dimensions"])),
            rows=int(count),
            saved_at=row["saved_at"],
        )

    def rows(self, name: str) -> Iterator[dict[str, Any]]:
        """Yield stored rows as dicts keyed by dimension id, ``value`` and ``status``."""
        meta = self.info(name)
        cursor = self._connection.execute(
            "SELECT coords, value, status FROM observations WHERE name = ? ORDER BY position",
            (name,),
        )
        for row in cursor:
            coords = json.loads(row["coords"])
            record = dict(zip(meta.dimensions, coords, strict=False))
            record["value"] = row["value"]
            record["status"] = row["status"]
            yield record

    def frame(self, name: str) -> list[dict[str, Any]]:
        """Materialise :meth:`rows` as a list."""
        return list(self.rows(name))

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        """Run a read-only SQL query and return rows as dicts.

        Only ``SELECT``/``WITH`` statements are accepted *and* the connection is
        switched to ``query_only`` while the statement runs, so a CTE-prefixed
        ``DELETE``/``UPDATE`` cannot mutate the file behind the store's back.
        """
        statement = sql.strip().lstrip("(").split(None, 1)
        if not statement or statement[0].upper() not in ("SELECT", "WITH"):
            raise TuikConfigError("only SELECT / WITH queries are allowed")
        self._connection.execute("PRAGMA query_only = ON")
        try:
            cursor = self._connection.execute(sql, tuple(params))
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.OperationalError as exc:
            if "readonly" in str(exc).lower() or "read-only" in str(exc).lower():
                raise TuikConfigError("only SELECT / WITH queries are allowed") from exc
            raise
        finally:
            self._connection.execute("PRAGMA query_only = OFF")
