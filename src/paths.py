from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

SOURCE_FILES = sorted(ROOT.glob("Report AR_AP_*.xlsx"))

# locations_master.csv stays a plain file: it is hand-edited in Excel to fix geocoding
# (see src/geocode.py). Everything else the pipeline hands between stages
# (trips_raw, trips_parsed, trips_clean, rate_table, model_summary) plus the two API
# result caches now live in data/pricing.db - see src/db.py.
LOCATIONS = DATA / "locations_master.csv"

# EPPO HSD B7 diesel reference price cache - see scripts/fetch_eppo_diesel.py
EPPO_DIESEL = DATA / "eppo_diesel_hsd_b7.json"
