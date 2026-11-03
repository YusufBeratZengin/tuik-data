"""Tests for the JSON-stat 2.0 and SDMX-JSON 2.0 decoders."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.conftest import FIXTURES, load_dataset, load_document
from tuik_data.errors import TuikParseError
from tuik_data.parser import Dataset, parse_jsonstat, parse_sdmx_json


def minimal_document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "version": "2.0",
        "class": "dataset",
        "id": ["TIME_PERIOD", "CITIES"],
        "size": [2, 2],
        "dimension": {
            "TIME_PERIOD": {
                "label": "Dönem",
                "category": {
                    "index": {"2023": 0, "2024": 1},
                    "label": {"2023": "2023", "2024": "2024"},
                },
            },
            "CITIES": {
                "label": "İl",
                "category": {
                    "index": {"TR34": 0, "TR06": 1},
                    "label": {"TR34": "İstanbul", "TR06": "Ankara"},
                },
            },
        },
        "value": [1, 2, 3, 4],
    }
    document.update(overrides)
    return document


class TestJsonstatShape:
    def test_dense_dataset(self, tufe_dataset: Dataset) -> None:
        assert tufe_dataset.size == (3, 3)
        assert tufe_dataset.dimension_ids == ("TIME_PERIOD", "COICOP")
        assert len(tufe_dataset) == 9
        assert tufe_dataset.values[0] == 1000.0
        assert tufe_dataset.label.startswith("TÜFE")
        assert tufe_dataset.updated == "2025-02-03"

    def test_roles_are_mapped(self, tufe_dataset: Dataset) -> None:
        roles = {dimension.id: dimension.role for dimension in tufe_dataset.dimensions}
        assert roles == {"TIME_PERIOD": "time", "COICOP": "metric"}

    def test_categories_keep_order_and_unit(self, tufe_dataset: Dataset) -> None:
        coicop = tufe_dataset.dimensions[1]
        assert coicop.codes == ("01", "04", "07")
        assert coicop.categories[0].label == "Gıda ve Alkolsüz İçecekler"
        assert coicop.categories[0].unit == {"label": "Endeks", "decimals": 2}
        assert coicop.categories[1].unit is None

    def test_size_is_derived_when_absent(self) -> None:
        document = minimal_document()
        del document["size"]
        dataset = parse_jsonstat(document)
        assert dataset.size == (2, 2)

    def test_id_is_derived_from_dimension_order(self) -> None:
        document = minimal_document()
        del document["id"]
        dataset = parse_jsonstat(document)
        assert dataset.dimension_ids == ("TIME_PERIOD", "CITIES")

    def test_list_style_index(self) -> None:
        document = minimal_document()
        document["dimension"]["CITIES"]["category"] = {
            "index": ["TR34", "TR06"],
            "label": {"TR06": "Ankara"},
        }
        dataset = parse_jsonstat(document)
        assert dataset.dimensions[1].codes == ("TR34", "TR06")
        assert dataset.dimensions[1].categories[0].label == "TR34"

    def test_index_order_is_respected_not_insertion_order(self) -> None:
        document = minimal_document()
        document["dimension"]["CITIES"]["category"]["index"] = {"TR06": 1, "TR34": 0}
        dataset = parse_jsonstat(document)
        assert dataset.dimensions[1].codes == ("TR34", "TR06")

    def test_repr_is_informative(self, tufe_dataset: Dataset) -> None:
        text = repr(tufe_dataset)
        assert "Dataset" in text
        assert "cells=9" in text
        assert "missing=2" in text


class TestMissingValues:
    def test_null_and_masked_tokens_become_none(self, tufe_dataset: Dataset) -> None:
        assert tufe_dataset.values[2] is None
        assert tufe_dataset.values[6] is None
        assert tufe_dataset.missing == 2
        assert tufe_dataset.coerced_missing == 1

    def test_missing_cell_does_not_shift_neighbours(self, tufe_dataset: Dataset) -> None:
        assert tufe_dataset.values[3] == 1010.2
        assert tufe_dataset.values[7] == 1150.0

    def test_truncated_value_array_is_padded_not_shifted(self) -> None:
        document = minimal_document(value=[1, 2])
        dataset = parse_jsonstat(document)
        assert dataset.values == (1, 2, None, None)

    def test_sparse_value_object(self) -> None:
        document = minimal_document(value={"0": 7.5, "3": 9})
        dataset = parse_jsonstat(document)
        assert dataset.values == (7.5, None, None, 9)

    def test_numeric_strings_are_coerced(self) -> None:
        dataset = parse_jsonstat(minimal_document(value=["1.5", "2", "n/a", None]))
        assert dataset.values == (1.5, 2, None, None)

    def test_booleans_are_not_observations(self) -> None:
        dataset = parse_jsonstat(minimal_document(value=[True, False, 3, 4]))
        assert dataset.values[:2] == (None, None)


class TestStatus:
    def test_status_object(self, tufe_dataset: Dataset) -> None:
        assert tufe_dataset.status == {2: "p", 6: "c"}

    def test_status_array(self) -> None:
        dataset = parse_jsonstat(minimal_document(status=["p", None, "", "r"]))
        assert dataset.status == {0: "p", 3: "r"}

    def test_status_string_applies_to_every_cell(self) -> None:
        dataset = parse_jsonstat(minimal_document(status="p"))
        assert dataset.status == {0: "p", 1: "p", 2: "p", 3: "p"}

    def test_status_invalid_type(self) -> None:
        with pytest.raises(TuikParseError, match="status"):
            parse_jsonstat(minimal_document(status=42))

    def test_status_invalid_key(self) -> None:
        with pytest.raises(TuikParseError, match="status key"):
            parse_jsonstat(minimal_document(status={"abc": "p"}))


class TestFlatOutput:
    def test_rows_in_tensor_order(self, tufe_dataset: Dataset) -> None:
        rows = tufe_dataset.records()
        assert rows[0] == {"TIME_PERIOD": "2024-01", "COICOP": "01", "value": 1000.0, "status": ""}
        assert rows[2]["status"] == "p"
        assert rows[2]["value"] is None
        assert rows[3]["COICOP"] == "01"
        assert rows[3]["TIME_PERIOD"] == "2024-02"

    def test_label_modes(self, tufe_dataset: Dataset) -> None:
        labelled = tufe_dataset.records(labels="label")[0]
        assert labelled["COICOP"] == "Gıda ve Alkolsüz İçecekler"
        both = tufe_dataset.records(labels="both")[0]
        assert both["COICOP"] == "01 - Gıda ve Alkolsüz İçecekler"
        codes = tufe_dataset.records(labels="code")[0]
        assert codes["TIME_PERIOD"] == "2024-01"

    def test_unit_column(self, tufe_dataset: Dataset) -> None:
        rows = tufe_dataset.records(include_unit=True)
        assert rows[0]["unit"] == "Endeks"

    def test_csv_roundtrip(self, tufe_dataset: Dataset, tmp_path: Any) -> None:
        target = tmp_path / "tufe.csv"
        text = tufe_dataset.to_csv(target)
        assert target.read_text(encoding="utf-8") == text
        lines = text.splitlines()
        assert lines[0] == "TIME_PERIOD,COICOP,value,status"
        assert "Gıda" not in text  # codes by default
        assert len(lines) == 10

    def test_csv_with_labels(self, tufe_dataset: Dataset) -> None:
        text = tufe_dataset.to_csv(labels="label")
        assert "Gıda ve Alkolsüz İçecekler" in text
        assert text.splitlines()[1].endswith(",")  # null rendered as empty field

    def test_to_json(self, tufe_dataset: Dataset) -> None:
        payload = json.loads(tufe_dataset.to_json())
        assert payload[0]["value"] == 1000.0
        assert payload[2]["value"] is None

    def test_len_and_missing(self, tufe_dataset: Dataset) -> None:
        assert len(tufe_dataset) == 9
        assert tufe_dataset.missing == 2


class TestAddressing:
    def test_cell_by_codes(self, tufe_dataset: Dataset) -> None:
        assert tufe_dataset.cell(TIME_PERIOD="2024-02", COICOP="07") == 980.1

    def test_cell_accepts_labels(self, tufe_dataset: Dataset) -> None:
        assert tufe_dataset.cell(TIME_PERIOD="2024 Mart", COICOP="Ulaştırma") == 990.5

    def test_cell_wrong_dimensions(self, tufe_dataset: Dataset) -> None:
        with pytest.raises(KeyError, match="expected dimension codes"):
            tufe_dataset.cell(TIME_PERIOD="2024-01")

    def test_cell_unknown_code(self, tufe_dataset: Dataset) -> None:
        with pytest.raises(KeyError):
            tufe_dataset.cell(TIME_PERIOD="2024-01", COICOP="99")

    def test_index_bounds(self, tufe_dataset: Dataset) -> None:
        with pytest.raises(IndexError, match="outside dimension"):
            tufe_dataset._index([0, 9])

    def test_iter_cells(self, tufe_dataset: Dataset) -> None:
        cells = list(tufe_dataset.iter_cells())
        assert cells[0][0] == ("2024-01", "01")
        assert cells[-1][0] == ("2024-03", "07")


class TestSubsetting:
    def test_select_drops_fixed_dimension(self, tufe_dataset: Dataset) -> None:
        subset = tufe_dataset.select(COICOP="01")
        assert subset.dimension_ids == ("TIME_PERIOD",)
        assert subset.values == (1000.0, 1010.2, None)

    def test_select_keeps_dimension_when_asked(self, tufe_dataset: Dataset) -> None:
        subset = tufe_dataset.select(drop=False, COICOP="07")
        assert subset.size == (3, 1)
        assert subset.values == (None, 980.1, 990.5)

    def test_select_preserves_status(self, tufe_dataset: Dataset) -> None:
        subset = tufe_dataset.select(TIME_PERIOD="2024-01")
        assert subset.status == {2: "p"}
        assert subset.values == (1000.0, 1120.5, None)

    def test_select_unknown_dimension(self, tufe_dataset: Dataset) -> None:
        with pytest.raises(KeyError, match="unknown dimension"):
            tufe_dataset.select(NOPE="x")

    def test_take_last_periods(self, tufe_dataset: Dataset) -> None:
        subset = tufe_dataset.take("TIME_PERIOD", ["2024-02", "2024-03"])
        assert subset.size == (2, 3)
        assert subset.values == (1010.2, 1135.7, 980.1, None, 1150.0, 990.5)
        assert subset.status == {3: "c"}

    def test_take_unknown_code(self, tufe_dataset: Dataset) -> None:
        with pytest.raises(KeyError):
            tufe_dataset.take("TIME_PERIOD", ["2099-01"])


class TestJsonstatErrors:
    @pytest.mark.parametrize(
        ("document", "message"),
        [
            ("not json", "not valid JSON"),
            ([1, 2, 3], "expected a JSON object"),
            ({"class": "collection"}, "not supported"),
            ({"dimension": {}}, "no 'dimension' object"),
        ],
    )
    def test_bad_documents(self, document: Any, message: str) -> None:
        with pytest.raises(TuikParseError, match=message):
            parse_jsonstat(document)

    def test_invalid_utf8(self) -> None:
        with pytest.raises(TuikParseError, match="UTF-8"):
            parse_jsonstat(b"\xff\xfe\x00")

    def test_size_mismatch(self) -> None:
        document = minimal_document(size=[2, 3])
        with pytest.raises(TuikParseError, match="does not match"):
            parse_jsonstat(document)

    def test_size_not_array(self) -> None:
        with pytest.raises(TuikParseError, match="one entry per dimension"):
            parse_jsonstat(minimal_document(size="2,2"))

    def test_negative_size(self) -> None:
        with pytest.raises(TuikParseError, match="non-negative"):
            parse_jsonstat(minimal_document(size=[-1, 2]))

    def test_value_too_long(self) -> None:
        with pytest.raises(TuikParseError, match="holds 4"):
            parse_jsonstat(minimal_document(value=[1, 2, 3, 4, 5]))

    def test_value_wrong_type(self) -> None:
        with pytest.raises(TuikParseError, match="'value'"):
            parse_jsonstat(minimal_document(value=12))

    def test_sparse_value_out_of_range(self) -> None:
        with pytest.raises(TuikParseError, match="outside the cube"):
            parse_jsonstat(minimal_document(value={"9": 1}))

    def test_sparse_value_bad_key(self) -> None:
        with pytest.raises(TuikParseError, match="not a cell index"):
            parse_jsonstat(minimal_document(value={"x": 1}))

    def test_missing_dimension_listed_in_id(self) -> None:
        document = minimal_document()
        document["id"] = ["TIME_PERIOD", "MISSING"]
        with pytest.raises(TuikParseError, match="missing"):
            parse_jsonstat(document)

    def test_duplicate_dimension_ids(self) -> None:
        document = minimal_document()
        document["id"] = ["TIME_PERIOD", "TIME_PERIOD"]
        with pytest.raises(TuikParseError, match="duplicate"):
            parse_jsonstat(document)

    def test_id_not_array(self) -> None:
        with pytest.raises(TuikParseError, match="non-empty array"):
            parse_jsonstat(minimal_document(id="TIME_PERIOD"))

    def test_dimension_without_category(self) -> None:
        document = minimal_document()
        document["dimension"]["CITIES"] = {"label": "İl"}
        with pytest.raises(TuikParseError, match="no category"):
            parse_jsonstat(document)

    def test_category_without_index_or_label(self) -> None:
        document = minimal_document()
        document["dimension"]["CITIES"]["category"] = {}
        with pytest.raises(TuikParseError, match="neither index nor label"):
            parse_jsonstat(document)

    def test_non_contiguous_index(self) -> None:
        document = minimal_document()
        document["dimension"]["CITIES"]["category"]["index"] = {"TR34": 0, "TR06": 2}
        with pytest.raises(TuikParseError, match="contiguous"):
            parse_jsonstat(document)

    def test_negative_index_value(self) -> None:
        document = minimal_document()
        document["dimension"]["CITIES"]["category"]["index"] = {"TR34": 0, "TR06": -1}
        with pytest.raises(TuikParseError, match="non-integer"):
            parse_jsonstat(document)

    def test_label_map_wrong_type(self) -> None:
        document = minimal_document()
        document["dimension"]["CITIES"]["category"]["label"] = ["İstanbul"]
        with pytest.raises(TuikParseError, match="malformed"):
            parse_jsonstat(document)


class TestUtf8:
    def test_turkish_labels_survive_bytes_roundtrip(self, tufe_dataset: Dataset) -> None:
        raw = (FIXTURES / "tufe_example.json").read_bytes()
        decoded = parse_jsonstat(raw)
        assert decoded.dimensions[1].categories[0].label == "Gıda ve Alkolsüz İçecekler"
        assert decoded.dimensions[0].categories[2].label == "2024 Mart"
        text = decoded.to_csv(labels="both")
        assert "Konut, Su, Elektrik, Gaz ve Diğer Yakıtlar" in text
        assert text.encode("utf-8").decode("utf-8") == text


class TestSdmxJson:
    def test_series_are_expanded(self, sdmx_dataset: Dataset) -> None:
        assert sdmx_dataset.dimension_ids == ("CITIES", "SEX", "TIME_PERIOD")
        assert sdmx_dataset.size == (2, 1, 3)
        assert len(sdmx_dataset) == 6

    def test_values_and_sparse_series(self, sdmx_dataset: Dataset) -> None:
        assert sdmx_dataset.values[:3] == (100.0, 101.5, None)
        # Ankara published only one observation: the rest stay None, no shift.
        assert sdmx_dataset.values[3] == 50.0
        assert sdmx_dataset.values[4:] == (None, None)

    def test_observation_status_is_resolved(self, sdmx_dataset: Dataset) -> None:
        assert sdmx_dataset.status.get(2) == "P"
        assert sdmx_dataset.status.get(0) == "A"

    def test_time_role(self, sdmx_dataset: Dataset) -> None:
        assert sdmx_dataset.dimensions[2].role == "time"

    def test_flat_output_matches(self, sdmx_dataset: Dataset) -> None:
        rows = sdmx_dataset.records(labels="label")
        assert rows[0]["CITIES"] == "İstanbul"
        assert rows[0]["TIME_PERIOD"] == "2022"
        assert rows[1]["value"] == 101.5

    def test_missing_structure(self) -> None:
        with pytest.raises(TuikParseError, match="'structure'"):
            parse_sdmx_json({"data": {"dataSets": []}})

    def test_missing_dimensions(self) -> None:
        with pytest.raises(TuikParseError, match="'dimensions'"):
            parse_sdmx_json({"data": {"structure": {}}})

    def test_bad_series_key(self) -> None:
        document = load_document("adnks_example_sdmx.json")
        document["data"]["dataSets"][0]["series"]["0"] = {"observations": {}}
        with pytest.raises(TuikParseError, match="parts"):
            parse_sdmx_json(document)

    def test_non_numeric_series_key(self) -> None:
        document = load_document("adnks_example_sdmx.json")
        document["data"]["dataSets"][0]["series"]["a:0"] = {"observations": {}}
        with pytest.raises(TuikParseError, match="not numeric"):
            parse_sdmx_json(document)

    def test_observation_index_out_of_range(self) -> None:
        document = load_document("adnks_example_sdmx.json")
        document["data"]["dataSets"][0]["series"]["0:0"]["observations"]["9"] = [1, [0]]
        with pytest.raises(TuikParseError, match="outside the cube"):
            parse_sdmx_json(document)

    def test_data_sets_must_be_array(self) -> None:
        document = load_document("adnks_example_sdmx.json")
        document["data"]["dataSets"] = {}
        with pytest.raises(TuikParseError, match="dataSets"):
            parse_sdmx_json(document)

    def test_flat_observations_without_series(self) -> None:
        document = {
            "data": {
                "dataSets": [{"observations": {"0": [5.0, [0]], "1": [6.0, [0]]}}],
                "structure": {
                    "dimensions": {
                        "observation": [
                            {
                                "id": "TIME_PERIOD",
                                "name": "Dönem",
                                "values": [{"id": "2023"}, {"id": "2024"}],
                            }
                        ]
                    }
                },
            }
        }
        dataset = parse_sdmx_json(document)
        assert dataset.values == (5.0, 6.0)
        assert dataset.size == (2,)

    def test_structure_only_payload_has_empty_values(self) -> None:
        document = load_document("adnks_example_sdmx.json")
        document["data"]["dataSets"] = []
        dataset = parse_sdmx_json(document)
        assert len(dataset) == 6
        assert all(value is None for value in dataset.values)

    def test_dimension_without_values(self) -> None:
        document = load_document("adnks_example_sdmx.json")
        document["data"]["structure"]["dimensions"]["series"][0]["values"] = []
        with pytest.raises(TuikParseError, match="no values"):
            parse_sdmx_json(document)

    def test_label_falls_back_to_header_meta(self) -> None:
        dataset = load_dataset("adnks_example_sdmx.json")
        assert dataset.updated == "2025-02-03"


def test_polars_conversion_if_available() -> None:
    polars = pytest.importorskip("polars")
    dataset = load_dataset("tufe_example.json")
    frame = dataset.to_polars()
    assert isinstance(frame, polars.DataFrame)
    assert frame.height == 9
    assert frame.columns == ["TIME_PERIOD", "COICOP", "value"]
