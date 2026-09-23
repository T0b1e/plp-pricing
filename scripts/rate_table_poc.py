"""POC: estimated price by distance bin x vehicle class.

    python scripts/rate_table_poc.py

Distance bins are round numbers picked from the real trip distribution in
data/trips_clean.csv (each bin covers a meaningful share of actual trips).
Prices come straight from the already-fitted model via src.estimate.estimate() -
no new model logic here.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import estimate
from src.files import DataFileError
from src.fit_model import ALL

BINS = [
    ("0-10", 5),
    ("11-20", 15),
    ("21-30", 25),
    ("31-50", 40),
    ("51-100", 75),
    ("101-150", 125),
    ("151-200", 175),
    ("201-300", 250),
    ("301-500", 400),
    ("500+", 650),
]


def build_table(summary, rates) -> pd.DataFrame:
    classes = (
        summary.loc[summary["vehicle_class"] != ALL]
        .sort_values("n", ascending=False)["vehicle_class"]
        .tolist()
    )
    rows = {}
    for label, km in BINS:
        row = {"km": km}
        for vehicle in classes:
            e = estimate.estimate(km, vehicle, stops=2, summary=summary, rates=rates)
            price = f"{e['price']:,.0f}"
            if e["extrapolated"]:
                price += "*"
            row[vehicle] = price
        rows[label] = row
    return pd.DataFrame(rows).T


def main():
    try:
        summary, rates = estimate.load()
    except DataFileError as e:
        sys.exit(str(e))

    table = build_table(summary, rates)
    print(table.to_string())
    print("\n* = km outside this vehicle class's training data (extrapolated, rough guide only)")

    out_path = Path(__file__).resolve().parent.parent / "data" / "rate_table_poc.csv"
    table.to_csv(out_path)
    print(f"\nWritten to {out_path}")


if __name__ == "__main__":
    main()
