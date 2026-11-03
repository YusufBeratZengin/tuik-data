"""Catalogue tests: identifiers, search folding and period helpers."""

from __future__ import annotations

from typing import Any

import pytest

from tuik_data import catalogues
from tuik_data.catalogues import (
    CATALOGUE,
    enrich_period_bounds,
    get_indicator,
    list_indicators,
    normalise,
    search,
)
from tuik_data.errors import TuikConfigError


class TestCatalogueShape:
    def test_codes_are_unique(self) -> None:
        codes = [indicator.code for indicator in CATALOGUE]
        assert len(codes) == len(set(codes))

    def test_entries_are_complete(self) -> None:
        for indicator in CATALOGUE:
            assert indicator.dataflow.startswith("TR,DF_")
            assert indicator.dataflow.count(",") == 2
            assert indicator.title_tr and indicator.title_en
            assert indicator.frequency in ("monthly", "quarterly", "annual")

    def test_v01_scope_is_covered(self) -> None:
        codes = {indicator.code for indicator in CATALOGUE}
        assert {"tufe_genel", "yi_ufe", "nufus_il"} <= codes

    def test_list_indicators_matches_catalogue(self) -> None:
        assert list_indicators() == CATALOGUE

    def test_indicator_from_package_root(self) -> None:
        import tuik_data

        assert tuik_data.list_indicators() == CATALOGUE


class TestLookup:
    def test_exact(self) -> None:
        assert get_indicator("tufe_genel").code == "tufe_genel"

    def test_case_insensitive(self) -> None:
        assert get_indicator("TUFE_GENEL").code == "tufe_genel"
        assert get_indicator(" Tufe_Genel ").code == "tufe_genel"

    def test_unknown_suggests(self) -> None:
        with pytest.raises(TuikConfigError, match="Did you mean"):
            get_indicator("tufe_genel_typo")

    def test_unknown_without_close_match(self) -> None:
        with pytest.raises(TuikConfigError, match="indicators"):
            get_indicator("zzzzzz")

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        indicator = get_indicator("tufe_genel")
        assert indicator.env_var == "TUIK_DATASET_TUFE_GENEL"
        assert indicator.resolve_dataflow({}) == indicator.dataflow
        assert indicator.resolve_dataflow({"TUIK_DATASET_TUFE_GENEL": "TR,DF_X,2.0"}) == (
            "TR,DF_X,2.0"
        )


class TestSearch:
    def test_turkish_folding(self) -> None:
        assert normalise("TÜFE İşgücü") == normalise("tufe isgucu")

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("enflasyon", "tufe_genel"),
            ("TUFE", "tufe_genel"),
            ("nüfus", "nufus_il"),
            ("unemployment", "issizlik"),
            ("ihracat", "ihracat_birim_deger"),
            ("yi-ufe", "yi_ufe"),
        ],
    )
    def test_top_hit(self, query: str, expected: str) -> None:
        assert search(query)[0].code == expected

    def test_empty_query_returns_everything(self) -> None:
        assert search("") == list(CATALOGUE)

    def test_no_match(self) -> None:
        assert search("kuantum") == []

    def test_multi_token_query(self) -> None:
        codes = [indicator.code for indicator in search("tufe sepet")]
        assert "tufe_sepet" in codes

    def test_scoring_prefers_exact_code(self) -> None:
        assert search("tufe")[0].code == "tufe_genel"


class TestPeriodBounds:
    def test_both_bounds(self) -> None:
        assert enrich_period_bounds(2023, 2024) == {
            "startPeriod": "2023",
            "endPeriod": "2024",
        }

    def test_open_ended(self) -> None:
        assert enrich_period_bounds(None, None) == {"startPeriod": None, "endPeriod": None}

    def test_reversed_years(self) -> None:
        with pytest.raises(TuikConfigError, match="before start_year"):
            enrich_period_bounds(2024, 2023)

    def test_implausible_year(self) -> None:
        with pytest.raises(TuikConfigError, match="four-digit"):
            enrich_period_bounds(12, None)


def test_catalogue_source_is_documented() -> None:
    assert catalogues.CATALOGUE_SOURCE.startswith("https://veriportali.tuik.gov.tr")


def test_haystack_contains_aliases() -> None:
    payload: dict[str, Any] = {}
    indicator = get_indicator("tufe_genel")
    assert "enflasyon" in indicator.haystack()
    assert payload == {}
