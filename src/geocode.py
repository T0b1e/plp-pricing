"""Step 4: one lat/lon per canonical place, cached so the API bill is paid once.

Order per place: Places Text Search (New) -> Geocoding API fallback.
Rows you fix by hand (set lat/lon and geocode_source=manual, or verified=y) are never
overwritten. Edit `search_query` to steer a bad match, then rerun.
"""
import re

import pandas as pd
from rapidfuzz import fuzz

from . import google_api as g
from .files import read_csv, read_json, write_csv, write_json
from .paths import GEOCODE_CACHE, LOCATIONS

LOCKED_SOURCES = {"manual"}
MAX_FAILS_IN_A_ROW = 10


def default_query(token: str) -> str:
    """'PCS (ห้องเย็นแปซิฟิค)' -> 'ห้องเย็นแปซิฟิค PCS'. Thai names search far better than codes."""
    m = re.match(r"^\s*([^()]+?)\s*\(([^()]+)\)\s*(.*)$", token)
    if m:
        head, inner, tail = (x.strip() for x in m.groups())
        return re.sub(r"\s+", " ", f"{inner} {head} {tail}").strip()
    return token.replace("(", " ").replace(")", " ").strip()


def load_cache() -> dict:
    return read_json(GEOCODE_CACHE)


def save_cache(cache: dict):
    write_json(GEOCODE_CACHE, cache, ensure_ascii=False, indent=1)


FOREIGN = re.compile(r"มาเลเซีย|Malaysia|เมียนมา|Myanmar|ลาว\b|Laos|กัมพูชา|Cambodia")


def pick(query: str, places: list[dict]) -> tuple[dict | None, str]:
    """Choose a result and grade it: high / medium / low."""
    # the lat/lon box also covers bits of Malaysia, Myanmar, Laos and Cambodia; check the address too
    places = [p for p in places if "location" in p
              and g.in_thailand(p["location"]["latitude"], p["location"]["longitude"])
              and not FOREIGN.search(p.get("formattedAddress", ""))]
    if not places:
        return None, "none"
    scored = [(fuzz.partial_ratio(query.lower(), p.get("displayName", {}).get("text", "").lower()), p)
              for p in places]
    scored.sort(key=lambda sp: -sp[0])
    score, best = scored[0]
    if len(places) == 1 or score >= 80:
        return best, "high"
    if score >= 55:
        return best, "medium"
    return best, "low"


_places_off = False   # set once Places (New) answers "API not enabled"; skip it for the run


def lookup(query: str, cache: dict) -> dict:
    global _places_off
    entry = cache.get(query)
    # geocoder errors (quota etc.) cached by older versions of this script are not answers
    if entry and entry.get("geocode", {}).get("status", "OK") not in ("OK", "ZERO_RESULTS"):
        if entry.get("places", {}).get("places"):
            del entry["geocode"]
        else:
            entry["geocode"] = g.geocode(query)   # raises again if still failing; entry stays stale
    # entries made while Places was disabled are retried with Places on a later run
    if entry is None or (entry.get("places_skipped") and not _places_off):
        entry = {}
        if not _places_off:
            try:
                entry["places"] = g.places_text_search(query)
            except g.ApiDisabled:
                _places_off = True
                print("  !! Places API (New) is not enabled - using Geocoding API only. "
                      "Enable it and rerun for better facility matches.")
        if _places_off:
            entry["places_skipped"] = True
        if not entry.get("places", {}).get("places"):
            entry["geocode"] = (cache.get(query) or {}).get("geocode") or g.geocode(query)
        cache[query] = entry
    entry.setdefault("places", {})

    best, conf = pick(query, entry["places"].get("places", []))
    # a bare code like "CW2" or "TUF" matches plus codes and random shops; never trust it blindly
    if best and re.fullmatch(r"[A-Za-z0-9/&.\-]{1,6}", query.strip()):
        conf = "low"
    if best:
        return {"lat": best["location"]["latitude"], "lon": best["location"]["longitude"],
                "place_id": best["id"], "formatted_address": best.get("formattedAddress", ""),
                "geocode_source": "places", "geocode_confidence": conf}

    for res in entry.get("geocode", {}).get("results", []):
        loc = res["geometry"]["location"]
        if not g.in_thailand(loc["lat"], loc["lng"]):
            continue
        # a bare "Thailand" or province centroid is not a location for pricing
        types = set(res.get("types", []))
        if types & {"country", "administrative_area_level_1"}:
            conf = "none"      # "Thailand" or a province centroid is useless for km
        elif res.get("partial_match") or types & {"route", "plus_code", "administrative_area_level_2"}:
            conf = "low"
        elif types & {"establishment", "point_of_interest", "premise", "street_address"}:
            conf = "medium"    # geocoder matched a POI; Places would confirm it
        else:
            conf = "low"       # locality / sublocality level
        if conf == "none":
            continue
        return {"lat": loc["lat"], "lon": loc["lng"], "place_id": res.get("place_id", ""),
                "formatted_address": res.get("formatted_address", ""),
                "geocode_source": "geocoding", "geocode_confidence": conf}

    return {"lat": "", "lon": "", "place_id": "", "formatted_address": "",
            "geocode_source": "not_found", "geocode_confidence": "none"}


def main(limit: int | None = None):
    loc = read_csv(LOCATIONS, dtype=str).fillna("")
    cache = load_cache()
    todo = loc[(loc["alias_of"] == "")
               & ~loc["geocode_source"].isin(LOCKED_SOURCES)
               & (loc["verified"].str.lower() != "y")]
    if limit:
        todo = todo.head(limit)

    new_calls, failed, in_a_row = 0, [], 0
    try:
        for n, (i, row) in enumerate(todo.iterrows(), 1):
            query = row["search_query"] or default_query(row["token"])
            loc.at[i, "search_query"] = query
            new_calls += query not in cache or bool(cache[query].get("places_skipped") and not _places_off)
            try:
                result = lookup(query, cache)
            except g.ApiError as e:   # row keeps its old values and is retried on the next run
                failed.append(query)
                in_a_row += 1
                print(f"  !! {e}")
                if in_a_row >= MAX_FAILS_IN_A_ROW:
                    raise g.ApiError(f"{in_a_row} lookups in a row failed - network or quota problem? "
                                     f"Progress is saved; rerun later.") from e
                continue
            in_a_row = 0
            for k, v in result.items():
                loc.at[i, k] = str(v)
            if n % 25 == 0:
                save_cache(cache)
                print(f"  geocoded {n}/{len(todo)}")
    finally:   # Ctrl+C or a fatal error: keep what was already looked up
        save_cache(cache)
        write_csv(loc, LOCATIONS, index=False)

    canon = loc[loc["alias_of"] == ""]
    print(f"[geocode] {len(todo)} places processed, {new_calls} new API lookups")
    if failed:
        print(f"  !! {len(failed)} lookups failed (left unchanged, retried on the next run): "
              + ", ".join(failed[:10]) + (" ..." if len(failed) > 10 else ""))
    print(canon["geocode_confidence"].value_counts().to_string())
    review = canon[canon["geocode_confidence"].isin(["low", "none", "medium"])].copy()
    review["frequency"] = pd.to_numeric(review["frequency"], errors="coerce").fillna(0).astype(int)
    review = review.sort_values("frequency", ascending=False)
    print(f"\n{len(review)} places need review (most-used first). Fix lat/lon + geocode_source=manual,")
    print(f"or edit search_query and rerun, in {LOCATIONS.name}:")
    print(review[["token", "frequency", "geocode_confidence", "formatted_address"]].head(30).to_string(index=False))


if __name__ == "__main__":
    import sys
    main(int(sys.argv[1]) if len(sys.argv) > 1 else None)
