"""Step 5: road km per trip via Google Routes API, then write trips_clean.csv.

Only the consecutive stop pairs that actually occur are routed (plus origin->destination
for multi-stop trips), and every result is cached by coordinates.
"""
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import google_api as g
from . import db
from .aliases import canonical_map
from .files import read_csv
from .parse_route import fuel_bracket
from .paths import LOCATIONS

WORKERS = 8
MAX_KM = 1500   # longest plausible domestic run
# Used only while Routes API is disabled: straight-line km * typical Thai road detour.
DETOUR = 1.3
FALLBACK_SOURCE = f"haversine_x{DETOUR}"


def haversine_km(pair_key: str) -> float:
    (a, b), (c, d) = (tuple(map(float, p.split(","))) for p in pair_key.split("|"))
    a, b, c, d = map(np.radians, (a, b, c, d))
    h = np.sin((c - a) / 2) ** 2 + np.cos(a) * np.cos(c) * np.sin((d - b) / 2) ** 2
    return float(6371 * 2 * np.arcsin(np.sqrt(h)))


def point_key(lat, lon) -> str:
    return f"{float(lat):.5f},{float(lon):.5f}"


def load_cache() -> dict:
    return db.load_distance_cache()


def save_cache(cache: dict):
    db.save_distance_cache(cache)


def fetch(pair_key: str) -> tuple[str, dict]:
    o, d = pair_key.split("|")
    (olat, olon), (dlat, dlon) = (map(float, o.split(",")), map(float, d.split(",")))
    try:
        resp = g.route_distance(olat, olon, dlat, dlon)
    except (g.ApiDisabled, g.MissingKey):   # affects every leg: stop instead of caching N errors
        raise
    except Exception as e:  # keep going; failures are reported, not zeroed
        return pair_key, {"km": None, "error": str(e)[:200]}
    try:
        route = (resp.get("routes") or [None])[0]
        if not route or "distanceMeters" not in route:
            return pair_key, {"km": None, "error": "no_route"}
        return pair_key, {"km": route["distanceMeters"] / 1000,
                          "minutes": int(route.get("duration", "0s").rstrip("s")) / 60}
    except Exception as e:  # keep going; failures are reported, not zeroed
        return pair_key, {"km": None, "error": str(e)[:200]}


def main():
    trips = db.read_table("trips_parsed", json_cols=["stops", "conditions", "non_place"])
    loc = read_csv(LOCATIONS, dtype=str).fillna("")
    canon = canonical_map()
    coords, bad_coords = {}, []
    for r in loc.itertuples():
        if r.alias_of == "" and r.lat and r.lon:
            try:
                coords[r.token] = point_key(r.lat, r.lon)
            except ValueError:   # hand-typed "13,75" or similar
                bad_coords.append(f"{r.token} ({r.lat}, {r.lon})")
    if bad_coords:
        print(f"  !! {len(bad_coords)} places have lat/lon that are not numbers - treated as not geocoded. "
              f"Fix in {LOCATIONS.name}:\n     " + "\n     ".join(bad_coords[:20]))

    def points(stops):
        return [coords.get(canon.get(s, s)) for s in stops]

    trips["points"] = trips["stops"].map(points)

    pairs = set()
    for pts in trips["points"]:
        if len(pts) < 2 or None in pts:
            continue
        pairs.update(f"{a}|{b}" for a, b in zip(pts, pts[1:]) if a != b)
        if pts[0] != pts[-1]:
            pairs.add(f"{pts[0]}|{pts[-1]}")

    cache = load_cache()
    todo = sorted(p for p in pairs if p not in cache or cache[p].get("km") is None)
    print(f"[distance] {len(pairs)} distinct legs, {len(todo)} to fetch from Routes API")
    routes_off = False
    if todo:
        try:
            k, v = fetch(todo[0])   # probe once before fanning out
            cache[k] = v
        except g.ApiDisabled:
            routes_off = True
            print(f"  !! Routes API is not enabled - using straight-line km x {DETOUR} for now.\n"
                  f"     Enable Routes API and rerun `python main.py --from distance` for road km.")
    try:
        if not routes_off:
            with ThreadPoolExecutor(WORKERS) as ex:
                for n, (k, v) in enumerate(ex.map(fetch, todo[1:]), 2):
                    cache[k] = v
                    if n % 50 == 0:
                        save_cache(cache)
                        print(f"  routed {n}/{len(todo)}")
    finally:   # Ctrl+C or a fatal API error: keep every leg already paid for
        save_cache(cache)

    fallback_used = set()

    def leg(a, b):
        if a == b:
            return 0.0
        key = f"{a}|{b}"
        v = cache.get(key, {}).get("km")
        if v is None and routes_off:   # never cached, so a later Routes run replaces it
            fallback_used.add(key)
            return haversine_km(key) * DETOUR
        return np.nan if v is None else v

    def total_km(pts):
        if len(pts) < 2 or None in pts:
            return np.nan
        return sum(leg(a, b) for a, b in zip(pts, pts[1:]))   # NaN leg -> NaN total

    def direct_km(pts):
        if len(pts) < 2 or None in pts:
            return np.nan
        return leg(pts[0], pts[-1])

    def km_source(pts):
        if len(pts) < 2 or None in pts:
            return ""
        legs = {f"{a}|{b}" for a, b in zip(pts, pts[1:]) if a != b}
        return FALLBACK_SOURCE if legs & fallback_used else "routes"

    trips["total_km"] = trips["points"].map(total_km)
    trips["direct_km"] = trips["points"].map(direct_km)
    trips["km_source"] = trips["points"].map(km_source)
    for end, idx in [("origin", 0), ("dest", -1)]:
        trips[f"{end}_lat"] = trips["points"].map(lambda p: float(p[idx].split(",")[0]) if p and p[idx] else np.nan)
        trips[f"{end}_lon"] = trips["points"].map(lambda p: float(p[idx].split(",")[1]) if p and p[idx] else np.nan)

    fuel = [fuel_bracket(list(c), r) for c, r in zip(trips["conditions"], trips["route_raw"])]
    trips["fuel_rate_bracket"] = [f[0] for f in fuel]
    trips["fuel_rate_low"] = [f[1] for f in fuel]
    trips["fuel_rate_high"] = [f[2] for f in fuel]

    trips["stops"] = trips["stops"].map(" > ".join)
    trips["conditions"] = trips["conditions"].map("; ".join)
    trips["non_place"] = trips["non_place"].map("; ".join)
    out_cols = ["bill_no", "job_order_no", "ship_date", "source_file", "status", "customer", "job_type",
                "vehicle_type", "route_raw", "origin", "destination", "stops", "stop_count",
                "conditions", "non_place", "origin_lat", "origin_lon", "dest_lat", "dest_lon",
                "total_km", "direct_km", "km_source", "fuel_rate_bracket", "fuel_rate_low",
                "fuel_rate_high", "price", "contractor_cost", "driver_cost"]
    db.replace_table(trips[out_cols], "trips_clean")

    routable = trips["stop_count"] >= 2
    ok = trips["total_km"].notna()
    bad = ok & ~trips["total_km"].between(0, MAX_KM)
    failed = [k for k in pairs if cache.get(k, {}).get("km") is None]
    print(f"[distance] trips with >=2 stops: {routable.sum():,}; with km: {(routable & ok).sum():,}; "
          f"missing (ungeocoded stop or failed leg): {(routable & ~ok).sum():,}")
    print(f"           failed legs: {len(failed) - len(fallback_used)}; implausible km (>{MAX_KM}): {bad.sum()}")
    print("           km source: " + trips.loc[ok, "km_source"].value_counts().to_dict().__repr__())
    print("           -> trips_clean table")


if __name__ == "__main__":
    main()
