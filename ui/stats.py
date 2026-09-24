"""Pure computations on the trips / model frames (no page layout)."""
import numpy as np
import pandas as pd
import streamlit as st

from src.estimate import estimate
from src.fit_model import ALL
from src.robust import mad_outlier_mask


def price_stats(df: pd.DataFrame) -> pd.DataFrame:
    """One row per vehicle class: count, spread and the latest price actually billed."""
    d = df[df["price"] > 0].sort_values("ship_date")
    if d.empty:
        return pd.DataFrame()
    g = d.groupby(d["vehicle_class"].fillna("(unclassified)"))
    out = g.agg(trips=("price", "size"), median=("price", "median"),
                p10=("price", lambda x: x.quantile(0.1)), p90=("price", lambda x: x.quantile(0.9)),
                min=("price", "min"), max=("price", "max"),
                last_price=("price", "last"), last_date=("ship_date", "last"),
                median_km=("total_km", "median"),
                fuel_rate_low=("fuel_rate_low", "median"), fuel_rate_high=("fuel_rate_high", "median"))
    return out.sort_values("trips", ascending=False).reset_index()


def cutoff_price_stats(values: np.ndarray) -> dict:
    """Plain mean, a 3-sigma-trimmed mean, and a modified-Z-trimmed mean, each with its own N.

    3-sigma: drop |x - mean| > 3*std, recompute mean over survivors.
    Modified Z (Iglewicz & Hoaglin): drop |0.6745*(x-median)/MAD| > 3.5, recompute mean.
    Falls back to the plain mean/N when there are too few points, or no spread, to trim.
    """
    n = len(values)
    avg = float(np.mean(values)) if n else np.nan
    out = {"avg": avg, "n": n, "avg_3sigma": avg, "n_3sigma": n, "avg_modiz": avg, "n_modiz": n}
    if n < 2:
        return out

    std = np.std(values)
    if std > 0:
        keep = np.abs(values - np.mean(values)) <= 3 * std
        if keep.any():
            out["avg_3sigma"], out["n_3sigma"] = float(values[keep].mean()), int(keep.sum())

    med = np.median(values)
    mad = np.median(np.abs(values - med))
    if mad > 0:
        modified_z = 0.6745 * (values - med) / mad
        keep = np.abs(modified_z) <= 3.5
        if keep.any():
            out["avg_modiz"], out["n_modiz"] = float(values[keep].mean()), int(keep.sum())

    return out


def with_total(df: pd.DataFrame, sum_cols: list[str], label_col: str) -> pd.DataFrame:
    """Append a TOTAL row: sums for sum_cols, 'TOTAL (n trips)' in label_col, other cells empty."""
    if df.empty:
        return df
    total = {c: df[c].sum(min_count=1) for c in sum_cols}
    total[label_col] = f"TOTAL ({len(df):,} trips)"
    return pd.concat([df, pd.DataFrame([total])], ignore_index=True)


def model_table(summary: pd.DataFrame, rates: pd.DataFrame, km: float, stops: int) -> pd.DataFrame:
    rows = []
    for name in summary.sort_values("n", ascending=False)["vehicle_class"]:
        e = estimate(km, name, stops, summary, rates)
        rows.append({"vehicle_class": name, "estimate": e["price"], "low": e["low"], "high": e["high"],
                     "trips_in_model": int(e["n"]), "method": e["method"],
                     "holdout_error_%": e["test_mdape_pct"],
                     "note": "outside data range" if e["extrapolated"] else ""})
    return pd.DataFrame(rows)


BIN_RANGES = [
    ("0-10", 5), ("11-20", 15), ("21-30", 25), ("31-50", 40), ("51-100", 75),
    ("101-150", 125), ("151-200", 175), ("201-300", 250), ("301-500", 400), ("500+", 650),
]


def bin_rate_table(summary: pd.DataFrame, rates: pd.DataFrame, stops: int = 2) -> pd.DataFrame:
    """One row per distance bin, one column per vehicle class - a quick overview grid.

    Cells are formatted THB strings with a trailing '*' where the km is outside that vehicle
    class's own training data (extrapolated, rough guide only).
    """
    classes = summary.loc[summary["vehicle_class"] != ALL].sort_values("n", ascending=False)["vehicle_class"].tolist()
    rows = []
    for label, km in BIN_RANGES:
        row = {"Range (km)": label, "km": km}
        for name in classes:
            e = estimate(km, name, stops, summary, rates)
            row[name] = f"{e['price']:,.0f}" + ("*" if e["extrapolated"] else "")
        rows.append(row)
    return pd.DataFrame(rows)


def bin_rate_long(summary: pd.DataFrame, rates: pd.DataFrame, vehicles: list[str], stops: int = 2) -> pd.DataFrame:
    """Same estimates as bin_rate_table, but long-form numeric rows for charting:
    one row per (vehicle class, distance bin) with a raw price and an extrapolated flag."""
    rows = []
    for label, km in BIN_RANGES:
        for name in vehicles:
            e = estimate(km, name, stops, summary, rates)
            rows.append({"Range (km)": label, "km": km, "vehicle_class": name,
                         "price": e["price"], "extrapolated": e["extrapolated"]})
    return pd.DataFrame(rows)


def flag_price_outliers(df: pd.DataFrame, group_cols: list[str]) -> pd.Series:
    """True where a trip's price is a MAD-based outlier within its (origin, dest, vehicle class) group.

    Pooled across every month in df - a single month is usually too few points for a stable MAD.
    Groups of size 1 never flag: there's nothing to compare against.
    """
    price = df["price"].to_numpy()
    resid = price - df.groupby(group_cols)["price"].transform("median").to_numpy()
    flags = np.zeros(len(df), dtype=bool)
    for _, idx in df.groupby(group_cols).indices.items():
        if len(idx) < 2:
            continue
        flags[idx] = mad_outlier_mask(resid[idx], price[idx])
    return pd.Series(flags, index=df.index)


@st.cache_data(show_spinner="Crunching monthly price stats…")
def monthly_pair_stats(_trips: pd.DataFrame, statuses: tuple[str, ...], min_trips: int,
                       drop_outliers: bool) -> pd.DataFrame:
    """One row per (origin, dest, vehicle class, month): trip count, mean, median, S.D., CV%.

    Months with fewer than min_trips billed trips are dropped - S.D. on 1-2 points isn't meaningful.
    """
    d = _trips[(_trips["price"] > 0) & _trips["ship_date"].notna()].copy()
    if drop_outliers:
        d = d[~flag_price_outliers(d, ["origin_c", "dest_c", "vehicle_class"])]
    d["month"] = d["ship_date"].dt.to_period("M").astype(str)
    g = d.groupby(["origin_c", "dest_c", "vehicle_class", "month"])["price"]
    out = g.agg(trips="size", mean="mean", median="median", sd="std").reset_index()
    out["sd"] = out["sd"].fillna(0.0)
    out["cv_pct"] = out["sd"] / out["mean"] * 100
    return out[out["trips"] >= min_trips]


@st.cache_data(show_spinner="Computing per-pair price…")
def pair_price_stats(_trips: pd.DataFrame, statuses: tuple[str, ...], drop_outliers: bool) -> pd.DataFrame:
    """One row per (origin, dest, vehicle class): a single representative price for that pair -
    the median of all its billed prices, after the same MAD-outlier filter used elsewhere on this
    tab. This is a whole-history figure (no per-month minimum), unlike `monthly_pair_stats`.
    """
    d = _trips[(_trips["price"] > 0) & _trips["ship_date"].notna()].copy()
    if drop_outliers:
        d = d[~flag_price_outliers(d, ["origin_c", "dest_c", "vehicle_class"])]
    return d.groupby(["origin_c", "dest_c", "vehicle_class"])["price"].median().reset_index(name="price")


def pair_rollup(monthly: pd.DataFrame, price_stats: pd.DataFrame) -> pd.DataFrame:
    """One row per (origin, dest, vehicle class): months of data, total trips, price, avg/worst monthly CV."""
    if monthly.empty:
        return monthly
    g = monthly.groupby(["origin_c", "dest_c", "vehicle_class"])
    out = g.agg(months=("month", "nunique"), trips=("trips", "sum"),
                avg_cv_pct=("cv_pct", "mean"), max_cv_pct=("cv_pct", "max"))
    out = out.sort_values("avg_cv_pct", ascending=False).reset_index()
    out = out.merge(price_stats, on=["origin_c", "dest_c", "vehicle_class"], how="left")
    out.insert(0, "rank", range(1, len(out) + 1))
    return out


def pair_deviations(trips: pd.DataFrame, origin: str, dest: str, vehicle_class: str,
                    monthly: pd.DataFrame, drop_outliers: bool) -> pd.DataFrame:
    """Trips on one pair + vehicle class, with each trip's % deviation from its month's median price.

    Always includes an is_mad_outlier flag (computed on this one pair's trips) so flagged trips are
    visible even when drop_outliers is False; when True, flagged trips are left out entirely, matching
    the aggregate stats in `monthly` (which were computed with the same flag).
    """
    d = trips[(trips["origin_c"] == origin) & (trips["dest_c"] == dest)
              & (trips["vehicle_class"] == vehicle_class) & (trips["price"] > 0)
              & trips["ship_date"].notna()].copy()
    d["is_mad_outlier"] = flag_price_outliers(d, ["origin_c", "dest_c", "vehicle_class"])
    if drop_outliers:
        d = d[~d["is_mad_outlier"]]
    d["month"] = d["ship_date"].dt.to_period("M").astype(str)
    med = monthly[(monthly["origin_c"] == origin) & (monthly["dest_c"] == dest)
                  & (monthly["vehicle_class"] == vehicle_class)][["month", "median"]] \
        .rename(columns={"median": "monthly_median"})
    d = d.merge(med, on="month", how="inner")   # drop months below the min-trips threshold
    d["pct_dev"] = (d["price"] - d["monthly_median"]) / d["monthly_median"] * 100
    cols = ["ship_date", "customer", "price", "month", "monthly_median", "pct_dev",
            "is_mad_outlier", "job_order_no"]
    return d.sort_values("ship_date", ascending=False)[cols]


def fuel_rate_monthly(df: pd.DataFrame, group_col: str | None = None) -> pd.DataFrame:
    """One row per month (+ group_col if given): n trips, min/median/max fuel_rate_high."""
    d = df[df["fuel_rate_high"].notna() & df["ship_date"].notna()].copy()
    if d.empty:
        return d
    d["month"] = d["ship_date"].dt.to_period("M").dt.to_timestamp()
    cols = ["month"] + ([group_col] if group_col else [])
    g = d.groupby(cols)["fuel_rate_high"]
    return g.agg(n="size", min="min", median="median", max="max").reset_index()


def fuel_bracket_sort_key(d: pd.DataFrame) -> pd.Series:
    """Low end of each bracket for sorting; '<30' sorts as if low = -inf."""
    return d["fuel_rate_low"].fillna(-np.inf)
