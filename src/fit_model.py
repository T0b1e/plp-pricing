"""Step 6: km -> price, one curve per vehicle class.

    price = base_fare + rate_per_km * total_km + drop_fee * extra_drops

extra_drops = stops beyond a plain A->B trip (stop_count - 2, floored at 0). Fitted with
trimmed least squares (drop |residual| > 3 MAD, refit) so a few odd bills don't bend the
line. Also writes a distance-band table of medians as a model-free cross-check.
"""
import numpy as np
import pandas as pd

from . import db
from .files import DataFileError, require_columns
from .robust import mad_scale
from .vehicles import vehicle_class, wheels

MIN_N = 30
BANDS = [0, 25, 50, 100, 200, 400, 800, 1500]
BAND_LABELS = [f"{a}-{b}" for a, b in zip(BANDS, BANDS[1:])]
HOLDOUT = 0.2
SEED = 42
ALL = "ALL"
# How a class's line was built, in words a reader of the table understands:
OWN = "Own trips"                    # >= MIN_N trips: fitted on this class's own bills
BORROWED = "Borrowed – few trips"    # < MIN_N trips: the all-truck line, scaled to this class's prices


def load_training() -> pd.DataFrame:
    df = db.read_table("trips_clean")
    require_columns(df, ["vehicle_type", "total_km", "price", "stop_count", "status"], "trips_clean")
    df["vehicle_class"] = df["vehicle_type"].fillna("").map(vehicle_class)
    # CONFIRM = sent for billing, actually completed; OPEN/POSTED jobs may never have shipped
    # and would otherwise pollute the price curve as outliers.
    df = df[df["vehicle_class"].notna() & df["total_km"].gt(0) & df["price"].gt(0)
            & df["status"].eq("CONFIRM")].copy()
    df["extra_drops"] = (df["stop_count"] - 2).clip(lower=0)
    df["band"] = pd.cut(df["total_km"], BANDS, labels=BAND_LABELS, include_lowest=True)
    return df


def design(d: pd.DataFrame, with_drops: bool) -> np.ndarray:
    cols = [np.ones(len(d)), d["total_km"].to_numpy()]
    if with_drops:
        cols.append(d["extra_drops"].to_numpy())
    return np.column_stack(cols)


def fit(d: pd.DataFrame) -> dict:
    with_drops = d["extra_drops"].gt(0).sum() >= 10
    X, y = design(d, with_drops), d["price"].to_numpy()
    keep = np.ones(len(y), bool)
    for _ in range(3):
        beta, *_ = np.linalg.lstsq(X[keep], y[keep], rcond=None)
        resid = y - X @ beta
        # scale is estimated on the currently-kept rows only, then applied to every row
        scale = mad_scale(resid[keep], y[keep])
        new_keep = np.abs(resid) <= 3 * scale
        if new_keep.sum() < max(X.shape[1] + 1, len(y) // 2) or (new_keep == keep).all():
            break
        keep = new_keep
    pred = X @ beta
    ss_res = ((y[keep] - pred[keep]) ** 2).sum()
    ss_tot = ((y[keep] - y[keep].mean()) ** 2).sum()
    ratio = y[keep] / np.maximum(pred[keep], 1)
    return {
        "n": len(y), "n_used": int(keep.sum()),
        "base_fare": beta[0], "rate_per_km": beta[1],
        "drop_fee": beta[2] if with_drops else 0.0,
        "r2": 1 - ss_res / ss_tot if ss_tot else np.nan,
        # actual/predicted ratio quantiles -> the band shown around an estimate
        "ratio_p10": np.quantile(ratio, 0.10), "ratio_p90": np.quantile(ratio, 0.90),
        "km_min": d["total_km"].min(), "km_max": d["total_km"].max(),
    }


def predict(row: dict, km: float, extra_drops: float = 0) -> float:
    return row["base_fare"] + row["rate_per_km"] * km + row["drop_fee"] * extra_drops


def mdape(model: dict, test: pd.DataFrame) -> float:
    pred = predict(model, test["total_km"], test["extra_drops"])
    return float(np.median(np.abs(pred - test["price"]) / test["price"]) * 100)


def main():
    df = load_training()
    if len(df) < MIN_N / (1 - HOLDOUT):
        raise DataFileError(f"Only {len(df)} trips with km and price - too few to fit. "
                            f"Run the geocode and distance steps first.")
    rng = np.random.default_rng(SEED)
    df["is_test"] = rng.random(len(df)) < HOLDOUT

    groups = {ALL: df, **{v: g for v, g in df.groupby("vehicle_class")}}
    summary = []
    for name, g in groups.items():
        train, test = g[~g["is_test"]], g[g["is_test"]]
        if len(train) < MIN_N:
            summary.append({"vehicle_class": name, "n": len(g), "method": BORROWED})
            continue
        m = fit(train)
        m["test_mdape_pct"] = mdape(m, test) if len(test) else np.nan
        m.update(fit(g))   # final coefficients use every row; the test score above stays honest
        summary.append({"vehicle_class": name, "method": OWN, **m})
    s = pd.DataFrame(summary)

    # small classes: the ALL curve scaled by the class's median price relative to ALL's prediction
    all_row = s[s["vehicle_class"] == ALL].iloc[0].to_dict()
    for i, r in s[s["method"] == BORROWED].iterrows():
        g = groups[r["vehicle_class"]]
        pred = predict(all_row, g["total_km"], g["extra_drops"])
        ok = pred > 0   # the ALL line can dip below zero at very short km; those rows can't give a ratio
        scale = float(np.median(g["price"][ok] / pred[ok])) if ok.any() else 1.0
        for k in ["base_fare", "rate_per_km", "drop_fee"]:
            s.at[i, k] = all_row[k] * scale
        for k in ["ratio_p10", "ratio_p90", "r2", "test_mdape_pct"]:
            s.at[i, k] = all_row[k]
        s.at[i, "km_min"], s.at[i, "km_max"] = g["total_km"].min(), g["total_km"].max()
        s.at[i, "n_used"] = len(g)

    db.replace_table(s, "model_summary")

    def band_stats(d: pd.DataFrame) -> pd.DataFrame:
        return (d.assign(baht_per_km=d["price"] / d["total_km"])
                 .groupby("band", observed=True)
                 .agg(n=("price", "size"), median_price=("price", "median"),
                      p10=("price", lambda x: x.quantile(0.1)), p90=("price", lambda x: x.quantile(0.9)),
                      median_km=("total_km", "median"), median_baht_per_km=("baht_per_km", "median"))
                 .reset_index())

    # true pooled median per band, not a median of each class's median - that would be biased
    # whenever classes have unequal trip counts
    all_bands = band_stats(df).assign(vehicle_class=ALL)
    per_class_bands = df.groupby("vehicle_class", observed=True).apply(band_stats, include_groups=False) \
        .reset_index(level=0)
    bands = pd.concat([all_bands, per_class_bands], ignore_index=True)
    bands = bands.merge(s[["vehicle_class", "method", "base_fare", "rate_per_km", "drop_fee", "r2"]],
                        on="vehicle_class", how="left")
    db.replace_table(bands, "rate_table")

    report(df, s)


def report(df, s):
    pd.set_option("display.width", 200)
    all_rows = db.read_table("trips_clean")[["price", "status"]]
    total = len(all_rows)
    status_counts = all_rows["status"].value_counts().to_dict()
    print(f"[model] trained on {len(df):,} of {total:,} rows "
          f"({len(df) / total:.0%}); multi-stop rows: {(df['extra_drops'] > 0).sum():,}")
    print(f"        status breakdown (all rows): {status_counts} - only CONFIRM is used for training")
    src = df["km_source"].value_counts().to_dict() if "km_source" in df else {}
    print(f"        km source: {src}")
    if any(k != "routes" for k in src):
        print("        !! some km are straight-line estimates, not road distance - "
              "enable Routes API and rerun `python main.py --from distance`")
    cols = ["vehicle_class", "method", "n", "base_fare", "rate_per_km", "drop_fee", "r2",
            "test_mdape_pct", "km_min", "km_max"]
    print(s[cols].round(2).to_string(index=False))

    lin = s[(s["method"] == OWN) & (s["vehicle_class"] != ALL)].copy()
    bad = lin[lin["rate_per_km"] <= 0]
    if len(bad):
        print(f"\n!! non-positive rate_per_km for: {', '.join(bad['vehicle_class'])} "
              "- check geocoding for those trips")
    for kind in ["reefer", "dry", "head"]:
        k = lin[lin["vehicle_class"].str.contains(kind)].copy()
        if len(k) < 2:
            continue
        k["w"] = k["vehicle_class"].map(wheels)
        k = k.sort_values("w")
        mono = k["rate_per_km"].is_monotonic_increasing
        order = " < ".join(f"{r.vehicle_class} {r.rate_per_km:.1f}" for r in k.itertuples())
        print(f"{'ok' if mono else '!!'} {kind} baht/km by size: {order}")
    print("\n-> rate_table, model_summary tables")


if __name__ == "__main__":
    main()
