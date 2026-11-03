"""CLI tests: output formats, file writing, error codes and exit statuses."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import FakeTransport, json_response, load_document, make_client
from tuik_data import TuikClient
from tuik_data._http import HttpResponse
from tuik_data.cli import main, render_table
from tuik_data.storage import SeriesStore

FIXTURE = Path(__file__).parent / "fixtures" / "tufe_example.json"


def fixture_client(**kwargs: object) -> tuple[TuikClient, FakeTransport]:
    transport = FakeTransport()
    transport.routes["DF_TUFE_SDMX_TT01"] = json_response(load_document("tufe_example.json"))
    transport.routes["DF_ADNKS_T30"] = HttpResponse(404, b"not found")
    return make_client(transport, **kwargs), transport


class TestHelpAndVersion:
    def test_version(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            main(["--version"])
        assert excinfo.value.code == 0
        assert "0.1.0" in capsys.readouterr().out

    def test_no_command_prints_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main([]) == 0
        assert "usage: tuik-data" in capsys.readouterr().out


class TestIndicators:
    def test_table(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["indicators"]) == 0
        out = capsys.readouterr().out
        assert "tufe_genel" in out
        assert "DF_TUFE_SDMX_TT01" in out
        assert "code" in out.splitlines()[0]

    def test_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["indicators", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        codes = [item["code"] for item in payload]
        assert "yi_ufe" in codes
        assert codes[0] == "tufe_genel"  # catalogue display order
        assert len(codes) == len(set(codes))


class TestSearch:
    def test_match(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["search", "enflasyon"]) == 0
        assert "tufe_genel" in capsys.readouterr().out

    def test_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["search", "nüfus", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload[0]["code"].startswith("nufus")

    def test_no_match_exits_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["search", "zzzz"]) == 2
        assert "no indicator matched" in capsys.readouterr().err


class TestFetch:
    def test_table_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        client, _ = fixture_client()
        assert main(["fetch", "tufe_genel"], client=client) == 0
        out = capsys.readouterr().out
        assert "TIME_PERIOD" in out
        assert "2024-01" in out
        assert "1000.0" in out

    def test_json_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        client, _ = fixture_client()
        assert main(["fetch", "tufe_genel", "--format", "json"], client=client) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["rows"][0]["value"] == 1000.0
        assert payload["dimensions"] == ["TIME_PERIOD", "COICOP"]

    def test_csv_to_stdout(self, capsys: pytest.CaptureFixture[str]) -> None:
        client, _ = fixture_client()
        assert main(["fetch", "tufe_genel", "--format", "csv"], client=client) == 0
        out = capsys.readouterr().out
        assert out.splitlines()[0] == "TIME_PERIOD,COICOP,value,status"

    def test_csv_to_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        client, _ = fixture_client()
        target = tmp_path / "out.csv"
        assert (
            main(["fetch", "tufe_genel", "--format", "csv", "--out", str(target)], client=client)
            == 0
        )
        assert target.read_text(encoding="utf-8").startswith("TIME_PERIOD")
        assert "wrote" in capsys.readouterr().out

    def test_labels_and_last_n(self, capsys: pytest.CaptureFixture[str]) -> None:
        client, _ = fixture_client()
        assert (
            main(
                ["fetch", "tufe_genel", "--labels", "both", "--son-12-ay", "--format", "csv"],
                client=client,
            )
            == 0
        )
        out = capsys.readouterr().out
        assert "01 - Gıda ve Alkolsüz İçecekler" in out
        assert out.count("\n", 0, out.index("\n", out.index("\n") + 1) + 1) >= 1

    def test_key_filter_and_dataset_override(self, capsys: pytest.CaptureFixture[str]) -> None:
        transport = FakeTransport()
        transport.routes["detail=nodata"] = json_response(
            {
                "data": {
                    "dataSets": [],
                    "structure": {
                        "dimensions": {
                            "series": [
                                {
                                    "id": "CITIES",
                                    "name": "İl",
                                    "values": [{"id": "TR34", "name": "İstanbul"}],
                                }
                            ],
                            "observation": [
                                {
                                    "id": "TIME_PERIOD",
                                    "name": "Dönem",
                                    "values": [{"id": "2024", "name": "2024"}],
                                }
                            ],
                        }
                    },
                }
            }
        )
        transport.routes["DF_FAKE"] = json_response(load_document("tufe_example.json"))
        client = make_client(transport)
        assert (
            main(
                ["fetch", "irrelevant", "--dataset", "DF_FAKE", "--key", "CITIES=TR34"],
                client=client,
            )
            == 0
        )
        assert "2024-01" in capsys.readouterr().out

    def test_malformed_key(self, capsys: pytest.CaptureFixture[str]) -> None:
        client, _ = fixture_client()
        assert main(["fetch", "tufe_genel", "--key", "CITIES"], client=client) == 2
        assert "DIM=CODE" in capsys.readouterr().err

    def test_unknown_indicator(self, capsys: pytest.CaptureFixture[str]) -> None:
        client, _ = fixture_client()
        assert main(["fetch", "nope"], client=client) == 2
        assert "unknown indicator" in capsys.readouterr().err

    def test_not_found_surfaces_as_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        client, _ = fixture_client()
        assert main(["fetch", "nufus_il"], client=client) == 2
        assert "404" in capsys.readouterr().err


class TestDims:
    def test_lists_dimensions(self, capsys: pytest.CaptureFixture[str]) -> None:
        transport = FakeTransport()
        transport.routes["detail=nodata"] = json_response(load_document("adnks_example_sdmx.json"))
        client = make_client(transport)
        assert main(["dims", "nufus_il"], client=client) == 0
        out = capsys.readouterr().out
        assert "CITIES" in out
        assert "İstanbul" in out

    def test_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        transport = FakeTransport()
        transport.routes["detail=nodata"] = json_response(load_document("adnks_example_sdmx.json"))
        client = make_client(transport)
        assert main(["dims", "nufus_il", "--json"], client=client) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload[0]["id"] == "CITIES"
        assert payload[0]["codes"][0] == {"code": "TR34", "label": "İstanbul"}

    def test_raw_dataflow_id(self, capsys: pytest.CaptureFixture[str]) -> None:
        transport = FakeTransport()
        transport.routes["detail=nodata"] = json_response(load_document("adnks_example_sdmx.json"))
        client = make_client(transport)
        assert main(["dims", "TR,DF_X,1.0"], client=client) == 0
        assert "CITIES" in capsys.readouterr().out

    def test_unknown_name(self, capsys: pytest.CaptureFixture[str]) -> None:
        client, _ = fixture_client()
        assert main(["dims", "bogus"], client=client) == 2
        assert "unknown indicator" in capsys.readouterr().err


class TestFlatten:
    def test_table(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["flatten", str(FIXTURE)]) == 0
        assert "2024-01" in capsys.readouterr().out

    def test_csv_out(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        target = tmp_path / "flat.csv"
        assert main(["flatten", str(FIXTURE), "--format", "csv", "--out", str(target)]) == 0
        assert "Gıda" not in target.read_text(encoding="utf-8")
        assert "wrote" in capsys.readouterr().out

    def test_missing_file(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["flatten", "nope.json"]) == 2
        assert "error:" in capsys.readouterr().err


class TestSaveAndMcp:
    def test_save(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        client, _ = fixture_client()
        store = tmp_path / "series.sqlite3"
        assert main(["save", "tufe_genel", "--store", str(store)], client=client) == 0
        assert "saved tufe_genel" in capsys.readouterr().out
        with SeriesStore(store) as opened:
            assert opened.info("tufe_genel").rows == 9

    def test_mcp_delegates_to_server(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        called: list[bool] = []

        def fake_serve() -> int:
            called.append(True)
            return 0

        import tuik_data.mcp_server as mcp

        monkeypatch.setattr(mcp, "serve", fake_serve)
        assert main(["mcp"]) == 0
        assert called == [True]


class TestRendering:
    def test_render_table_aligns_and_truncates(self) -> None:
        rows = [{"a": "x", "b": "y" * 60}, {"a": "long", "b": "z"}]
        text = render_table(rows, ["a", "b"], max_width=10)
        lines = text.splitlines()
        assert lines[0].startswith("a ")
        assert len(lines) == 4
        assert lines[1].startswith("---")
        assert "…" in lines[2]

    def test_render_empty(self) -> None:
        assert render_table([], ["a"]) == "(no rows)"

    def test_null_values_render_as_empty(self) -> None:
        text = render_table([{"a": None}], ["a"])
        assert text.splitlines()[2].strip() == ""
