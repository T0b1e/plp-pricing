"""SQLite storage for the pipeline's tables and the two API-result caches.

Tables produced by a pipeline stage (trips_raw, trips_parsed, locations_master,
trips_clean, rate_table, model_summary) are fully rewritten every run: replace_table
drops and reloads the whole table, the same "always regenerated" semantics the old
CSV/parquet files had - the transform code in each src/*.py module is unchanged, only
the final read/write call is.

distance_cache and geocode_cache are different: they hold paid-for API results that
accumulate across runs, so they get real primary-key tables with row-level upserts
(upsert_distance) instead of whole-file rewrites. That is what lets a single Streamlit
click write just its own key instead of racing another user over the whole cache, the
way the old distance_cache.json load-modify-save-whole-file pattern could.
"""
import json
import sqlite3
from contextlib import contextmanager

import pandas as pd

from .files import DataFileError
from .paths import DATA

DB_PATH = DATA / "pricing.db"


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("CREATE TABLE IF NOT EXISTS distance_cache "
                     "(key TEXT PRIMARY KEY, km REAL, minutes REAL, error TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS geocode_cache "
                     "(query TEXT PRIMARY KEY, value TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS eppo_diesel "
                     "(date TEXT PRIMARY KEY, year INTEGER, month TEXT, day INTEGER, "
                     "item TEXT, country TEXT, unit TEXT, price REAL, no_bias REAL)")
        yield conn
        conn.commit()
    finally:
        conn.close()


def table_exists(name: str) -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return row is not None


def replace_table(df: pd.DataFrame, name: str, json_cols: list[str] | None = None):
    """Drop and reload `name` with `df` - the whole-file-rewrite semantics the CSV/parquet had.

    json_cols lists columns holding Python lists (e.g. trips_parsed's `stops`) - sqlite has no
    array type, so those are JSON-encoded going in and decoded again by read_table.
    """
    if json_cols:
        df = df.copy()
        for c in json_cols:
            df[c] = df[c].map(json.dumps)
    try:
        with _connect() as conn:
            df.to_sql(name, conn, if_exists="replace", index=False)
    except sqlite3.OperationalError as e:
        raise DataFileError(f"Cannot write table '{name}' to {DB_PATH.name}: {e}") from None


def read_table(name: str, json_cols: list[str] | None = None) -> pd.DataFrame:
    with _connect() as conn:
        try:
            df = pd.read_sql(f"SELECT * FROM {name}", conn)
        except pd.errors.DatabaseError:
            raise DataFileError(f"table '{name}' not found in {DB_PATH.name} - run the step that builds "
                                f"it first (see `python main.py --help`).") from None
    if json_cols:
        for c in json_cols:
            df[c] = df[c].map(json.loads)
    return df


def load_distance_cache() -> dict:
    with _connect() as conn:
        rows = conn.execute("SELECT key, km, minutes, error FROM distance_cache").fetchall()
    cache = {}
    for key, km, minutes, error in rows:
        cache[key] = {"km": km, "error": error} if km is None else {"km": km, "minutes": minutes}
    return cache


def upsert_distance(key: str, value: dict):
    """Write one leg's result without touching any other row - fixes the old load-whole-cache,
    save-whole-cache race between simultaneous Streamlit users."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO distance_cache (key, km, minutes, error) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET km=excluded.km, minutes=excluded.minutes, error=excluded.error",
            (key, value.get("km"), value.get("minutes"), value.get("error")))


def save_distance_cache(cache: dict):
    """Bulk upsert, for the batch pipeline's periodic checkpoint writes."""
    rows = [(k, v.get("km"), v.get("minutes"), v.get("error")) for k, v in cache.items()]
    with _connect() as conn:
        conn.executemany(
            "INSERT INTO distance_cache (key, km, minutes, error) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET km=excluded.km, minutes=excluded.minutes, error=excluded.error",
            rows)


def load_eppo_diesel() -> dict:
    """date (YYYY-MM-DD) -> record, for every day pulled so far."""
    cols = ["date", "year", "month", "day", "item", "country", "unit", "price", "no_bias"]
    with _connect() as conn:
        rows = conn.execute(f"SELECT {', '.join(cols)} FROM eppo_diesel").fetchall()
    return {r[0]: dict(zip(cols, r)) for r in rows}


def save_eppo_diesel(days: dict):
    """Bulk upsert - one row per date, keyed on `date` so a rerun just overwrites what changed."""
    cols = ["date", "year", "month", "day", "item", "country", "unit", "price", "no_bias"]
    rows = [tuple(v.get(c) for c in cols) for v in days.values()]
    placeholders = ", ".join("?" * len(cols))
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "date")
    with _connect() as conn:
        conn.executemany(
            f"INSERT INTO eppo_diesel ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(date) DO UPDATE SET {updates}", rows)


def load_geocode_cache() -> dict:
    with _connect() as conn:
        rows = conn.execute("SELECT query, value FROM geocode_cache").fetchall()
    return {q: json.loads(v) for q, v in rows}


def save_geocode_cache(cache: dict):
    rows = [(q, json.dumps(v, ensure_ascii=False)) for q, v in cache.items()]
    with _connect() as conn:
        conn.executemany(
            "INSERT INTO geocode_cache (query, value) VALUES (?, ?) "
            "ON CONFLICT(query) DO UPDATE SET value=excluded.value",
            rows)
