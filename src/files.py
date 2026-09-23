"""Reading and writing the files in data/, with errors a person can act on.

The CSVs are meant to be edited by hand in Excel, which brings two common failures:
the file is still open (Windows locks it, so writing fails) and it gets re-saved in
the local ANSI code page instead of UTF-8. The JSON caches hold paid API results, so
they are written atomically: an interrupted run can never leave half a file behind.
"""
import json
import os
import time
from pathlib import Path

import pandas as pd


class DataFileError(RuntimeError):
    """A data file is missing, locked or unreadable; the message says what to do."""


def read_csv(path: Path, **kw) -> pd.DataFrame:
    if not path.exists():
        raise DataFileError(f"{path.name} not found in {path.parent} - run the step that builds it first "
                            f"(see `python main.py --help`).")
    try:
        return pd.read_csv(path, encoding="utf-8-sig", **kw)
    except UnicodeDecodeError:
        raise DataFileError(f"{path.name} is not UTF-8. In Excel use Save As -> "
                            f"'CSV UTF-8 (Comma delimited)', then rerun.") from None
    except PermissionError:
        raise DataFileError(f"{path.name} is locked - close it in Excel and rerun.") from None


def require_columns(df: pd.DataFrame, cols, name: str):
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise DataFileError(f"{name} is missing column(s): {', '.join(missing)}")


def write_csv(df: pd.DataFrame, path: Path, **kw):
    try:
        df.to_csv(path, encoding="utf-8-sig", **kw)
    except PermissionError:
        raise DataFileError(f"Cannot write {path.name} - it is probably open in Excel. "
                            f"Close it and rerun this step.") from None


def read_json(path: Path) -> dict:
    """Missing file -> {}. A corrupt file is an error, never silently {} (that would re-pay the API)."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise DataFileError(f"{path.name} is corrupt ({e}). Restore it from a backup, or move it away "
                            f"to start an empty cache (every lookup will be paid again).") from None


def write_json(path: Path, obj, **kw):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, **kw), encoding="utf-8")
    for attempt in range(5):   # another process (the web UI, a virus scanner) may hold it for a moment
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                raise DataFileError(f"Cannot replace {path.name} - another program is holding it. "
                                    f"The new data is in {tmp.name}.") from None
            time.sleep(0.2 * (attempt + 1))
