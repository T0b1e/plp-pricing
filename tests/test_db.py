import pandas as pd
import pytest

from src import db
from src.files import DataFileError


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")


def test_table_roundtrip():
    df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    db.replace_table(df, "widgets")
    out = db.read_table("widgets")
    assert out.to_dict("records") == [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]


def test_replace_table_drops_old_rows():
    db.replace_table(pd.DataFrame({"a": [1, 2, 3]}), "widgets")
    db.replace_table(pd.DataFrame({"a": [9]}), "widgets")
    assert db.read_table("widgets")["a"].tolist() == [9]


def test_read_missing_table_raises_data_file_error():
    with pytest.raises(DataFileError, match="not found"):
        db.read_table("nope")


def test_table_exists():
    assert not db.table_exists("widgets")
    db.replace_table(pd.DataFrame({"a": [1]}), "widgets")
    assert db.table_exists("widgets")


def test_json_cols_roundtrip():
    df = pd.DataFrame({"stops": [["A", "B"], ["C"]]})
    db.replace_table(df, "trips", json_cols=["stops"])
    out = db.read_table("trips", json_cols=["stops"])
    assert out["stops"].tolist() == [["A", "B"], ["C"]]


def test_distance_cache_empty_is_empty_dict():
    assert db.load_distance_cache() == {}


def test_upsert_distance_writes_one_key_without_touching_others():
    db.upsert_distance("a|b", {"km": 12.5, "minutes": 20.0})
    db.upsert_distance("c|d", {"km": None, "error": "no_route"})
    cache = db.load_distance_cache()
    assert cache == {"a|b": {"km": 12.5, "minutes": 20.0}, "c|d": {"km": None, "error": "no_route"}}

    db.upsert_distance("a|b", {"km": 13.0, "minutes": 21.0})   # overwrite, same key
    cache = db.load_distance_cache()
    assert cache["a|b"] == {"km": 13.0, "minutes": 21.0}
    assert cache["c|d"] == {"km": None, "error": "no_route"}   # untouched


def test_save_distance_cache_is_a_bulk_upsert():
    db.upsert_distance("a|b", {"km": 1.0, "minutes": 2.0})
    db.save_distance_cache({"a|b": {"km": 5.0, "minutes": 6.0}, "e|f": {"km": 7.0, "minutes": 8.0}})
    cache = db.load_distance_cache()
    assert cache == {"a|b": {"km": 5.0, "minutes": 6.0}, "e|f": {"km": 7.0, "minutes": 8.0}}


def test_geocode_cache_roundtrip_with_thai_text():
    entry = {"places": {"places": [{"displayName": {"text": "ห้องเย็นแปซิฟิค"}}]}}
    db.save_geocode_cache({"query 1": entry})
    assert db.load_geocode_cache() == {"query 1": entry}
