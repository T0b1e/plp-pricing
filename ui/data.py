"""Loaders for the pipeline outputs in data/ (cached across reruns)."""
import json

import numpy as np
import pandas as pd
import streamlit as st

from src.aliases import canonical_map
from src.files import DataFileError, read_csv, require_columns
from src.paths import EPPO_DIESEL, LOCATIONS, MODEL_SUMMARY, RATE_TABLE, TRIPS_CLEAN, TRIPS_PARSED
from src.vehicles import vehicle_class


# cache_resource shares one copy across reruns and users (cache_data would copy ~30k rows per click);
# safe because the page only filters these frames, never modifies them in place
@st.cache_resource(show_spinner="Loading trips…")
def load_trips() -> tuple[pd.DataFrame, str | None]:
    """Trips plus a warning if road km could not be attached (the rest still works without it)."""
    if not TRIPS_PARSED.exists():
        raise DataFileError(f"{TRIPS_PARSED.name} not found - run `python main.py --to parse` first.")
    t = pd.read_parquet(TRIPS_PARSED)
    canon = canonical_map()
    t = t[t["stop_count"] >= 2].copy()
    t["origin_c"] = t["origin"].map(lambda s: canon.get(s, s))
    t["dest_c"] = t["destination"].map(lambda s: canon.get(s, s))
    t["vehicle_class"] = t["vehicle_type"].fillna("").map(vehicle_class)
    t["stops_txt"] = t["stops"].map(" > ".join)
    # "13.584565, 100.276712" per place, built once as a dict: a per-row lookup cost ~5 s on every click
    loc = read_csv(LOCATIONS, dtype=str).fillna("")
    loc = loc[(loc["alias_of"] == "") & (loc["lat"] != "") & (loc["lon"] != "")]
    lat, lon = pd.to_numeric(loc["lat"], errors="coerce"), pd.to_numeric(loc["lon"], errors="coerce")
    ll = {p: f"{a:.6f}, {b:.6f}" for p, a, b in zip(loc["token"], lat, lon) if pd.notna(a) and pd.notna(b)}
    t["origin_latlon"] = t["origin_c"].map(ll).fillna("")
    t["dest_latlon"] = t["dest_c"].map(ll).fillna("")
    t["ship_date"] = pd.to_datetime(t["ship_date"], errors="coerce")
    eppo = load_eppo_diesel()
    if not eppo.empty:
        # nearest published EPPO reading to ship_date, no cutoff - EPPO publishing lags real time
        # by weeks/months, so a trip more recent than the latest reading still gets that reading
        # rather than being left blank. merge_asof needs both sides sorted on the join key
        dated = t[t["ship_date"].notna()].sort_values("ship_date")
        matched = pd.merge_asof(dated, eppo, left_on="ship_date", right_on="date", direction="nearest")
        t["eppo_price"] = matched["eppo_price"].reindex(t.index)
    else:
        t["eppo_price"] = np.nan
    fuel_cols = ["fuel_rate_bracket", "fuel_rate_low", "fuel_rate_high"]
    t["total_km"] = np.nan
    for c in fuel_cols:
        t[c] = np.nan
    warning = None
    if TRIPS_CLEAN.exists():
        try:
            extra_cols = ["job_order_no", "total_km", *fuel_cols]
            extra = read_csv(TRIPS_CLEAN, usecols=extra_cols, dtype={"job_order_no": str})
            t = t.drop(columns=["total_km", *fuel_cols]) \
                 .merge(extra.drop_duplicates("job_order_no"), on="job_order_no", how="left")
        except Exception as e:   # e.g. the pipeline is rewriting it right now
            warning = f"Road km per trip not loaded ({TRIPS_CLEAN.name}: {e}). Press Reload data to retry."
    return t, warning


@st.cache_resource(show_spinner="Loading EPPO diesel reference price…")
def load_eppo_diesel() -> pd.DataFrame:
    """EPPO HSD B7 reference price (scripts/fetch_eppo_diesel.py), one row per published date.

    Empty frame (not an error) if the file hasn't been fetched yet - the divergence column
    just stays blank until someone runs the script.
    """
    if not EPPO_DIESEL.exists():
        return pd.DataFrame(columns=["date", "eppo_price"])
    with EPPO_DIESEL.open(encoding="utf-8") as f:
        raw = json.load(f)
    e = pd.DataFrame(raw.values())
    e["date"] = pd.to_datetime(e["date"])
    return e[["date", "no_bias"]].rename(columns={"no_bias": "eppo_price"}).sort_values("date")


@st.cache_resource(show_spinner="Loading places…")
def load_locations() -> pd.DataFrame:
    loc = read_csv(LOCATIONS, dtype=str).fillna("")
    require_columns(loc, ["token", "alias_of", "lat", "lon"], LOCATIONS.name)
    return loc[loc["alias_of"] == ""].drop_duplicates("token").set_index("token")


@st.cache_data(show_spinner="Loading price model…")
def load_model():
    if not (MODEL_SUMMARY.exists() and RATE_TABLE.exists()):
        return None, None
    return read_csv(MODEL_SUMMARY), read_csv(RATE_TABLE)
