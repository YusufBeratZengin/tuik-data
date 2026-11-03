# Contributing to tuik-data

Thanks for helping make Turkish official statistics easier to use from Python.
This project is small on purpose: standard library at runtime, one job per module,
tests for every behaviour.

## Quick start

```bash
git clone https://github.com/YusufBeratZengin/tuik-data
cd tuik-data
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pip install -e .
bash scripts/check.sh
```

`scripts/check.sh` runs ruff (lint + format check), `mypy --strict`, the whole test
suite and the 85 % coverage gate. It exits non-zero if anything fails; please run
it before opening a pull request. Nothing in the default test run touches the
network.

## Ground rules

* **No invented statistics.** Never hard-code an official value, rate, tax figure
  or legal statement. If a fact is needed and unverified, mark it `NEEDS_SOURCE`
  in the issue/PR and stop.
* **No secrets.** API keys come from `TUIK_API_KEY` only. Fixtures must be
  obviously synthetic (`"example"`, `"fake"`, `xxxxxx`) and say so in the file.
* **Runtime dependencies stay at zero.** A new dependency needs a written
  justification in the PR (size, maintenance, security). Optional extras are fine.
* **Turkish text is UTF-8, always.** Labels go through `ensure_ascii=False`; CSV is
  written as UTF-8 with LF endings.
* **Missing cells stay `None`.** Never drop or reorder values to "clean" a cube.

## Adding an indicator

1. Find the dataflow id in the TÜİK Data Portal SDMX catalogue
   (<https://veriportali.tuik.gov.tr>) — it looks like `TR,DF_...,1.0`.
2. Add an `Indicator(...)` entry to `CATALOGUE` in `src/tuik_data/catalogues.py`
   with Turkish and English titles, frequency, theme and search aliases.
3. Add a test in `tests/test_catalogue.py` (top search hit) and, if the shape is
   unusual, a small synthetic fixture in `tests/fixtures/`.
4. Do not add values, only identifiers. Users override changed ids with
   `TUIK_DATASET_<CODE>`.

## Tests

* `pytest` — everything, network tests skipped.
* `pytest --cov=tuik_data --cov-report=term-missing` — coverage.
* `TUIK_LIVE=1 TUIK_API_KEY=xxxxxx pytest tests/test_network.py` — optional live
  smoke test with your own key; never commit its output.
* Prefer one behaviour per test and assert on messages users actually see.
* New code needs a test that fails without the change.

## Style

* `ruff check` and `ruff format` must be clean (`line-length = 100`).
* `mypy --strict` must pass; public functions have type hints and docstrings.
* Keep public API changes backward compatible within a minor version; note anything
  user-visible in `CHANGELOG.md` under *Unreleased*.

## Commit and PR

* Small commits, imperative English subject (`fix: keep null cells in place`).
* A PR should say what changed, why, and how it was verified (command + output).
* CI must be green on Linux and Windows.

## Reporting bugs

Open an issue with the template. Include the command you ran, the Python version,
and — for parsing problems — a minimal JSON snippet with the numbers replaced by
`0` (we only need the structure, never the data).
