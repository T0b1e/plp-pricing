import pandas as pd
import pytest

from src.files import DataFileError, read_csv, read_json, require_columns, write_json


def test_json_roundtrip_leaves_no_temp_file(tmp_path):
    p = tmp_path / "cache.json"
    write_json(p, {"a|b": {"km": 12.5}}, indent=0)
    assert read_json(p) == {"a|b": {"km": 12.5}}
    assert list(tmp_path.iterdir()) == [p]


def test_missing_json_is_empty_cache(tmp_path):
    assert read_json(tmp_path / "nope.json") == {}


def test_corrupt_json_is_an_error_not_an_empty_cache(tmp_path):
    p = tmp_path / "cache.json"
    p.write_text('{"a": {"km": 1', encoding="utf-8")   # a run killed mid-write, old style
    with pytest.raises(DataFileError, match="corrupt"):
        read_json(p)


def test_csv_saved_as_ansi_by_excel(tmp_path):
    p = tmp_path / "locations_master.csv"
    p.write_bytes("token,alias_of\nห้องเย็น,\n".encode("cp874"))
    with pytest.raises(DataFileError, match="UTF-8"):
        read_csv(p, dtype=str)


def test_missing_csv(tmp_path):
    with pytest.raises(DataFileError, match="not found"):
        read_csv(tmp_path / "trips_clean.csv")


def test_require_columns():
    with pytest.raises(DataFileError, match="alias_of"):
        require_columns(pd.DataFrame(columns=["token"]), ["token", "alias_of"], "x.csv")
