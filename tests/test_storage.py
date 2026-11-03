"""Local SQLite store tests: round-trips, unicode, missing cells and safety rails."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tuik_data.errors import TuikConfigError, TuikError
from tuik_data.parser import Dataset, parse_jsonstat
from tuik_data.storage import SeriesStore, default_store_path


@pytest.fixture()
def store(tmp_path: Path) -> Iterator[SeriesStore]:
    with SeriesStore(tmp_path / "series.sqlite3") as opened:
        yield opened


class TestRoundTrip:
    def test_save_and_load(self, store: SeriesStore, tufe_dataset: Dataset) -> None:
        store.save("tufe", tufe_dataset, indicator="tufe_genel")
        assert store.names() == ["tufe"]
        info = store.info("tufe")
        assert info.indicator == "tufe_genel"
        assert info.rows == 9
        assert info.dimensions == ("TIME_PERIOD", "COICOP")
        rows = store.frame("tufe")
        assert rows[0] == {"TIME_PERIOD": "2024-01", "COICOP": "01", "value": 1000.0, "status": ""}
        assert rows[2]["value"] is None
        assert rows[2]["status"] == "p"

    def test_unicode_labels_survive(self, store: SeriesStore, tufe_dataset: Dataset) -> None:
        store.save("tufe", tufe_dataset)
        assert store.info("tufe").label.startswith("TÜFE")

    def test_unicode_category_codes_roundtrip(self, store: SeriesStore) -> None:
        dataset = parse_jsonstat(
            {
                "version": "2.0",
                "class": "dataset",
                "id": ["GRUP"],
                "size": [2],
                "dimension": {
                    "GRUP": {
                        "label": "Harcama grubu",
                        "category": {
                            "index": {"Gıda": 0, "Ulaştırma": 1},
                            "label": {"Gıda": "Gıda ve Alkolsüz İçecekler"},
                        },
                    }
                },
                "value": [1.5, None],
                "status": {"1": "c"},
            }
        )
        store.save("unicode", dataset)
        rows = store.frame("unicode")
        assert rows[0]["GRUP"] == "Gıda"
        assert rows[1]["GRUP"] == "Ulaştırma"
        assert rows[1]["value"] is None
        assert rows[1]["status"] == "c"

    def test_resave_replaces_series(self, store: SeriesStore, tufe_dataset: Dataset) -> None:
        store.save("tufe", tufe_dataset)
        store.save("tufe", tufe_dataset.select(TIME_PERIOD="2024-01"))
        assert store.info("tufe").rows == 3

    def test_save_rejects_empty_name(self, store: SeriesStore, tufe_dataset: Dataset) -> None:
        with pytest.raises(TuikConfigError, match="must not be empty"):
            store.save("  ", tufe_dataset)

    def test_missing_series_is_explained(self, store: SeriesStore) -> None:
        with pytest.raises(TuikError, match="no stored series"):
            store.info("nope")

    def test_delete(self, store: SeriesStore, tufe_dataset: Dataset) -> None:
        store.save("tufe", tufe_dataset)
        assert store.delete("tufe") is True
        assert store.delete("tufe") is False
        assert store.names() == []

    def test_data_survives_reopen(self, tmp_path: Path, tufe_dataset: Dataset) -> None:
        path = tmp_path / "nested" / "series.sqlite3"
        with SeriesStore(path) as first:
            first.save("tufe", tufe_dataset)
        with SeriesStore(path) as second:
            assert second.info("tufe").rows == 9

    def test_in_memory_store(self, tufe_dataset: Dataset) -> None:
        with SeriesStore(":memory:") as memory:
            memory.save("x", tufe_dataset)
            assert memory.info("x").rows == 9


class TestQuery:
    def test_select_query(self, store: SeriesStore, tufe_dataset: Dataset) -> None:
        store.save("tufe", tufe_dataset)
        rows = store.query("SELECT coords, value FROM observations WHERE value > ?", (1000,))
        assert len(rows) >= 1
        assert all(row["value"] > 1000 for row in rows)

    def test_write_queries_are_refused(self, store: SeriesStore) -> None:
        with pytest.raises(TuikConfigError, match="only SELECT"):
            store.query("DELETE FROM observations")
        with pytest.raises(TuikConfigError, match="only SELECT"):
            store.query("   ")

    def test_cte_prefixed_write_is_refused(self, store: SeriesStore, tufe_dataset: Dataset) -> None:
        store.save("tufe", tufe_dataset)
        with pytest.raises(TuikConfigError, match="only SELECT"):
            store.query("WITH x AS (SELECT 1) DELETE FROM series")
        assert store.names() == ["tufe"]

    def test_nested_select_is_allowed(self, store: SeriesStore, tufe_dataset: Dataset) -> None:
        store.save("tufe", tufe_dataset)
        rows = store.query("SELECT * FROM (SELECT name FROM series)")
        assert rows[0]["name"] == "tufe"


def test_default_store_path_uses_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TUIK_STORE", str(tmp_path / "custom.sqlite3"))
    assert default_store_path() == tmp_path / "custom.sqlite3"
    monkeypatch.delenv("TUIK_STORE")
    assert default_store_path().name == "series.sqlite3"
