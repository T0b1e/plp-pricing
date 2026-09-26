from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
EXCEL = ROOT / "excel"
DATA.mkdir(exist_ok=True)

SOURCE_FILES = sorted(EXCEL.glob("Report AR_AP_*.xlsx"))

TRIPS_RAW = DATA / "trips_raw.parquet"
TRIPS_PARSED = DATA / "trips_parsed.parquet"
LOCATIONS = DATA / "locations_master.csv"
GEOCODE_CACHE = DATA / "geocode_cache.json"
DISTANCE_CACHE = DATA / "distance_cache.json"
TRIPS_CLEAN = DATA / "trips_clean.csv"
RATE_TABLE = DATA / "rate_table.csv"
MODEL_SUMMARY = DATA / "model_summary.csv"

# EPPO HSD B7 diesel reference price cache - see scripts/fetch_eppo_diesel.py
EPPO_DIESEL = DATA / "eppo_diesel_hsd_b7.json"
