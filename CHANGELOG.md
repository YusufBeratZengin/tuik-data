# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-03

First release: TÜFE, Yİ-ÜFE and ADNKS through the TÜİK SDMX service, plus the
extras that make the client usable offline.

### Added

- `tuik_data.parser`: JSON-stat 2.0 decoder (`parse_jsonstat`) and SDMX-JSON 2.0
  decoder (`parse_sdmx_json`) sharing one dense `Dataset` model — dimension order,
  roles, category labels and units, per-cell `status`, UTF-8 labels, `None` for
  `null`/masked/truncated cells without index shifting.
- `Dataset` output helpers: `rows()`, `records()`, `to_csv()`, `to_json()`,
  `to_polars()` (optional extra), `cell()`, `select()`, `take()`.
- `tuik_data.client.TuikClient`: SDMX 2.1 REST queries (`get_dataset`,
  `get_indicator`, `get_tufe_annual`, `dimensions`), dataflow id normalisation,
  dimension-filter expansion with actionable errors listing valid codes, retries
  with exponential backoff, on-disk response cache, injected transport.
- `tuik_data.auth.TokenManager`: API key → bearer token exchange against the TÜİK
  login realm, in-memory cache, refresh 30 s before expiry, one automatic retry on
  HTTP 401, key redaction.
- `tuik_data.catalogues`: eight curated indicators (CPI, CPI contributions, CPI
  basket, PPI, ADNKS population, ADNKS by age/sex, labour force, export unit value)
  with Turkish/English labels, aliases, search with Turkish case folding and
  `TUIK_DATASET_<CODE>` overrides.
- `tuik_data.storage.SeriesStore`: SQLite long-form store, read-only SQL queries,
  offline reads.
- `tuik_data.mcp_server`: stdio JSON-RPC 2.0 MCP server with four tools, no
  third-party SDK.
- `tuik-data` CLI: `fetch` (table/json/csv, `--son-12-ay`, `--key`, `--dataset`),
  `indicators`, `search`, `dims`, `flatten`, `save`, `mcp`.
- Tests: 236 offline tests (93 %+ branch coverage) including error paths, UTF-8,
  retries, token renewal, cache expiry and MCP framing; a live smoke test gated by
  `TUIK_LIVE=1`.
- `scripts/check.sh` gate (ruff lint + format, mypy strict, pytest + coverage),
  GitHub Actions CI on Linux and Windows, Dependabot, issue/PR templates.

### Notes

- No official figure is stored in this repository; every value comes from TÜİK at
  request time. Dataflow identifiers are catalogue entries and can be overridden.
- Live endpoints require a personal SMS-verified API key and are not exercised by
  the default test run.

[0.1.0]: https://github.com/YusufBeratZengin/tuik-data/releases/tag/v0.1.0
