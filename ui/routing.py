"""Road km between two known places: distance cache first, one Routes API call if asked."""
import pandas as pd
import streamlit as st

from src import distance, google_api
from src.files import DataFileError


def leg_key(locs: pd.DataFrame, origin: str, dest: str) -> str:
    """Distance-cache key for one origin -> destination leg, built from rounded lat/lon."""
    o, d = locs.loc[origin], locs.loc[dest]
    return f"{distance.point_key(o['lat'], o['lon'])}|{distance.point_key(d['lat'], d['lon'])}"


def road_km(locs: pd.DataFrame, origin: str, dest: str) -> tuple[float | None, str]:
    """Road km between two canonical places: distance cache first, Routes API if asked."""
    try:
        o, d = locs.loc[origin], locs.loc[dest]
    except KeyError:
        return None, "place not in locations_master.csv"
    if not (o["lat"] and o["lon"] and d["lat"] and d["lon"]):
        missing = [n for n, r in [(origin, o), (dest, d)] if not (r["lat"] and r["lon"])]
        return None, "no coordinates yet for: " + ", ".join(missing)
    try:
        key = leg_key(locs, origin, dest)
    except ValueError:
        return None, "lat/lon in locations_master.csv is not a number for one of these places"
    try:
        cache = distance.load_cache()
    except DataFileError as e:
        return None, str(e)
    if cache.get(key, {}).get("km") is not None:
        return cache[key]["km"], "distance cache"
    # one click = one API call: the flag is consumed here, so a failure is not retried on every rerun
    if not st.session_state.pop(f"allow_api_{key}", False):
        return None, "ask"
    try:
        with st.spinner(f"Asking Google Routes for the road km {origin} → {dest}…"):
            k, v = distance.fetch(key)
    except google_api.ApiDisabled:
        return None, "Routes API is not enabled on the Google key"
    except google_api.MissingKey:
        return None, "no GOOGLE_MAPS_API_KEY in .env on the machine running this page"
    if v.get("km") is None:   # not cached: a failed lookup should be retryable
        return None, f"Routes API: {v.get('error', 'no route')}"
    cache[k] = v
    try:
        distance.save_cache(cache)
    except Exception as e:   # the km is still good for this page view
        st.toast(f"Road km not saved to the cache: {e}")
    return v["km"], "Google Routes API"
