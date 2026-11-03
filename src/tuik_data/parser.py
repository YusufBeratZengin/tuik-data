"""JSON-stat 2.0 and SDMX-JSON 2.0 decoders, implemented with the standard library.

The public entry points are:

``parse_jsonstat(doc)``
    Turn a JSON-stat 2.0 *dataset* (``dict`` or ``str``/``bytes``) into a
    :class:`Dataset` with a dense value array, dimension metadata and cell status.

``parse_sdmx_json(doc)``
    Turn an SDMX-JSON 2.0 *data* message into the same :class:`Dataset` model, so
    that both wire formats flatten identically.

Design rules
------------
* Nothing is dropped.  ``Dataset.raw`` keeps the decoded document, unmapped cells
  become ``None`` (never a shifted neighbour), and ``Dataset.status`` keeps the
  provider's confidentiality flags.
* Tensor order follows the JSON-stat specification: ``id`` lists the dimension
  identifiers and the last dimension varies fastest (row-major / C order).
"""

from __future__ import annotations

import csv
import io
import json
import math
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from .errors import TuikParseError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path

    import polars as pl

__all__ = [
    "MISSING_TOKENS",
    "Category",
    "Dataset",
    "Dimension",
    "parse_jsonstat",
    "parse_sdmx_json",
]

#: String tokens that providers use for "no observation".  They are decoded as
#: ``None`` so that a cell never silently becomes text in a numeric column.
MISSING_TOKENS = frozenset({"", ".", "..", "...", ":", "-", "n/a", "na", "nan", "null"})

LabelMode = Literal["code", "label", "both"]


def _as_text(raw: Any) -> str:
    """Return ``raw`` as UTF-8 safe text (Turkish labels survive untouched)."""
    if isinstance(raw, str):
        return raw
    return str(raw)


def _coerce_number(raw: Any) -> float | int | None:
    """Convert a JSON-stat value cell to a number or ``None``."""
    if raw is None:
        return None
    if isinstance(raw, bool):  # bool is an int subclass; never a valid observation
        return None
    if isinstance(raw, int | float):
        if isinstance(raw, float) and not math.isfinite(raw):
            return None
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if text.lower() in MISSING_TOKENS:
            return None
        try:
            return int(text)
        except ValueError:
            pass
        try:
            return float(text)
        except ValueError:
            return None
    return None


@dataclass(frozen=True)
class Category:
    """One category (code) of a dimension, with its optional label and unit."""

    code: str
    label: str
    unit: Mapping[str, Any] | None = None

    def __str__(self) -> str:  # pragma: no cover - convenience
        return self.label or self.code


@dataclass(frozen=True)
class Dimension:
    """A JSON-stat dimension: identifier, categories in dataset order and role."""

    id: str
    label: str
    categories: tuple[Category, ...]
    role: str | None = None

    def __len__(self) -> int:
        return len(self.categories)

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(category.code for category in self.categories)

    def find(self, code_or_label: str) -> str:
        """Resolve a category code or label to its code (case-insensitive)."""
        needle = code_or_label.strip()
        for category in self.categories:
            if category.code == needle:
                return category.code
        folded = needle.casefold()
        for category in self.categories:
            if category.code.casefold() == folded or category.label.casefold() == folded:
                return category.code
        raise KeyError(code_or_label)


@dataclass
class Dataset:
    """A dense, flattened view of a JSON-stat 2.0 / SDMX-JSON 2.0 datacube."""

    dimensions: tuple[Dimension, ...]
    values: tuple[float | int | None, ...]
    status: dict[int, str] = field(default_factory=dict)
    label: str = ""
    source: str = ""
    updated: str = ""
    href: str = ""
    raw: Any = None
    coerced_missing: int = 0

    # -- basic shape ---------------------------------------------------------
    @property
    def size(self) -> tuple[int, ...]:
        return tuple(len(dimension) for dimension in self.dimensions)

    @property
    def dimension_ids(self) -> tuple[str, ...]:
        return tuple(dimension.id for dimension in self.dimensions)

    @property
    def missing(self) -> int:
        """Number of ``None`` cells (masked, ``..``, out of range, ...)."""
        return sum(1 for value in self.values if value is None)

    def __len__(self) -> int:
        return len(self.values)

    def __repr__(self) -> str:
        return (
            f"<Dataset {self.label or 'unlabelled'!r} dims={list(self.dimension_ids)} "
            f"cells={len(self)} missing={self.missing}>"
        )

    # -- coordinates ---------------------------------------------------------
    def _strides(self) -> tuple[int, ...]:
        strides: list[int] = []
        acc = 1
        for size in reversed(self.size):
            strides.append(acc)
            acc *= size
        return tuple(reversed(strides))

    def _index(self, coords: Sequence[int]) -> int:
        strides = self._strides()
        if len(coords) != len(strides):
            raise IndexError(f"expected {len(strides)} coordinates, got {len(coords)}")
        index = 0
        for coord, size, stride in zip(coords, self.size, strides, strict=True):
            if not 0 <= coord < size:
                raise IndexError(f"coordinate {coord} outside dimension of size {size}")
            index += coord * stride
        return index

    def _coords(self, index: int) -> tuple[int, ...]:
        coords: list[int] = []
        for size, stride in zip(self.size, self._strides(), strict=True):
            coords.append((index // stride) % size)
        return tuple(coords)

    def cell(self, **kwargs: str) -> float | int | None:
        """Return one value addressed by category code, e.g. ``cell(TIME_PERIOD="2024")``."""
        if set(kwargs) != set(self.dimension_ids):
            raise KeyError(f"expected dimension codes {self.dimension_ids}, got {sorted(kwargs)}")
        coords: list[int] = []
        for dimension in self.dimensions:
            code = dimension.find(kwargs[dimension.id])
            coords.append(dimension.codes.index(code))
        return self.values[self._index(coords)]

    def iter_cells(self) -> Iterator[tuple[tuple[str, ...], float | int | None]]:
        """Yield ``(codes, value)`` pairs in tensor order (last dimension fastest)."""
        codes_per_dim = [dimension.codes for dimension in self.dimensions]
        for index, value in enumerate(self.values):
            coords = self._coords(index)
            yield tuple(codes_per_dim[i][coord] for i, coord in enumerate(coords)), value

    # -- flat output ---------------------------------------------------------
    def _column(self, dimension: Dimension, mode: LabelMode) -> list[str]:
        if mode == "code":
            return list(dimension.codes)
        if mode == "label":
            return [category.label or category.code for category in dimension.categories]
        return [
            category.code
            if category.label in ("", category.code)
            else f"{category.code} - {category.label}"
            for category in dimension.categories
        ]

    def rows(
        self,
        *,
        labels: LabelMode = "code",
        include_status: bool = True,
        include_unit: bool = False,
    ) -> Iterator[dict[str, Any]]:
        """Yield one flat ``dict`` per cell, in tensor order.

        Parameters
        ----------
        labels:
            ``"code"`` keeps raw codes, ``"label"`` uses human labels and
            ``"both"`` produces ``"01 - Food and non-alcoholic beverages"``.
        include_status:
            Add a ``status`` column holding the provider flag (``""`` when absent).
        include_unit:
            Add a ``unit`` column from ``category.unit["label"]`` when available.
        """
        columns = [self._column(dimension, labels) for dimension in self.dimensions]
        coords = [0] * len(self.dimensions)
        for index, value in enumerate(self.values):
            row: dict[str, Any] = {
                dimension.id: columns[position][coords[position]]
                for position, dimension in enumerate(self.dimensions)
            }
            row["value"] = value
            if include_status:
                row["status"] = self.status.get(index, "")
            if include_unit:
                row["unit"] = self._unit_for(coords)
            yield row
            # advance the odometer (last dimension fastest)
            for position in range(len(coords) - 1, -1, -1):
                coords[position] += 1
                if coords[position] < self.size[position]:
                    break
                coords[position] = 0

    def _unit_for(self, coords: Sequence[int]) -> str:
        for position, dimension in enumerate(self.dimensions):
            unit = dimension.categories[coords[position]].unit
            if unit:
                label = unit.get("label") or unit.get("symbol") or ""
                if label:
                    return _as_text(label)
        return ""

    def records(self, **kwargs: Any) -> list[dict[str, Any]]:
        """Materialise :meth:`rows` as a list."""
        return list(self.rows(**kwargs))

    def to_csv(
        self,
        path: str | Path | None = None,
        *,
        labels: LabelMode = "code",
        delimiter: str = ",",
        include_unit: bool = False,
    ) -> str:
        """Render as CSV (UTF-8, ``\\n`` line endings) and optionally write ``path``."""
        buffer = io.StringIO()
        fieldnames = [*self.dimension_ids, "value", "status"]
        if include_unit:
            fieldnames.insert(len(self.dimension_ids) + 1, "unit")
        writer = csv.DictWriter(
            buffer, fieldnames=fieldnames, delimiter=delimiter, lineterminator="\n"
        )
        writer.writeheader()
        for row in self.rows(labels=labels, include_status=True, include_unit=include_unit):
            writer.writerow({key: ("" if row[key] is None else row[key]) for key in fieldnames})
        text = buffer.getvalue()
        if path is not None:
            from pathlib import Path as _Path

            _Path(path).write_text(text, encoding="utf-8")
        return text

    def to_json(self, *, indent: int | None = 2) -> str:
        """Render the flat rows as a JSON array (UTF-8 preserved, ``None`` kept)."""
        return json.dumps(self.records(), ensure_ascii=False, indent=indent)

    def to_polars(self) -> pl.DataFrame:
        """Return a Polars ``DataFrame`` (requires the optional ``polars`` extra)."""
        try:
            import polars as pl
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ImportError(
                "to_polars() needs the optional dependency: pip install 'tuik-data[polars]'"
            ) from exc
        columns: dict[str, list[Any]] = {dimension.id: [] for dimension in self.dimensions}
        columns["value"] = []
        for row in self.rows(labels="code", include_status=False):
            for dimension in self.dimensions:
                columns[dimension.id].append(row[dimension.id])
            columns["value"].append(row["value"])
        return pl.DataFrame(columns)

    # -- subsetting ----------------------------------------------------------
    def select(self, drop: bool = True, **filters: str) -> Dataset:
        """Return a sub-cube where each filtered dimension is fixed to one category.

        ``drop=True`` removes single-category dimensions from the result, which is
        what you want for flattening (``select(CITIES="TR34")`` -> one row per period).
        """
        resolved: dict[int, int] = {}
        for name, wanted in filters.items():
            position = self._dimension_position(name)
            dimension = self.dimensions[position]
            code = dimension.find(wanted)
            resolved[position] = dimension.codes.index(code)

        axes: list[list[int]] = []
        kept_positions: list[int] = []
        for position, dimension in enumerate(self.dimensions):
            if position in resolved:
                if drop:
                    continue
                kept_positions.append(position)
                axes.append([resolved[position]])
            else:
                kept_positions.append(position)
                axes.append(list(range(len(dimension))))

        new_size = tuple(len(axis) for axis in axes)
        values: list[float | int | None] = []
        status: dict[int, str] = {}
        old_strides = self._strides()
        for new_index, new_coords in enumerate(_product([range(size) for size in new_size])):
            old_coords = [0] * len(self.dimensions)
            for slot, position in enumerate(kept_positions):
                old_coords[position] = axes[slot][new_coords[slot]]
            old_index = sum(
                coord * stride for coord, stride in zip(old_coords, old_strides, strict=True)
            )
            values.append(self.values[old_index])
            if old_index in self.status:
                status[new_index] = self.status[old_index]

        dimensions: list[Dimension] = []
        for position in kept_positions:
            dimension = self.dimensions[position]
            if position in resolved:
                dimensions.append(
                    Dimension(
                        id=dimension.id,
                        label=dimension.label,
                        categories=(dimension.categories[resolved[position]],),
                        role=dimension.role,
                    )
                )
            else:
                dimensions.append(dimension)

        return Dataset(
            dimensions=tuple(dimensions),
            values=tuple(values),
            status=status,
            label=self.label,
            source=self.source,
            updated=self.updated,
            href=self.href,
            raw=self.raw,
        )

    def take(self, dimension_id: str, codes: Sequence[str]) -> Dataset:
        """Keep only ``codes`` along one dimension, in the given order.

        Useful for "last 12 periods" views without re-fetching, and safe with
        missing cells: the value array is rebuilt, never sliced by offset.
        """
        position = self._dimension_position(dimension_id)
        dimension = self.dimensions[position]
        wanted: list[int] = []
        for code in codes:
            resolved = dimension.find(code)
            wanted.append(dimension.codes.index(resolved))
        axes: list[list[int]] = []
        for slot, current in enumerate(self.dimensions):
            if slot == position:
                axes.append(wanted)
            else:
                axes.append(list(range(len(current))))
        old_strides = self._strides()
        new_size = tuple(len(axis) for axis in axes)
        values: list[float | int | None] = []
        status: dict[int, str] = {}
        for new_index, coords in enumerate(_product([range(size) for size in new_size])):
            old_coords = [axes[slot][coord] for slot, coord in enumerate(coords)]
            old_index = sum(
                coord * stride for coord, stride in zip(old_coords, old_strides, strict=True)
            )
            values.append(self.values[old_index])
            if old_index in self.status:
                status[new_index] = self.status[old_index]
        kept = list(self.dimensions)
        kept[position] = Dimension(
            id=dimension.id,
            label=dimension.label,
            categories=tuple(dimension.categories[index] for index in wanted),
            role=dimension.role,
        )
        return Dataset(
            dimensions=tuple(kept),
            values=tuple(values),
            status=status,
            label=self.label,
            source=self.source,
            updated=self.updated,
            href=self.href,
            raw=self.raw,
        )

    def _dimension_position(self, name: str) -> int:
        for position, dimension in enumerate(self.dimensions):
            if dimension.id == name:
                return position
        folded = name.casefold()
        for position, dimension in enumerate(self.dimensions):
            if dimension.id.casefold() == folded or dimension.label.casefold() == folded:
                return position
        raise KeyError(f"unknown dimension {name!r}; available: {list(self.dimension_ids)}")


def _product(ranges: Sequence[range]) -> Iterable[tuple[int, ...]]:
    if not ranges:
        yield ()
        return
    coords = [0] * len(ranges)
    while True:
        yield tuple(coords)
        for position in range(len(ranges) - 1, -1, -1):
            coords[position] += 1
            if coords[position] < len(ranges[position]):
                break
            coords[position] = 0
        else:
            return


# ---------------------------------------------------------------------------
# JSON-stat 2.0
# ---------------------------------------------------------------------------
def _load(doc: Any) -> Mapping[str, Any]:
    if isinstance(doc, bytes | bytearray):
        try:
            doc = doc.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TuikParseError("response is not valid UTF-8") from exc
    if isinstance(doc, str):
        try:
            doc = json.loads(doc)
        except json.JSONDecodeError as exc:
            raise TuikParseError(f"response is not valid JSON: {exc}") from exc
    if not isinstance(doc, Mapping):
        raise TuikParseError(f"expected a JSON object, got {type(doc).__name__}")
    return doc


def _build_categories(raw_dimension: Mapping[str, Any], dim_id: str) -> tuple[Category, ...]:
    category = raw_dimension.get("category")
    if category is None:
        raise TuikParseError(f"dimension {dim_id!r} has no category object")
    if not isinstance(category, Mapping):
        raise TuikParseError(f"dimension {dim_id!r} category is not an object")

    index = category.get("index")
    labels = category.get("label") or {}
    units = category.get("unit") or {}
    if not isinstance(labels, Mapping) or not isinstance(units, Mapping):
        raise TuikParseError(f"dimension {dim_id!r} has malformed label/unit maps")

    if index is None:
        codes = list(labels) or []
        if not codes:
            raise TuikParseError(f"dimension {dim_id!r} has neither index nor label")
        positions = {code: position for position, code in enumerate(codes)}
    elif isinstance(index, Mapping):
        positions = {}
        for code, position in index.items():
            if not isinstance(position, int) or isinstance(position, bool) or position < 0:
                raise TuikParseError(f"dimension {dim_id!r} has a non-integer category index")
            positions[_as_text(code)] = position
    elif isinstance(index, list):
        positions = {_as_text(code): position for position, code in enumerate(index)}
    else:
        raise TuikParseError(f"dimension {dim_id!r} index must be an object or an array")

    ordered = sorted(positions.items(), key=lambda item: item[1])
    expected = list(range(len(ordered)))
    if [position for _, position in ordered] != expected:
        raise TuikParseError(
            f"dimension {dim_id!r} category index is not a contiguous 0..{len(ordered) - 1} range"
        )

    return tuple(
        Category(
            code=code,
            label=_as_text(labels.get(code, code)),
            unit=units.get(code) if isinstance(units.get(code), Mapping) else None,
        )
        for code, _ in ordered
    )


def _role_map(document: Mapping[str, Any]) -> dict[str, str]:
    roles: dict[str, str] = {}
    raw_role = document.get("role")
    if not isinstance(raw_role, Mapping):
        return roles
    for role_name, members in raw_role.items():
        if isinstance(members, str):
            members = [members]
        if not isinstance(members, list):
            continue
        for member in members:
            roles[_as_text(member)] = _as_text(role_name)
    return roles


def _status_map(raw_status: Any, total: int) -> dict[int, str]:
    if raw_status is None:
        return {}
    if isinstance(raw_status, str):
        return dict.fromkeys(range(total), raw_status) if raw_status else {}
    if isinstance(raw_status, list):
        return {
            index: _as_text(item)
            for index, item in enumerate(raw_status[:total])
            if item not in (None, "")
        }
    if isinstance(raw_status, Mapping):
        status: dict[int, str] = {}
        for key, value in raw_status.items():
            try:
                index = int(key)
            except (TypeError, ValueError) as exc:
                raise TuikParseError(f"status key {key!r} is not a cell index") from exc
            if value not in (None, ""):
                status[index] = _as_text(value)
        return status
    raise TuikParseError("status must be a string, an array or an object")


def parse_jsonstat(doc: Mapping[str, Any] | str | bytes) -> Dataset:
    """Decode a JSON-stat 2.0 dataset into a :class:`Dataset`.

    Raises
    ------
    TuikParseError
        If the document is not a JSON-stat 2.0 *dataset* or its shape is
        inconsistent (size mismatch, non-contiguous category index, ...).
    """
    document = _load(doc)
    doc_class = document.get("class", "dataset")
    if doc_class not in ("dataset", None):
        raise TuikParseError(
            f"expected a JSON-stat dataset (class='dataset'), got class={doc_class!r}; "
            "collections and bundles are not supported"
        )

    raw_dimensions = document.get("dimension")
    if not isinstance(raw_dimensions, Mapping) or not raw_dimensions:
        raise TuikParseError("JSON-stat document has no 'dimension' object")

    ids = document.get("id")
    if ids is None:
        ids = list(raw_dimensions)
    if not isinstance(ids, list) or not ids:
        raise TuikParseError("'id' must be a non-empty array of dimension identifiers")
    ids = [_as_text(dim_id) for dim_id in ids]
    if len(set(ids)) != len(ids):
        raise TuikParseError("'id' contains duplicate dimension identifiers")

    roles = _role_map(document)
    dimensions: list[Dimension] = []
    for dim_id in ids:
        if dim_id not in raw_dimensions:
            raise TuikParseError(f"dimension {dim_id!r} is listed in 'id' but missing")
        raw_dimension = raw_dimensions[dim_id]
        if not isinstance(raw_dimension, Mapping):
            raise TuikParseError(f"dimension {dim_id!r} is not an object")
        dimensions.append(
            Dimension(
                id=dim_id,
                label=_as_text(raw_dimension.get("label", dim_id)),
                categories=_build_categories(raw_dimension, dim_id),
                role=roles.get(dim_id),
            )
        )

    derived_size = [len(dimension) for dimension in dimensions]
    raw_size = document.get("size")
    if raw_size is None:
        size = derived_size
    else:
        if not isinstance(raw_size, list) or len(raw_size) != len(dimensions):
            raise TuikParseError("'size' must be an array with one entry per dimension")
        size = []
        for position, value in enumerate(raw_size):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise TuikParseError(f"'size[{position}]' must be a non-negative integer")
            if value != derived_size[position]:
                raise TuikParseError(
                    f"'size[{position}]'={value} does not match "
                    f"{derived_size[position]} categories of {dimensions[position].id!r}"
                )
            size.append(value)

    total = math.prod(size) if size else 0

    raw_value = document.get("value")
    if raw_value is None:
        raw_values: list[Any] = []
    elif isinstance(raw_value, list):
        raw_values = raw_value
    elif isinstance(raw_value, Mapping):
        # Non-standard but seen in the wild: sparse {"12": 3.4} maps.
        raw_values = [None] * total
        for key, value in raw_value.items():
            try:
                index = int(key)
            except (TypeError, ValueError) as exc:
                raise TuikParseError(f"'value' key {key!r} is not a cell index") from exc
            if not 0 <= index < total:
                raise TuikParseError(f"'value' key {index} is outside the cube ({total} cells)")
            raw_values[index] = value
    else:
        raise TuikParseError("'value' must be an array (or a sparse object)")

    if len(raw_values) > total:
        raise TuikParseError(f"'value' has {len(raw_values)} cells but the cube holds {total}")

    coerced = 0
    values: list[float | int | None] = []
    for raw in raw_values:
        value = _coerce_number(raw)
        if value is None and raw is not None:
            coerced += 1
        values.append(value)
    # A short value array (truncated tail) is padded with None: never shifted.
    values.extend([None] * (total - len(values)))

    return Dataset(
        dimensions=tuple(dimensions),
        values=tuple(values),
        status=_status_map(document.get("status"), total),
        label=_as_text(document.get("label", "")),
        source=_as_text(document.get("source", "")),
        updated=_as_text(document.get("updated", "")),
        href=_as_text(document.get("href", "")),
        raw=document,
        coerced_missing=coerced,
    )


# ---------------------------------------------------------------------------
# SDMX-JSON 2.0
# ---------------------------------------------------------------------------
def parse_sdmx_json(doc: Mapping[str, Any] | str | bytes) -> Dataset:
    """Decode an SDMX-JSON 2.0 data message into a :class:`Dataset`.

    SDMX series keys are expanded into a dense tensor, so a series that the
    provider omitted (or truncated) shows up as ``None`` instead of shifting the
    following observations.
    """
    document = _load(doc)
    data = document.get("data", document)
    if not isinstance(data, Mapping):
        raise TuikParseError("SDMX-JSON message has no 'data' object")
    structure = data.get("structure", document.get("structure"))
    if not isinstance(structure, Mapping):
        raise TuikParseError("SDMX-JSON message has no 'structure' object")
    raw_dimensions = structure.get("dimensions")
    if not isinstance(raw_dimensions, Mapping):
        raise TuikParseError("SDMX-JSON 'structure' has no 'dimensions' object")

    dims: list[Dimension] = []
    for section in ("series", "observation"):
        entries = raw_dimensions.get(section)
        if entries is None:
            entries = []
        if not isinstance(entries, list):
            raise TuikParseError(f"SDMX-JSON 'dimensions.{section}' must be an array")
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise TuikParseError(f"SDMX-JSON dimension in {section!r} is not an object")
            dim_id = _as_text(entry.get("id", ""))
            values = entry.get("values")
            if values is None:
                values = []
            if not dim_id or not isinstance(values, list):
                raise TuikParseError(f"SDMX-JSON dimension {dim_id!r} has no values")
            category = tuple(
                Category(
                    code=_as_text(value.get("id", position)),
                    label=_as_text(value.get("name", value.get("id", position))),
                )
                for position, value in enumerate(values)
                if isinstance(value, Mapping)
            )
            if not category:
                raise TuikParseError(f"SDMX-JSON dimension {dim_id!r} has no values")
            dims.append(
                Dimension(
                    id=dim_id,
                    label=_as_text(entry.get("name", dim_id)),
                    categories=category,
                    role="time" if dim_id.upper() in ("TIME_PERIOD", "TIME") else None,
                )
            )
    if not dims:
        raise TuikParseError("SDMX-JSON message has no dimensions")

    size = [len(dimension) for dimension in dims]
    total = math.prod(size)
    cells: list[float | int | None] = [None] * total
    status: dict[int, str] = {}
    strides: list[int] = []
    acc = 1
    for dim_size in reversed(size):
        strides.append(acc)
        acc *= dim_size
    strides.reverse()

    datasets = data.get("dataSets")
    if datasets is None:
        datasets = []
    if not isinstance(datasets, list):
        raise TuikParseError("SDMX-JSON 'dataSets' must be an array")
    n_series_dims = len(raw_dimensions.get("series") or [])
    obs_values_dim = dims[n_series_dims] if len(dims) > n_series_dims else None

    for dataset in datasets:
        if not isinstance(dataset, Mapping):
            raise TuikParseError("SDMX-JSON dataSet is not an object")
        series_block = dataset.get("series")
        if series_block is not None and not isinstance(series_block, Mapping):
            raise TuikParseError("SDMX-JSON 'series' must be an object")
        if isinstance(series_block, Mapping):
            for key, series in series_block.items():
                if not isinstance(series, Mapping):
                    raise TuikParseError("SDMX-JSON series entry is not an object")
                series_coords = _parse_key(key, n_series_dims)
                observations = series.get("observations")
                if not isinstance(observations, Mapping):
                    raise TuikParseError("SDMX-JSON series has no 'observations' object")
                for obs_key, payload in observations.items():
                    obs_index = _as_index(obs_key, size[n_series_dims])
                    raw_value = payload[0] if isinstance(payload, list) and payload else payload
                    coords = [*series_coords, obs_index]
                    index = sum(
                        coord * stride for coord, stride in zip(coords, strides, strict=True)
                    )
                    cells[index] = _coerce_number(raw_value)
                    flag = _sdmx_observation_status(payload, structure)
                    if flag:
                        status[index] = flag
        else:
            # Flat observation block (no series dimensions).
            observations = dataset.get("observations")
            if not isinstance(observations, Mapping):
                raise TuikParseError("SDMX-JSON dataSet has no observations")
            for obs_key, payload in observations.items():
                obs_index = _as_index(obs_key, total)
                raw_value = payload[0] if isinstance(payload, list) and payload else payload
                cells[obs_index] = _coerce_number(raw_value)
                flag = _sdmx_observation_status(payload, structure)
                if flag:
                    status[obs_index] = flag

    if obs_values_dim is None:  # pragma: no cover - guarded by dims check
        raise TuikParseError("SDMX-JSON message has no observation dimension")

    return Dataset(
        dimensions=tuple(dims),
        values=tuple(cells),
        status=status,
        label=_as_text(document.get("label", "")),
        source="SDMX-JSON",
        updated=_as_text(document.get("meta", {}).get("updated", ""))
        if isinstance(document.get("meta"), Mapping)
        else "",
        raw=document,
    )


def _parse_key(key: str, expected: int) -> list[int]:
    if expected == 0:
        return []
    parts = key.split(":")
    if len(parts) != expected:
        raise TuikParseError(f"series key {key!r} has {len(parts)} parts, expected {expected}")
    try:
        return [int(part) for part in parts]
    except ValueError as exc:
        raise TuikParseError(f"series key {key!r} is not numeric") from exc


def _as_index(key: str, limit: int) -> int:
    try:
        index = int(key)
    except (TypeError, ValueError) as exc:
        raise TuikParseError(f"observation key {key!r} is not an integer") from exc
    if not 0 <= index < limit:
        raise TuikParseError(f"observation index {index} is outside the cube ({limit} cells)")
    return index


def _sdmx_observation_status(payload: Any, structure: Mapping[str, Any]) -> str:
    """Resolve the ``OBS_STATUS`` flag of an observation, if the message has one.

    In SDMX-JSON the attribute block carries *indices* into the
    ``structure.attributes.observation[*].values`` dictionaries.
    """
    if not isinstance(payload, list) or len(payload) < 2:
        return ""
    raw_attributes = payload[1]
    if not isinstance(raw_attributes, list):
        return ""
    attribute_defs = (structure.get("attributes") or {}).get("observation") or []
    for position, flag_index in enumerate(raw_attributes):
        if flag_index is None or position >= len(attribute_defs):
            continue
        definition = attribute_defs[position]
        if not isinstance(definition, Mapping):
            continue
        values = definition.get("values") or []
        if not isinstance(values, list) or not 0 <= int(flag_index) < len(values):
            continue
        value = values[int(flag_index)]
        if isinstance(value, Mapping):
            code = _as_text(value.get("id", ""))
            if code:
                return code
    return ""
