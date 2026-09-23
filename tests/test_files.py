import pandas as pd
import pytest

from src.files import DataFileError, read_csv, require_columns


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
