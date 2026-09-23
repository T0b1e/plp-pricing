"""One-time import of the old CSV/parquet/JSON files into data/pricing.db.

Run once, after pulling the SQLite-backed pipeline code, so the tables and caches are
populated without re-running (and re-paying for) the geocode/distance API steps:

    python -m scripts.migrate_to_sqlite

The source files (trips_raw.parquet, trips_parsed.parquet, trips_clean.csv,
rate_table.csv, model_summary.csv, distance_cache.json, geocode_cache.json,
eppo_diesel_hsd_b7.json) are left untouched - data/pricing.db becomes the new source of
truth going forward, but nothing here is deleted. locations_master.csv is not migrated:
it stays a plain file, hand-edited in Excel to fix geocoding (see src/geocode.py).
"""
import json

import pandas as pd

from src import db
from src.paths import DATA

PARQUET_TABLES = {
    "trips_raw": (DATA / "trips_raw.parquet", None),
    "trips_parsed": (DATA / "trips_parsed.parquet", ["stops", "conditions", "non_place"]),
}
CSV_TABLES = {
    "trips_clean": DATA / "trips_clean.csv",
    "rate_table": DATA / "rate_table.csv",
    "model_summary": DATA / "model_summary.csv",
}
JSON_CACHES = {
    "distance_cache.json": db.save_distance_cache,
    "geocode_cache.json": db.save_geocode_cache,
    "eppo_diesel_hsd_b7.json": db.save_eppo_diesel,
}


def migrate_parquet():
    for table, (path, json_cols) in PARQUET_TABLES.items():
        if not path.exists():
            print(f"  (skip) {path.name} not found")
            continue
        df = pd.read_parquet(path)
        if json_cols:
            for c in json_cols:
                df[c] = df[c].map(list)   # parquet list columns come back as numpy arrays
        db.replace_table(df, table, json_cols=json_cols)
        print(f"  {path.name} -> {table} table ({len(df):,} rows)")


def migrate_csv():
    for table, path in CSV_TABLES.items():
        if not path.exists():
            print(f"  (skip) {path.name} not found")
            continue
        dtype = {"job_order_no": str} if "job_order_no" in pd.read_csv(path, encoding="utf-8-sig", nrows=0).columns else None
        df = pd.read_csv(path, encoding="utf-8-sig", dtype=dtype)
        db.replace_table(df, table)
        print(f"  {path.name} -> {table} table ({len(df):,} rows)")


def migrate_json_caches():
    for filename, save in JSON_CACHES.items():
        path = DATA / filename
        if not path.exists():
            print(f"  (skip) {filename} not found")
            continue
        cache = json.loads(path.read_text(encoding="utf-8"))
        save(cache)
        print(f"  {filename} -> {len(cache):,} cached entries")


def main():
    print("[migrate] parquet tables:")
    migrate_parquet()
    print("[migrate] csv tables:")
    migrate_csv()
    print("[migrate] json caches:")
    migrate_json_caches()
    print(f"\n-> {db.DB_PATH}")


if __name__ == "__main__":
    main()
