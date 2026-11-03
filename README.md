# tuik-data

[![CI](https://github.com/YusufBeratZengin/tuik-data/actions/workflows/ci.yml/badge.svg)](https://github.com/YusufBeratZengin/tuik-data/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Runtime dependencies: 0](https://img.shields.io/badge/runtime%20deps-0-brightgreen.svg)](#dependencies)

**Zero-dependency Python client & CLI for Turkish Statistical Institute (TÜİK) Open Data & SDMX APIs.**

TÜİK moved its data portal to SDMX 2.1 / JSON-stat 2.0, where every table is a
multi-dimensional cube: dimensions, codelists, roles, masked cells and a flat
value array whose order you have to get exactly right. `tuik-data` turns those
cubes into plain rows (and CSV / Polars), and handles the API-key → bearer-token
dance for you — with the standard library only.

```python
from tuik_data import TuikClient

client = TuikClient()                       # reads TUIK_API_KEY
cpi = client.get_indicator("tufe_genel", last_n=12)
print(cpi.to_csv(labels="both"))
```

No pandas, no requests, no lxml. Python 3.10+.

---

## Install

```bash
pip install tuik-data
```

Optional extras:

```bash
pip install "tuik-data[polars]"   # Dataset.to_polars()
```

## 30-second example (no API key needed)

Any JSON-stat 2.0 or SDMX-JSON 2.0 file you download from the Data Portal can be
flattened offline. `examples/tufe_example.json` ships with the repo and contains
**synthetic** numbers (it says so inside the file):

```console
$ tuik-data flatten examples/tufe_example.json
TIME_PERIOD  COICOP  value   status
-----------  ------  ------  ------
2024-01      01      1000.0
2024-01      04      1120.5
2024-01      07              p
2024-02      01      1010.2
2024-02      04      1135.7
2024-02      07      980.1
2024-03      01              c
2024-03      04      1150.0
2024-03      07      990.5
```

The same file through the library (real output of `python examples/offline_quickstart.py`):

```console
$ python examples/offline_quickstart.py
<Dataset 'TÜFE - ana harcama grupları (örnek fixture, resmî veri değildir)' dims=['TIME_PERIOD', 'COICOP'] cells=9 missing=2>

TIME_PERIOD,COICOP,value,status
{'TIME_PERIOD': '2024-01 - 2024 Ocak', 'COICOP': '04 - Konut, Su, Elektrik, Gaz ve Diğer Yakıtlar', 'value': 1120.5, 'status': ''}

last period only:
  2024-03 - 2024 Mart 01 - Gıda ve Alkolsüz İçecekler                         None  status='c'
  2024-03 - 2024 Mart 04 - Konut, Su, Elektrik, Gaz ve Diğer Yakıtlar         1150.0  status=''
  2024-03 - 2024 Mart 07 - Ulaştırma                                          990.5  status=''
```

Missing cells are `None` (`p` = provisional, `c` = confidential in the fixture),
and they never shift their neighbours — that is the whole point.

## Live data (API key)

TÜİK protects `nsiws.tuik.gov.tr` with a short-lived bearer token that you obtain
once as an **SMS-verified API key** from <https://veriportali.tuik.gov.tr>
(*User Information → API Key*). Export it; it is never written to disk by this
library and never appears in error messages:

```bash
export TUIK_API_KEY=xxxxxx
```

```python
from tuik_data import TuikClient

with TuikClient() as client:
    cpi = client.get_indicator("tufe_genel", start_year=2024)
    print(cpi.records(labels="both")[:3])

    # Annual convenience wrapper: the observations TÜİK published for that year.
    # It does NOT compute an average or a year-on-year rate.
    print(client.get_tufe_annual(2025).to_csv("tufe-2025.csv", labels="both"))
```

## CLI

```console
$ tuik-data fetch tufe_genel --son-12-ay --format table
$ tuik-data fetch tufe_genel --start-year 2024 --format csv --out tufe.csv
$ tuik-data fetch nufus_il --key CITIES=TR34 --labels both
$ tuik-data dims nufus_il                 # what can I filter on?
$ tuik-data indicators                    # catalogue
$ tuik-data search "işgücü"               # offline search, Turkish or English
$ tuik-data flatten ~/Downloads/tufe.json --format csv --out tufe.csv
$ tuik-data save tufe_genel               # keep it in a local SQLite store
$ tuik-data mcp                           # MCP stdio server
```

`--son-12-ay` (also `--last-12`) keeps the newest 12 periods; `--last-n N` is the
generic form. Exit codes: `0` success, `2` TÜİK/config/parse error.

## What "flattening" means here

A JSON-stat 2.0 dataset is a dense array plus metadata. This library:

| Concern | Behaviour |
| --- | --- |
| Dimension order | Follows `id`; the last dimension varies fastest (spec-compliant). |
| Codes vs labels | `labels="code" \| "label" \| "both"` (`"01 - Gıda ve Alkolsüz İçecekler"`). |
| Missing cells | `null`, `""`, `".."`, `"."`, `":"`, `"-"`, `"n/a"` → `None`; never shifted. |
| Masked cells | Provider flag kept per cell in `Dataset.status` (`p`, `c`, ...). |
| Truncated arrays | Padded with `None` to the declared cube size. |
| Roles | `time` / `geo` / `metric` preserved on each `Dimension`. |
| SDMX-JSON 2.0 | Series keys expanded into the same dense model (sparse series stay `None`). |
| Turkish text | Read as UTF-8, written as UTF-8 CSV (`Ölçü`, `Gıda`, `İstanbul` intact). |

`Dataset` API in one screen:

```python
dataset.size                 # (3, 3)
dataset.dimension_ids        # ('TIME_PERIOD', 'COICOP')
dataset.cell(TIME_PERIOD="2024-02", COICOP="07")
dataset.records(labels="both")           # list[dict]
dataset.select(COICOP="01")              # sub-cube, fixed dims dropped
dataset.take("TIME_PERIOD", ["2024-03"]) # just one period
dataset.to_csv("out.csv", labels="label")  # UTF-8, LF
dataset.to_polars()                      # optional extra
dataset.raw                              # the decoded document, untouched
```

## Extras beyond v0.1

* **Token lifecycle** (`tuik_data.auth`): exchanges the API key for a bearer token,
  caches it in memory, renews it 30 s before expiry, retries once on HTTP 401.
* **Offline store** (`tuik_data.storage`): SQLite (`sqlite3`, stdlib) keeps series
  long-form so reports still run without a network or a key.
* **Polars output**: `Dataset.to_polars()` — no pandas needed.
* **MCP server** (`tuik_data.mcp_server`): stdio JSON-RPC 2.0 with
  `tuik_get_indicator`, `tuik_list_indicators`, `tuik_search`,
  `tuik_describe_indicator`. Wire it up as
  `{"command": "tuik-data", "args": ["mcp"]}`.

## Indicators in the catalogue

| Code | Indicator | Frequency |
| --- | --- | --- |
| `tufe_genel` | CPI, main expenditure groups | monthly |
| `tufe_katki` | CPI, contribution of groups | monthly |
| `tufe_sepet` | CPI basket and weights | annual |
| `yi_ufe` | Domestic producer price index | monthly |
| `nufus_il` | ADNKS population by province | annual |
| `nufus_il_yas_cinsiyet` | ADNKS population by province, age, sex | annual |
| `issizlik` | Main labour force indicators | monthly |
| `ihracat_birim_deger` | Export unit value index | monthly |

Override any dataflow id without editing code (the portal does rename tables):

```bash
export TUIK_DATASET_TUFE_GENEL="TR,DF_TUFE_SDMX_TT01,1.1"
```

## Dependencies

Runtime: **none**. The package uses `urllib`, `json`, `sqlite3`, `csv`, `gzip`.
Justifications, so nobody has to guess:

* `urllib` instead of `requests`/`httpx`: HTTPS with gzip and typed errors is ~80
  lines; a dependency would dominate the install size of a small client.
* `sqlite3` instead of DuckDB: stdlib, no compiler, one file, plenty fast for
  series of tens of thousands of rows. DuckDB users can export CSV and load it.
* `polars` and the `mcp` SDK are deliberately optional; the MCP server is
  implemented over stdio JSON-RPC with the standard library.

Dev-only: `pytest`, `pytest-cov`, `ruff`, `mypy` (`requirements-dev.txt`).

## Honest limitations

* **Official values are not encoded anywhere in this repo.** All numbers come from
  TÜİK at request time; in case of dispute the Official Gazette and TÜİK bulletins
  prevail. `get_tufe_annual()` selects published observations, it does not
  calculate a rate.
* **Dataflow ids** in `catalogues.py` are taken from the TÜİK Data Portal SDMX
  catalogue and can change without notice (`NEEDS_SOURCE` for any specific value
  claim — the library only stores identifiers). Override them with
  `TUIK_DATASET_<CODE>`.
* **Live endpoints are not exercised by the test suite** (they need a personal,
  SMS-verified key). Every network code path is tested with an injected transport
  and a local HTTP server; `tests/test_network.py` runs only with
  `TUIK_LIVE=1 TUIK_API_KEY=...`.
* **Bulk/whole-cube queries can be large.** Filter by period and dimension, or use
  `last_n`; the MCP tool truncates at 500 rows and says so.
* **Only `dataset`-class JSON-stat is accepted.** Collections and bundles raise a
  clear `TuikParseError`.
* **api_key storage:** the key lives in your environment only. The token cache is
  in memory; the response cache is plain JSON under `~/.cache/tuik-data`.

## Development

```bash
python -m pip install -r requirements-dev.txt
bash scripts/check.sh          # lint + format + mypy --strict + tests + coverage >= 85%
```

Coverage is 93 %+ with branch coverage; tests include error paths, UTF-8, masked
cells, retries, token renewal, caching and MCP framing. Network tests are skipped
by default.

## License

MIT © 2026 YusufBeratZengin. This project is not affiliated with TÜİK.
