"""Ready-made indicator codes for the TÜİK SDMX service.

Each :class:`Indicator` pins a *dataflow* (the SDMX "table") plus the labels a
human searches for.  Dataflow identifiers are the ones published by the TÜİK Data
Portal SDMX catalogue (``veriportali.tuik.gov.tr``); the portal changes them
rarely but it does change them, so every code can be overridden at runtime with
``TUIK_DATASET_<CODE>`` (for example ``TUIK_DATASET_TUFE_GENEL=TR,DF_...,1.2``)
without touching this file.

The catalogue stores *identifiers*, never values: tuik-data does not ship any
statistics and never guesses one.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from .errors import TuikConfigError

__all__ = [
    "CATALOGUE",
    "CATALOGUE_SOURCE",
    "Indicator",
    "enrich_period_bounds",
    "get_indicator",
    "list_indicators",
    "normalise",
    "search",
]

CATALOGUE_SOURCE = "https://veriportali.tuik.gov.tr/tr/sdmx-web-service-documentation"
ENV_PREFIX = "TUIK_DATASET_"

_TR_FOLD = str.maketrans(
    {
        "İ": "i",
        "I": "i",
        "ı": "i",
        "Ş": "s",
        "ş": "s",
        "Ğ": "g",
        "ğ": "g",
        "Ç": "c",
        "ç": "c",
        "Ü": "u",
        "ü": "u",
        "Ö": "o",
        "ö": "o",
        "Â": "a",
        "â": "a",
    }
)


def normalise(text: str) -> str:
    """Fold Turkish case and diacritics so ``TUFE`` matches ``TÜFE``."""
    return " ".join(text.translate(_TR_FOLD).casefold().split())


@dataclass(frozen=True)
class Indicator:
    """One curated dataflow with bilingual labels and search aliases."""

    code: str
    title_tr: str
    title_en: str
    dataflow: str
    theme: str
    frequency: str
    aliases: tuple[str, ...] = ()
    notes_tr: str = ""
    docs: str = CATALOGUE_SOURCE
    extra: Mapping[str, str] = field(default_factory=dict)

    @property
    def env_var(self) -> str:
        return f"{ENV_PREFIX}{self.code.upper()}"

    def resolve_dataflow(self, environ: Mapping[str, str] | None = None) -> str:
        """Return the dataflow id, honouring a ``TUIK_DATASET_<CODE>`` override."""
        env = os.environ if environ is None else environ
        override = (env.get(self.env_var) or "").strip()
        return override or self.dataflow

    def haystack(self) -> str:
        return normalise(
            " ".join([self.code, self.title_tr, self.title_en, self.theme, *self.aliases])
        )


CATALOGUE: tuple[Indicator, ...] = (
    Indicator(
        code="tufe_genel",
        title_tr="Tüketici Fiyat Endeksi (TÜFE) - ana harcama grupları",
        title_en="Consumer Price Index (CPI) by main expenditure groups",
        dataflow="TR,DF_TUFE_SDMX_TT01,1.0",
        theme="Fiyat İstatistikleri",
        frequency="monthly",
        aliases=("enflasyon", "tufe", "cpi", "inflation", "fiyat endeksi", "harcama grubu"),
        notes_tr=(
            "Ana harcama gruplarına göre endeks ve değişim oranları. Serinin "
            "yayımlanmış değerlerini döndürür; oran hesabı yapmaz."
        ),
    ),
    Indicator(
        code="tufe_katki",
        title_tr="TÜFE - ana harcama gruplarının genel endekse katkısı",
        title_en="CPI - contribution of main expenditure groups",
        dataflow="TR,DF_TUFE_SDMX_TT03,1.0",
        theme="Fiyat İstatistikleri",
        frequency="monthly",
        aliases=("katki", "contribution", "enflasyon katkisi", "cpi contribution"),
        notes_tr="Aylık ve yıllık değişim oranları ile genel endekse katkılar.",
    ),
    Indicator(
        code="tufe_sepet",
        title_tr="TÜFE - mal ve hizmet sepeti ve ağırlıkları",
        title_en="CPI - basket of goods and services and weights",
        dataflow="TR,DF_TUFE_SDMX_TT04,1.0",
        theme="Fiyat İstatistikleri",
        frequency="annual",
        aliases=("sepet", "agirlik", "basket", "weights"),
        notes_tr="Sepet kalemleri ve ağırlıkları; endeks değeri içermez.",
    ),
    Indicator(
        code="yi_ufe",
        title_tr="Yurt İçi Üretici Fiyat Endeksi (Yİ-ÜFE)",
        title_en="Domestic Producer Price Index (PPI)",
        dataflow="TR,DF_YIUFE_EDO_V1,1.0",
        theme="Fiyat İstatistikleri",
        frequency="monthly",
        aliases=("ufe", "yi-ufe", "ppi", "ppi", "uretici fiyat", "producer price"),
        notes_tr="Yurt içi üretici fiyat endeksi ve değişim oranları (2003=100).",
    ),
    Indicator(
        code="nufus_il",
        title_tr="ADNKS - illere göre nüfus",
        title_en="Population of provinces by years (ADNKS)",
        dataflow="TR,DF_ADNKS_T30,1.1",
        theme="Nüfus ve Demografi",
        frequency="annual",
        aliases=("nufus", "population", "adnks", "il nufusu", "sehir nufusu"),
        notes_tr="Adrese Dayalı Nüfus Kayıt Sistemi, il düzeyinde yıllık nüfus.",
    ),
    Indicator(
        code="nufus_il_yas_cinsiyet",
        title_tr="ADNKS - il, yaş grubu ve cinsiyete göre nüfus",
        title_en="Population by province, age group and sex",
        dataflow="TR,DF_ADNKS_T16,1.0",
        theme="Nüfus ve Demografi",
        frequency="annual",
        aliases=("yas", "cinsiyet", "age", "sex", "gender", "nufus piramidi"),
        notes_tr="İl, yaş grubu ve cinsiyet kırılımında nüfus.",
    ),
    Indicator(
        code="issizlik",
        title_tr="İşgücü - temel göstergeler (aylık)",
        title_en="Main labour force indicators (monthly)",
        dataflow="TR,DF_ISGUCU_AYLIK_TEMEL_ISGUCU_C,1.0",
        theme="İşgücü",
        frequency="monthly",
        aliases=("issizlik", "unemployment", "isgucu", "labour force", "istihdam", "employment"),
        notes_tr=(
            "15+ ve 15-24 yaş için nüfus, işgücü, istihdam, işsizlik oranı vb. "
            "Gösterge seçimi boyut filtresiyle yapılır."
        ),
    ),
    Indicator(
        code="ihracat_birim_deger",
        title_tr="İhracat birim değer endeksi",
        title_en="Export unit value index",
        dataflow="TR,DF_IHRACAT_BIRIM_DEGER_V1,1.0",
        theme="Dış Ticaret",
        frequency="monthly",
        aliases=("ihracat", "export", "dis ticaret", "foreign trade", "birim deger"),
        notes_tr="Dış ticaret fiyat endekslerinden ihracat birim değer endeksi.",
    ),
)

_BY_CODE: dict[str, Indicator] = {indicator.code: indicator for indicator in CATALOGUE}


def list_indicators() -> tuple[Indicator, ...]:
    """Return the catalogue in display order."""
    return CATALOGUE


def get_indicator(code: str) -> Indicator:
    """Look up an indicator by code (case-insensitive).

    Raises
    ------
    TuikConfigError
        If the code is unknown, with close matches in the message.
    """
    key = normalise(code).replace(" ", "_")
    if key in _BY_CODE:
        return _BY_CODE[key]
    folded = {normalise(name): indicator for name, indicator in _BY_CODE.items()}
    if key in folded:
        return folded[key]
    import difflib

    close = difflib.get_close_matches(key, list(_BY_CODE), n=3, cutoff=0.5)
    hint = f" Did you mean: {', '.join(close)}?" if close else ""
    raise TuikConfigError(
        f"unknown indicator {code!r}.{hint} Run `tuik-data indicators` for the full list."
    )


def search(query: str, indicators: Iterable[Indicator] | None = None) -> list[Indicator]:
    """Rank catalogue entries against ``query`` (Turkish or English, case-insensitive).

    Scoring: exact code 100, code prefix 60, title/alias substring 40, all query
    tokens present 20.  ``query=""`` returns everything.
    """
    pool = list(indicators if indicators is not None else CATALOGUE)
    needle = normalise(query)
    if not needle:
        return pool
    tokens = needle.split()
    scored: list[tuple[int, int, Indicator]] = []
    for position, indicator in enumerate(pool):
        haystack = indicator.haystack()
        code = normalise(indicator.code)
        score = 0
        if needle == code:
            score = 100
        elif code.startswith(needle):
            score = 60
        elif needle in haystack:
            score = 40
        elif all(token in haystack for token in tokens):
            score = 20
        if score:
            scored.append((-score, position, indicator))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [indicator for _, _, indicator in scored]


def enrich_period_bounds(start_year: int | None, end_year: int | None) -> dict[str, str | None]:
    """Translate year bounds into SDMX ``startPeriod``/``endPeriod`` values."""
    if start_year is not None and end_year is not None and end_year < start_year:
        raise TuikConfigError(f"end_year ({end_year}) is before start_year ({start_year})")
    if start_year is not None and start_year < 1900:
        raise TuikConfigError(f"start_year={start_year} looks wrong; use a four-digit year")
    return {
        "startPeriod": str(start_year) if start_year is not None else None,
        "endPeriod": str(end_year) if end_year is not None else None,
    }
