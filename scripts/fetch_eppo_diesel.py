"""Pull HSD B7 diesel retail prices from EPPO's open-data API into the eppo_diesel table.

Uses the "domestic and international oil prices" resource, which carries a
real Year + Month + Date(day) field (Thailand HSD B7 is reported roughly
weekly), so records get a true calendar date (YYYY-MM-DD) instead of always
defaulting to the 1st of the month. The reported Price(Baht) is a single
official figure per reading - not a MIN/WT.AVG/MAX band - so it's stored
as-is under "no_bias", meant as a ground-truth reference to compare against
the banded fuel_rate_low/fuel_rate_high pulled from contract text in
src/parse_route.py.

Run it yourself, e.g.:
    python -m scripts.fetch_eppo_diesel
    python -m scripts.fetch_eppo_diesel --start-year 2015 --sleep 2
    python -m scripts.fetch_eppo_diesel --force   # refetch years already saved

Safe to interrupt: each year is upserted into data/pricing.db as soon as it's
fetched, so progress is never lost and a rerun only re-fetches missing years.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from src import db

API_BASE = "https://catalog.eppo.go.th/api/3/action/datastore_search"
RESOURCE_ID = "7d56918d-adbf-42b7-bd36-e4b33d425027"
COUNTRY = "TH-THAILAND"
ITEM = "1052-HSD (B7)"


def fetch_year(year: int, sleep_s: float, retries: int = 4) -> list[dict]:
    filters = json.dumps({"Country": COUNTRY, "Item": ITEM, "Year": year})
    params = {
        "resource_id": RESOURCE_ID,
        "filters": filters,
        "limit": 1000,
    }
    url = f"{API_BASE}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; eppo-fetch/1.0)"})

    delay = sleep_s
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.load(resp)
            if not payload.get("success"):
                raise RuntimeError(f"API returned success=false for year {year}: {payload}")
            return payload["result"]["records"]
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            if attempt == retries:
                raise
            print(f"  year {year}: attempt {attempt} failed ({exc}); retrying in {delay:.1f}s",
                  file=sys.stderr)
            time.sleep(delay)
            delay *= 2
    return []


def records_to_days(records: list[dict]) -> dict[str, dict]:
    by_day: dict[str, dict] = {}
    for r in records:
        parsed = datetime.strptime(f"{r['Year']} {r['Month']} {r['Date']}", "%Y %B %d")
        date = parsed.strftime("%Y-%m-%d")
        by_day[date] = {
            "date": date,
            "year": r["Year"],
            "month": r["Month"],
            "day": r["Date"],
            "item": r["Item"],
            "country": r["Country"],
            "unit": r.get("UNIT", "").strip(),
            "price": r["Price(Baht)"],
            "no_bias": r["Price(Baht)"],
        }
    return by_day


def year_is_fetched(data: dict, year: int) -> bool:
    return any(v["year"] == year for v in data.values())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start-year", type=int, default=2012)
    parser.add_argument("--end-year", type=int, default=datetime.now(timezone.utc).year)
    parser.add_argument("--sleep", type=float, default=1.5, help="seconds to sleep between API calls")
    parser.add_argument("--force", action="store_true", help="refetch years already saved")
    args = parser.parse_args()

    data = db.load_eppo_diesel()
    print(f"Loaded {len(data)} existing day records from {db.DB_PATH.name}")

    years = range(args.start_year, args.end_year + 1)
    for year in years:
        if not args.force and year_is_fetched(data, year):
            print(f"{year}: already fetched, skipping")
            continue

        print(f"{year}: fetching...")
        try:
            records = fetch_year(year, args.sleep)
        except Exception as exc:
            print(f"{year}: FAILED after retries ({exc}); stopping so progress isn't lost", file=sys.stderr)
            break

        days = records_to_days(records)
        data.update(days)
        db.save_eppo_diesel(days)
        print(f"{year}: saved {len(days)} day(s), {len(data)} total")

        time.sleep(args.sleep)

    print(f"Done. {len(data)} day records in {db.DB_PATH}")


if __name__ == "__main__":
    main()
