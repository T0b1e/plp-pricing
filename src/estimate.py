"""Step 7: the CLI. Input km (+ vehicle class), get an estimated price.

    python -m src.estimate --km 120 --vehicle "10W reefer"
    python -m src.estimate --km 250 --vehicle 22 --stops 4
    python -m src.estimate --list

Reads only data/model_summary.csv and data/rate_table.csv - no API calls.
"""
import argparse
import sys

from rapidfuzz import fuzz, process

from .files import DataFileError, read_csv
from .fit_model import ALL, BAND_LABELS, BANDS, OWN, predict
from .paths import MODEL_SUMMARY, RATE_TABLE


class UnknownVehicle(ValueError):
    pass


def load():
    if not (MODEL_SUMMARY.exists() and RATE_TABLE.exists()):
        raise DataFileError("No model yet - run `python main.py` first.")
    return read_csv(MODEL_SUMMARY), read_csv(RATE_TABLE)


def match_vehicle(query: str, classes: list[str]) -> str | None:
    q = query.strip().lower().replace("wheel", "w").replace("ล้อ", "w").replace(" ", "")
    exact = [c for c in classes if c.lower().replace(" ", "") == q]
    if exact:
        return exact[0]
    if q.isdigit():   # "10" -> the most common 10W class
        hits = [c for c in classes if c.startswith(f"{q}W")]
        return hits[0] if hits else None
    best = process.extractOne(q, classes, scorer=fuzz.WRatio,
                              processor=lambda s: s.lower().replace(" ", ""))
    return best[0] if best and best[1] >= 70 else None


def band_of(km: float) -> str | None:
    for (a, b), label in zip(zip(BANDS, BANDS[1:]), BAND_LABELS):
        if a <= km <= b:
            return label
    return None


def estimate(km: float, vehicle: str | None, stops: int, summary, rates) -> dict:
    classes = summary.loc[summary["vehicle_class"] != ALL].sort_values("n", ascending=False)["vehicle_class"].tolist()
    name = ALL
    if vehicle and vehicle != ALL:
        name = match_vehicle(vehicle, classes)
        if name is None:
            raise UnknownVehicle(f"Unknown vehicle '{vehicle}'. Known: {', '.join(classes)}")
    m = summary.set_index("vehicle_class").loc[name].to_dict()
    extra = max(stops - 2, 0)
    price = predict(m, km, extra)
    out = {"vehicle": name, "km": km, "stops": stops, "price": price,
           "low": price * m["ratio_p10"], "high": price * m["ratio_p90"], **m}

    band = band_of(km)
    b = rates[(rates["vehicle_class"] == name) & (rates["band"] == band)]
    out["band"] = band
    out["band_row"] = b.iloc[0].to_dict() if len(b) else None
    out["extrapolated"] = not (m["km_min"] <= km <= m["km_max"])
    return out


def show(e: dict):
    fmt = lambda x: f"{x:,.0f}"
    print(f"\nEstimated price: {fmt(e['price'])} THB   "
          f"(typical range {fmt(e['low'])} - {fmt(e['high'])})")
    print(f"  vehicle {e['vehicle']}, {e['km']:g} km, {e['stops']} stops")
    drops = f" + {e['drop_fee']:,.0f}/extra drop" if e["drop_fee"] else ""
    method = ("own trips - line fitted on this truck class's own bills" if e["method"] == OWN else
              "borrowed - too few trips for this class, all-truck line scaled to its prices")
    print(f"  method: {method}: {e['base_fare']:,.0f} + {e['rate_per_km']:.2f} THB/km{drops}"
          f"   r2={e['r2']:.2f}  holdout error (median) {e['test_mdape_pct']:.0f}%  n={int(e['n'])}")
    br = e["band_row"]
    if br and br["n"] > 0:
        print(f"  cross-check, actual trips {e['band']} km: median {fmt(br['median_price'])} THB "
              f"(p10-p90 {fmt(br['p10'])} - {fmt(br['p90'])}, n={int(br['n'])})")
    else:
        print(f"  cross-check: no historical trips in the {e['band']} km band for this vehicle")
    if e["extrapolated"]:
        print(f"  !! {e['km']:g} km is outside the data for this vehicle "
              f"({e['km_min']:.0f}-{e['km_max']:.0f} km) - treat as a rough guide")
    if e["vehicle"] == ALL:
        print("  !! no --vehicle given: truck size dominates price, pass --vehicle for a real quote")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Estimate transport price from km.")
    ap.add_argument("--km", type=float, help="road distance in km (sum of legs for multi-stop)")
    ap.add_argument("--vehicle", "-v", help='vehicle class, e.g. "10W reefer", "4W dry", "22W head", or just "10"')
    ap.add_argument("--stops", type=int, default=2, help="number of stops incl. origin (default 2 = A->B)")
    ap.add_argument("--list", action="store_true", help="list vehicle classes and their curves")
    a = ap.parse_args(argv)

    if a.stops < 1:
        ap.error("--stops must be at least 1")
    try:
        summary, rates = load()
    except DataFileError as e:
        sys.exit(str(e))
    if a.list:
        cols = ["vehicle_class", "method", "n", "base_fare", "rate_per_km", "drop_fee", "r2", "test_mdape_pct"]
        print(summary[cols].round(1).to_string(index=False))
        return
    if a.km is None or a.km <= 0:
        ap.error("--km must be a positive number")
    try:
        show(estimate(a.km, a.vehicle, a.stops, summary, rates))
    except UnknownVehicle as e:
        ap.error(str(e))


if __name__ == "__main__":
    main()
