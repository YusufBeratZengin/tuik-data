"""Smoke test against the live TÜİK SDMX service.

Skipped by default.  To run it you need your own SMS-verified API key::

    TUIK_LIVE=1 TUIK_API_KEY=xxxxxx python -m pytest tests/test_network.py -v

The test asserts protocol behaviour (HTTP status, decoding, UTF-8 labels), never
a specific statistic: this project does not encode official values.
"""

from __future__ import annotations

import os

import pytest

from tuik_data import TuikClient
from tuik_data.errors import TuikAuthError, TuikError

pytestmark = pytest.mark.network


def live_enabled() -> bool:
    return os.environ.get("TUIK_LIVE") == "1" and bool(os.environ.get("TUIK_API_KEY"))


@pytest.fixture()
def client() -> TuikClient:
    if not live_enabled():
        pytest.skip("set TUIK_LIVE=1 and TUIK_API_KEY to run live tests")
    return TuikClient(timeout=60.0)


def test_missing_key_is_reported_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.environ.get("TUIK_LIVE") != "1":
        pytest.skip("live tests disabled")
    monkeypatch.delenv("TUIK_API_KEY", raising=False)
    with pytest.raises(TuikError):
        TuikClient(timeout=30.0).get_dataset("TR,DF_TUFE_SDMX_TT01,1.0")


def test_auth_failure_is_explicit() -> None:
    if os.environ.get("TUIK_LIVE") != "1":
        pytest.skip("live tests disabled")
    with pytest.raises(TuikAuthError):
        TuikClient(api_key="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", timeout=30.0).get_dataset(
            "TR,DF_TUFE_SDMX_TT01,1.0"
        )


def test_tufe_last_year(client: TuikClient) -> None:
    dataset = client.get_indicator("tufe_genel", last_n=12)
    assert len(dataset) > 0
    assert any(value is not None for value in dataset.values)
    assert dataset.dimensions
    rows = dataset.records(labels="both")
    assert rows


def test_dimensions_are_listed(client: TuikClient) -> None:
    dimensions = client.dimensions("TR,DF_TUFE_SDMX_TT01,1.0")
    assert dimensions
    for dimension in dimensions:
        assert dimension.categories
