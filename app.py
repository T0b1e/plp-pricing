"""Web UI for the price lookup:  python -m streamlit run app.py

Tab 1: origin + destination -> historical trips on that route, or a model estimate if none.
Tab 2: km + vehicle -> model estimate, cross-checked against real trips of similar length.

Reads the pipeline outputs in data/. The only API call is an optional Routes lookup for a
route that has never been billed, and its result goes into the same distance cache.
"""
import json

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from src import distance, google_api
from src.aliases import canonical_map
from src.estimate import estimate
from src.files import DataFileError, read_csv, require_columns
from src.fit_model import ALL
from src.paths import EPPO_DIESEL, LOCATIONS, MODEL_SUMMARY, RATE_TABLE, TRIPS_CLEAN, TRIPS_PARSED
from src.robust import mad_outlier_mask
from src.vehicles import vehicle_class

st.set_page_config(page_title="Transport price lookup", page_icon="🚚", layout="wide")


def _tex_num(x: float, decimals: int = 0) -> str:
    """1234.5 -> '1{,}235' so LaTeX doesn't add a space after the thousands comma."""
    return f"{x:,.{decimals}f}".replace(",", "{,}")


def formula_card(km: float, stops: int, vehicle: str | None):
    """Card above the model table: the formula, what each term means, and this km worked through."""
    with st.expander("🧮 How the estimate is calculated", expanded=False):
        st.latex(r"\text{Price (THB/trip)} = \text{Base fare} + \text{Rate per km} \times \text{Road km}"
                 r" + \text{Drop fee} \times \text{Extra drops}")
        t1, t2, t3, t4 = st.columns(4)
        t1.markdown("**Base fare** (THB)  \nFixed part of every trip, even a very short one: loading, "
                    "waiting, minimum charge.")
        t2.markdown("**Rate per km** (THB/km)  \nHow much the price rises for each extra km driven.")
        t3.markdown("**Road km** (km)  \nGoogle driving distance; multi-stop trips add up every leg "
                    "A→B→C.")
        t4.markdown("**Extra drops** (count)  \nStops beyond a plain A→B trip = stops − 2. "
                    "Drop fee is THB per extra drop.")

        # worked example: the chosen truck, or the class with the most trips
        name = vehicle or summary.loc[summary["vehicle_class"] != ALL].sort_values("n", ascending=False)[
            "vehicle_class"].iloc[0]
        e = estimate(km, name, stops, summary, rates)
        extra = max(stops - 2, 0)
        drop = ""
        if e["drop_fee"] and extra:
            sign = "-" if e["drop_fee"] < 0 else "+"
            drop = rf" {sign} {_tex_num(abs(e['drop_fee']))} \times {extra}"
        st.markdown(f"**Worked example: {name}, {km:,.0f} km, {stops} stops**"
                    + ("" if vehicle else " (the truck class with the most trips; each row in the table "
                                          "uses its own numbers)"))
        st.latex(rf"{_tex_num(e['base_fare'])} + {_tex_num(e['rate_per_km'], 2)} \times {_tex_num(km)}"
                 rf"{drop} = \mathbf{{{_tex_num(e['price'])}}}\ \text{{THB}}")

        st.markdown(
            f"- **Low – High** ({e['low']:,.0f} – {e['high']:,.0f} THB here): the normal range. About 8 in "
            f"10 past bills fell inside it once scaled to their own estimate.\n"
            f"- **Typical error** ({e['test_mdape_pct']:.0f}% here): how far off the model was on trips it "
            f"was never shown. Lower = more trustworthy.\n"
            f"- **Based on**: *Own trips* = the line is built from this truck class's own bills; "
            f"*Borrowed – few trips* = under 30 bills, so the all-trucks line is adjusted to fit. "
            f"Rough guide only.\n"
            f"- Reefer prices follow fixed per-route contracts, so their ranges are wider than for dry "
            f"trucks. For a route billed before, the real price in tab 1 beats this estimate.")


# ---------- column labels: unit in the header, meaning in the hover tooltip ----------

NumCol, TxtCol, DateCol = st.column_config.NumberColumn, st.column_config.TextColumn, st.column_config.DateColumn


def thb(label, help, decimals=0):
    return NumCol(f"{label} (THB)", help=help, format=f"%,.{decimals}f")


MEDAL_COLORS = {1: "#FFD700", 2: "#C0C0C0", 3: "#CD7F32"}   # gold, silver, bronze

# Fixed categorical order (never cycled/reordered) - validated for CVD-safe adjacent pairs.
CAT_PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
FUEL_BLUE = CAT_PALETTE[0]
MAX_FUEL_SERIES = 5


def medal_rank_style(df: pd.DataFrame):
    """Styler for a df with a 'rank' column: top 3 ranks get a gold/silver/bronze square badge."""
    def _cell(v):
        color = MEDAL_COLORS.get(v)
        if not color:
            return ""
        return (f"background-color: {color}; color: #1a1a1a; font-weight: 700; "
                f"text-align: center; border-radius: 3px;")
    return df.style.map(_cell, subset=["rank"])


VEHICLE_HELP = ("Truck class from the bill: 4W/6W/10W/18W/22W = number of wheels; reefer = refrigerated, "
                "dry = not refrigerated, head = tractor head pulling a trailer/container")

STATUS_HELP = ("Job status from the source report: OPEN = job order created, not necessarily run or billed "
               "(closest to 'quoted'); POSTED = job done, not yet sent for billing; CONFIRM = sent for "
               "billing, complete. The sidebar's status filter defaults to CONFIRM only, since OPEN/POSTED "
               "jobs may never have shipped and can skew prices.")

STATS_CFG = {   # price_stats(): one row per vehicle class
    "vehicle_class": TxtCol("Vehicle class", help=VEHICLE_HELP),
    "trips": NumCol("Trips (count)", help="Number of billed trips in this group", format="%d"),
    "median": thb("Typical price (median)", "Middle billed price: half the trips were billed less, half more"),
    "p10": thb("Low estimate (10th percentile)", "10% of trips were billed at or below this - the cheap end"),
    "p90": thb("High estimate (90th percentile)", "90% of trips were billed at or below this - the expensive end"),
    "min": thb("Min price", "Lowest billed price in this group"),
    "max": thb("Max price", "Highest billed price in this group"),
    "last_price": thb("Last price", "Price on the most recent trip - usually the current contract rate"),
    "last_date": DateCol("Last trip date", help="Ship date of the most recent trip", format="DD/MM/YYYY"),
    "median_km": NumCol("Median road km (km)", format="%.0f",
                        help="Google road distance, all legs summed for multi-stop trips"),
    "fuel_rate_low": NumCol("Fuel rate low (THB/litre)", format="%.2f",
                            help="Median bottom-of-band diesel rate billed on this route + vehicle class"),
    "fuel_rate_high": NumCol("Fuel rate high (THB/litre)", format="%.2f",
                             help="Median top-of-band diesel rate billed on this route + vehicle class - "
                                  "the rate normally used to invoice"),
}

TRIPS_CFG = {   # one row per billed trip
    "ship_date": DateCol("Ship date", help="วันที่ขึ้นสินค้า (column C)", format="DD/MM/YYYY"),
    "status": TxtCol("Status", help=STATUS_HELP),
    "customer": TxtCol("Customer", help="ชื่อลูกค้า (column K)"),
    "vehicle_class": TxtCol("Vehicle class", help=VEHICLE_HELP),
    "vehicle_type": TxtCol("Vehicle (as billed)", help="ประเภทรถ exactly as in the report (column AG)"),
    "origin_c": TxtCol("Origin", help="First stop of the route, spelling variants merged"),
    "origin_latlon": TxtCol("Origin lat, lon (°)", help="Latitude, longitude in decimal degrees (WGS84)"),
    "dest_c": TxtCol("Destination", help="Last stop of the route, spelling variants merged"),
    "dest_latlon": TxtCol("Destination lat, lon (°)", help="Latitude, longitude in decimal degrees (WGS84)"),
    "stops_txt": TxtCol("Stops (in order)", help="Every stop parsed from เส้นทางขนส่ง (column AB)"),
    "stop_count": NumCol("Stops (count)", help="2 = plain A→B; more = multi-drop", format="%d"),
    "total_km": NumCol("Road km (km)", format="%.1f",
                       help="Google Routes driving distance, sum of every leg A→B→C…"),
    "fuel_rate_low": NumCol("Fuel rate low (THB/litre)", format="%.2f",
                            help="Diesel-price band the rate is pegged to, from อัตราน้ำมัน(...) in the route "
                                 "(column AB) - bottom of the band, e.g. 30.00 for '30-30.99'"),
    "fuel_rate_high": NumCol("Fuel rate high (THB/litre)", format="%.2f",
                             help="Diesel-price band the rate is pegged to, from อัตราน้ำมัน(...) in the route "
                                  "(column AB) - top of the band, e.g. 30.99 for '30-30.99' - the rate actually "
                                  "billed"),
    "price": thb("Price", "ราคาขนส่ง (column T), excluding VAT and other charges", decimals=2),
    "contractor_cost": thb("Contractor cost", "ค่าใช้จ่ายผู้รับเหมา (column AI)", decimals=2),
    "job_order_no": TxtCol("Job order no.", help="เลขที่ใบสั่งปฏิบัติงาน (column B)"),
    "bill_no": TxtCol("Bill no.", help="เลขที่บิล (column A)"),
    "source_file": TxtCol("Source file", help="Which Report AR_AP workbook the row came from"),
}

PAIR_PRICE_COL = thb("Price", "Median billed price for this pair across all its history, after the "
                              "MAD-outlier filter above (if on) - the single representative price "
                              "for this pair", decimals=0)

PAIR_CFG = {   # pair_rollup(): one row per origin -> destination + vehicle class
    "rank": NumCol("Rank", format="%d", help="Position by avg monthly CV, worst first"),
    "origin_c": TxtCol("Origin", help="First stop of the route, spelling variants merged"),
    "dest_c": TxtCol("Destination", help="Last stop of the route, spelling variants merged"),
    "vehicle_class": TxtCol("Vehicle class", help=VEHICLE_HELP),
    "months": NumCol("Months with data (count)", format="%d",
                     help="Calendar months that had at least the minimum trip count set above"),
    "trips": NumCol("Trips (count)", format="%,d", help="Total billed trips across those months"),
    "price": PAIR_PRICE_COL,
    "avg_cv_pct": NumCol("Avg monthly CV (%)", format="%.1f%%",
                         help="Coefficient of variation (S.D. ÷ mean price) within each month, "
                              "averaged across months - higher means this pair's price swings around "
                              "more from trip to trip in a typical month"),
    "max_cv_pct": NumCol("Worst monthly CV (%)", format="%.1f%%",
                         help="The single month with the highest S.D. ÷ mean price for this pair"),
}

PAIR_MONTH_CFG = {   # pair_rollup() filtered to one calendar month
    "origin_c": TxtCol("Origin", help="First stop of the route, spelling variants merged"),
    "dest_c": TxtCol("Destination", help="Last stop of the route, spelling variants merged"),
    "vehicle_class": TxtCol("Vehicle class", help=VEHICLE_HELP),
    "trips": NumCol("N - trips this month", format="%,d",
                    help="Billed trips this pair had in the selected month - the sample size behind "
                         "the CV next to it. A high CV on a low N is easy to get by chance"),
    "price": PAIR_PRICE_COL,
    "mean": thb("Mean price", "Average billed price this month"),
    "median": thb("Median price", "Middle billed price this month"),
    "sd": thb("S.D.", "Standard deviation of billed price this month"),
    "cv_pct": NumCol("CV (%)", format="%.1f%%",
                     help="S.D. ÷ mean, as a percentage - how spread out prices were this month "
                          "relative to their average"),
}

MONTHLY_CFG = {   # monthly stats for one selected pair
    "month": TxtCol("Month"),
    "trips": NumCol("Trips (count)", format="%d", help="Billed trips this month"),
    "mean": thb("Mean price", "Average billed price this month"),
    "median": thb("Median price", "Middle billed price this month"),
    "sd": thb("S.D.", "Standard deviation of billed price this month"),
    "cv_pct": NumCol("CV (%)", format="%.1f%%",
                     help="S.D. ÷ mean, as a percentage - how spread out prices were this month "
                          "relative to their average"),
}

DEV_CFG = {   # trips for one selected pair, with % deviation from monthly median
    "ship_date": DateCol("Ship date", help="วันที่ขึ้นสินค้า (column C)", format="DD/MM/YYYY"),
    "customer": TxtCol("Customer", help="ชื่อลูกค้า (column K)"),
    "price": thb("Price", "ราคาขนส่ง (column T), excluding VAT and other charges", decimals=2),
    "month": TxtCol("Month"),
    "monthly_median": thb("Monthly median", "Median price of this pair + vehicle class in this calendar month"),
    "pct_dev": NumCol("% dev. from monthly median", format="%.1f%%",
                      help="(Price − monthly median) ÷ monthly median, as a percentage. "
                           "Positive = billed above the typical price that month, negative = below"),
    "is_mad_outlier": st.column_config.CheckboxColumn(
        "MAD outlier?",
        help="Flagged as a price outlier for this pair + vehicle class: |price − group median| exceeds "
             "3× a floored MAD-based scale, pooled across every month shown here"),
    "job_order_no": TxtCol("Job order no.", help="เลขที่ใบสั่งปฏิบัติงาน (column B)"),
}

MODEL_CFG = {   # model_table(): one row per vehicle class
    "vehicle_class": TxtCol("Vehicle class", help=VEHICLE_HELP),
    "estimate": thb("Estimate", "Model price = base fare + THB/km × km + drop fee × extra drops"),
    "low": thb("Low", "Cheap end of normal: 1 in 10 past trips was billed this far below its model price, or more"),
    "high": thb("High", "Expensive end of normal: 1 in 10 past trips was billed this far above its model price, or more"),
    "trips_in_model": NumCol("Trips in model (count)", format="%,d",
                             help="Billed trips with road km used to fit this vehicle class"),
    "method": TxtCol("Based on", help="Own trips = line built from this truck class's own bills (30+ trips). "
                                      "Borrowed – few trips = under 30 trips, so the all-trucks line is "
                                      "scaled to this class's prices; less reliable"),
    "holdout_error_%": NumCol("Typical error (%)", format="%.0f%%",
                              help="Median % gap between model and actual price on 20% of trips kept "
                                   "aside and not used to fit - how far off a typical estimate is"),
    "note": TxtCol("Note", help="'outside data range' = no trips this short/long for this vehicle; "
                                "treat the estimate as a rough guide"),
}


# ---------- data ----------

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


def reload_button():
    if st.button("Reload data"):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.rerun()


try:
    trips, km_warning = load_trips()
    locs = load_locations()
except Exception as e:   # the page is useless without these; say why instead of a traceback
    st.error(f"Could not load the trip data: {e}")
    st.caption("If the pipeline is running right now, wait for it to finish, then reload.")
    reload_button()
    st.stop()

# the price model is optional: historical route lookups (tab 1) still work without it
model_warning = None
try:
    summary, rates = load_model()
except Exception as e:   # historical lookups still work without the model
    summary, rates = None, None
    model_warning = f"Price model not loaded: {e}"


# ---------- helpers ----------

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


def model_table(km: float, stops: int) -> pd.DataFrame:
    rows = []
    for name in summary.sort_values("n", ascending=False)["vehicle_class"]:
        e = estimate(km, name, stops, summary, rates)
        rows.append({"vehicle_class": name, "estimate": e["price"], "low": e["low"], "high": e["high"],
                     "trips_in_model": int(e["n"]), "method": e["method"],
                     "holdout_error_%": e["test_mdape_pct"],
                     "note": "outside data range" if e["extrapolated"] else ""})
    return pd.DataFrame(rows)


# distance bins picked from the real spread of trips_clean.total_km: each covers a meaningful
# share of actual trips, using the midpoint as the representative km fed into estimate()
BIN_RANGES = [
    ("0-10", 5), ("11-20", 15), ("21-30", 25), ("31-50", 40), ("51-100", 75),
    ("101-150", 125), ("151-200", 175), ("201-300", 250), ("301-500", 400), ("500+", 650),
]


def bin_rate_table(stops: int = 2) -> pd.DataFrame:
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
def monthly_pair_stats(min_trips: int, drop_outliers: bool) -> pd.DataFrame:
    """One row per (origin, dest, vehicle class, month): trip count, mean, median, S.D., CV%.

    Months with fewer than min_trips billed trips are dropped - S.D. on 1-2 points isn't meaningful.
    """
    d = trips[(trips["price"] > 0) & trips["ship_date"].notna()].copy()
    if drop_outliers:
        d = d[~flag_price_outliers(d, ["origin_c", "dest_c", "vehicle_class"])]
    d["month"] = d["ship_date"].dt.to_period("M").astype(str)
    g = d.groupby(["origin_c", "dest_c", "vehicle_class", "month"])["price"]
    out = g.agg(trips="size", mean="mean", median="median", sd="std").reset_index()
    out["sd"] = out["sd"].fillna(0.0)
    out["cv_pct"] = out["sd"] / out["mean"] * 100
    return out[out["trips"] >= min_trips]


@st.cache_data(show_spinner="Computing per-pair price…")
def pair_price_stats(drop_outliers: bool) -> pd.DataFrame:
    """One row per (origin, dest, vehicle class): a single representative price for that pair -
    the median of all its billed prices, after the same MAD-outlier filter used elsewhere on this
    tab. This is a whole-history figure (no per-month minimum), unlike `monthly_pair_stats`.
    """
    d = trips[(trips["price"] > 0) & trips["ship_date"].notna()].copy()
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


def pair_deviations(origin: str, dest: str, vehicle_class: str, monthly: pd.DataFrame,
                     drop_outliers: bool) -> pd.DataFrame:
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


def leg_key(origin: str, dest: str) -> str:
    """Distance-cache key for one origin -> destination leg, built from rounded lat/lon."""
    o, d = locs.loc[origin], locs.loc[dest]
    return f"{distance.point_key(o['lat'], o['lon'])}|{distance.point_key(d['lat'], d['lon'])}"


def road_km(origin: str, dest: str) -> tuple[float | None, str]:
    """Road km between two canonical places: distance cache first, Routes API if asked."""
    try:
        o, d = locs.loc[origin], locs.loc[dest]
    except KeyError:
        return None, "place not in locations_master.csv"
    if not (o["lat"] and o["lon"] and d["lat"] and d["lon"]):
        missing = [n for n, r in [(origin, o), (dest, d)] if not (r["lat"] and r["lon"])]
        return None, "no coordinates yet for: " + ", ".join(missing)
    try:
        key = leg_key(origin, dest)
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


def need_model():
    """Shown wherever a model estimate is requested but data/model_summary.csv hasn't been built yet."""
    st.info("The price model is not built yet (no `data/model_summary.csv`). Finish geocoding, "
            "then run `python main.py --from distance`. Historical prices by route still work.")


def model_failed(e: Exception):
    """Shown wherever estimate()/model_table() raises - usually a stale or malformed model file."""
    st.error(f"Model estimate failed: {type(e).__name__}: {e}")
    st.caption("`data/model_summary.csv` may be from an older pipeline version - rerun "
               "`python main.py --from model`, then Reload data.")


# ---------- sidebar: what the data can do right now ----------

with st.sidebar:
    st.header("Data status")
    status_counts = trips["status"].value_counts()
    all_statuses = status_counts.index.tolist()
    default_statuses = ["CONFIRM"] if "CONFIRM" in all_statuses else all_statuses
    selected_statuses = st.multiselect(
        "Job status", all_statuses, default=default_statuses, help=STATUS_HELP,
        format_func=lambda s: f"{s} ({status_counts[s]:,})")
    if selected_statuses:
        trips = trips[trips["status"].isin(selected_statuses)]
    else:
        st.warning("No status selected - showing every trip regardless of status.")
    st.caption(f"{len(trips):,} of {status_counts.sum():,} trips shown after the status filter above.")
    st.metric("Trips with a route (2+ stops)", f"{len(trips):,}")
    dates = trips["ship_date"].dropna()
    if len(dates):
        st.caption(f"Ship dates {dates.min():%d %b %Y} – {dates.max():%d %b %Y}")
    geo = (locs["lat"] != "").sum()
    st.metric("Places with coordinates", f"{geo:,} / {len(locs):,}")
    st.write(("✅" if trips["total_km"].notna().any() else "⏳") + " road km per trip")
    st.write(("✅" if summary is not None else "⏳") + " price model")
    for w in (km_warning, model_warning):
        if w:
            st.warning(w)
    reload_button()


st.title("Transport price lookup")
tab_dashboard, tab_route, tab_km, tab_summary, tab_fuel = st.tabs(
    ["Dashboard", "By origin → destination", "By km", "Price variability", "Fuel rate"])

# ---------- tab 0: dashboard ----------

with tab_dashboard:
    st.info(
        "**Note:** Origin and Destination must be a real, geocodable address - ideally a Google Maps "
        "link or a place already saved in the location list. Estimated total km comes from the Google "
        "Maps Routes API and excludes live traffic conditions (same convention used everywhere else in "
        "this app). Use the map links below to check a point or a route before trusting its price."
    )
    st.caption("Car Type / Ori / Dest are the real combinations billed to date. Each Avg.Price is a "
               "different way of averaging the billed price - a plain mean, a 3-sigma-trimmed mean, "
               "and a modified-Z-trimmed mean - each recomputed after dropping its own outliers. "
               "N is the trip count that mean is based on.")

    dash_f1, dash_f2 = st.columns(2)
    dash_vehicle_opts = sorted(trips["vehicle_class"].dropna().unique())
    dash_customer_opts = sorted(trips["customer"].dropna().unique())
    dash_vehicle = dash_f1.multiselect(
        "Vehicle type", dash_vehicle_opts, default=dash_vehicle_opts,
        help="Show only these truck classes. All selected by default.")
    dash_customer = dash_f2.multiselect(
        "Customer", dash_customer_opts, default=dash_customer_opts,
        help="Show only routes billed to these customers. All selected by default.")

    def stats_row(values: np.ndarray) -> dict:
        s = cutoff_price_stats(values)
        return {"Avg.Price (before cut-off)": s["avg"], "N (before cut-off)": s["n"],
                "Avg.Price (3-sigma, after cut-off)": s["avg_3sigma"], "N (3-sigma, after cut-off)": s["n_3sigma"],
                "Avg.Price (modi-Z, after cut-off)": s["avg_modiz"], "N (modi-Z, after cut-off)": s["n_modiz"]}

    def dash_column_config(price_help: str, decimals: int = 0) -> dict:
        price_cols = ["Avg.Price (before cut-off)", "Avg.Price (3-sigma, after cut-off)",
                      "Avg.Price (modi-Z, after cut-off)"]
        n_cols = ["N (before cut-off)", "N (3-sigma, after cut-off)", "N (modi-Z, after cut-off)"]
        cfg = {label: NumCol(label, help=price_help, format=f"%,.{decimals}f") for label in price_cols}
        cfg.update({label: NumCol(label, help="Trips that mean is based on.", format="%d") for label in n_cols})
        return cfg

    def maps_route_url(o_latlon: str, d_latlon: str) -> str | None:
        """o_latlon/d_latlon are 'lat, lon' (from the geocode cache) or '' if never geocoded."""
        if not o_latlon or not d_latlon:
            return None
        o_lat, o_lon = (x.strip() for x in o_latlon.split(","))
        d_lat, d_lon = (x.strip() for x in d_latlon.split(","))
        return (f"https://www.google.com/maps/dir/?api=1&origin={o_lat},{o_lon}"
                f"&destination={d_lat},{d_lon}&travelmode=driving")

    def gas_divergence_value(g: pd.DataFrame) -> tuple[float, float, float] | None:
        """(diff, pct, eppo_base) median (billed top-of-band fuel rate) - (EPPO HSD B7 price on that
        trip's ship date), THB/litre and as a % of the EPPO price, plus the EPPO base price itself.
        None where no row has both sides present."""
        mask = g["fuel_rate_high"].notna() & g["eppo_price"].notna()
        if not mask.any():
            return None
        base = g.loc[mask, "eppo_price"].median()
        diff = (g.loc[mask, "fuel_rate_high"] - g.loc[mask, "eppo_price"]).median()
        pct = diff / base * 100 if base else np.nan
        return diff, pct, base

    def gas_divergence_resolved(
        g: pd.DataFrame, fallback: tuple[float, float, float] | None = None
    ) -> tuple[float, float, float, bool] | None:
        """(diff, pct, base, estimated) for this route, falling back to a same-car-type/global
        estimate when this route has no trip with both a billed fuel rate and an EPPO price."""
        own = gas_divergence_value(g)
        if own is not None:
            return (*own, False)
        if fallback is not None:
            return (*fallback, True)
        return None

    def gas_price_base_text(resolved: tuple[float, float, float, bool] | None) -> str | None:
        if resolved is None:
            return None
        _, _, base, estimated = resolved
        return f"{base:.2f}{' (est.)' if estimated else ''}"

    def gas_divergence_text(resolved: tuple[float, float, float, bool] | None) -> str | None:
        if resolved is None:
            return None
        diff, pct, _, estimated = resolved
        sign = "+" if diff >= 0 else ""
        return f"{sign}{diff:.2f} ({sign}{pct:.1f}%){' (est.)' if estimated else ''}"

    OTHER_CFG = {
        "Gas Price Range": TxtCol(
            "Gas Price Range",
            help="Median fuel-rate band (THB/litre) billed on this route - partial coverage, blank "
                 "where no trip on this route/class carries a fuel clause."),
        "EPPO Base Price": TxtCol(
            "EPPO Base Price",
            help="Median EPPO published HSD B7 diesel retail price (THB/litre) nearest each trip's ship "
                 "date (data/eppo_diesel_hsd_b7.json, run scripts/fetch_eppo_diesel.py to update) - the "
                 "base the divergence next to it is measured against. Where the route has no billed fuel "
                 "clause at all, this falls back to the same car type's median (or the overall median if "
                 "that car type has none either), marked '(est.)'."),
        "Gas Price Divergence": TxtCol(
            "Gas Price Divergence",
            help="Median gap between the billed top-of-band fuel rate and the EPPO base price (see "
                 "'EPPO Base Price' column) - THB/litre and as a % of the EPPO price. Positive = billed "
                 "above the published market price. Where the route has no billed fuel clause at all, "
                 "this falls back to the same car type's median divergence (or the overall median if "
                 "that car type has none either), marked '(est.)'; every trip otherwise gets EPPO's "
                 "nearest published reading, even past the newest date EPPO has published so far."),
        "Existing pair price (min-max)": TxtCol(
            "Existing pair price (min-max)",
            help="Min-max range actually billed on this route. The alternative calculation method - "
                 "estimated km (from the geocoded addresses) x model-estimated price/km, for a car "
                 "type / route with no billed history yet - is not wired up here."),
    }
    MAP_CFG = {
        "Route Map": st.column_config.LinkColumn(
            "Route Map", help="Open the full driving route (origin → destination) on Google Maps.",
            display_text="🛣️ Open"),
        "Total KM": NumCol("Total KM", help="Estimated road distance (median across trips on this route, "
                                            "Google Routes API, excludes live traffic).", format="%,.0f"),
    }

    # Known bad data: bill_no 2607000163 (TUF -> "DC Makro Mahachai") has origin/destination
    # geocoded to the wrong "DC Makro Mahachai" candidate (a retail mall ~1km from origin,
    # instead of the actual distribution center ~10km+ away - see geocode_cache.json, which lists
    # both candidates). total_km for that leg is computed as ~1.05km against a real billed price
    # of 2016-5376 THB, producing 1900-5100 THB/km outlier ratios that this bill repeats 30+ times
    # (recurring contract), dominating the untrimmed 4W/6W reefer per-km averages below. The raw
    # rows are kept in trips_clean.csv untouched; they are only excluded here from the price/km
    # stats until the geocode is corrected at the source.
    BAD_GEOCODE_BILL_NOS = {"2607000163"}

    priced = trips[trips["vehicle_class"].notna() & (trips["origin_c"] != "") & (trips["dest_c"] != "")
                   & (trips["price"] > 0) & ~trips["bill_no"].astype(str).isin(BAD_GEOCODE_BILL_NOS)]
    if dash_vehicle:
        priced = priced[priced["vehicle_class"].isin(dash_vehicle)]
    if dash_customer:
        priced = priced[priced["customer"].isin(dash_customer)]
    global_gas_divergence = gas_divergence_value(priced)
    vehicle_gas_divergence = {
        vc: gas_divergence_value(g) for vc, g in priced.groupby("vehicle_class")
    }
    route_row_list = []
    for (vc, o, d), g in priced.groupby(["vehicle_class", "origin_c", "dest_c"]):
        sr = stats_row(g["price"].to_numpy())
        fallback = vehicle_gas_divergence.get(vc) or global_gas_divergence
        resolved = gas_divergence_resolved(g, fallback=fallback)
        route_row_list.append({
            "Car Type": vc, "Ori": o, "Dest": d,
            "Total KM": g["total_km"][g["total_km"] > 0].median(),
            "Route Map": maps_route_url(g["origin_latlon"].iloc[0], g["dest_latlon"].iloc[0]),
            **sr,
            "Existing pair price (min-max)": f"{g['price'].min():,.0f}-{g['price'].max():,.0f}",
            "Gas Price Range": (f"{g['fuel_rate_low'].median():.2f}-{g['fuel_rate_high'].median():.2f}"
                                if g["fuel_rate_low"].notna().any() and g["fuel_rate_high"].notna().any()
                                else None),
            "EPPO Base Price": gas_price_base_text(resolved),
            "Gas Price Divergence": gas_divergence_text(resolved),
        })
    if route_row_list:
        route_rows = pd.DataFrame(route_row_list).sort_values(
            ["Car Type", "Ori", "N (before cut-off)"], ascending=[True, True, False]).reset_index(drop=True)
    else:
        route_rows = pd.DataFrame(route_row_list)

    def diverging_mask(df: pd.DataFrame, tol: float = 0.005, margin: float | None = None) -> pd.Series:
        """True where a cut-off mean moved away from the before-cut-off mean by more than either a
        relative tolerance (tol, default 0.5%) or an absolute margin (e.g. 20 THB/km) - i.e. that
        row actually had outliers trimmed, as opposed to a flat/too-small-to-trim group. `margin` suits
        per-km THB values better than a relative %, since a THB/km gap is meaningful at a fixed size
        regardless of whether the average itself is small or large."""
        before = df["Avg.Price (before cut-off)"]
        threshold = margin if margin is not None else tol * before.abs().clip(lower=1)
        moved = pd.Series(False, index=df.index)
        for col in ["Avg.Price (3-sigma, after cut-off)", "Avg.Price (modi-Z, after cut-off)"]:
            moved |= (df[col] - before).abs() > threshold
        return moved

    def with_summary_row(df: pd.DataFrame) -> pd.DataFrame:
        """Append a SUMMARY row: total trips + trip-weighted average price per method, overall trimmed %."""
        if df.empty:
            return df
        summary = {"Car Type": f"SUMMARY ({len(df):,} routes)"}
        for avg_col, n_col in [("Avg.Price (before cut-off)", "N (before cut-off)"),
                                ("Avg.Price (3-sigma, after cut-off)", "N (3-sigma, after cut-off)"),
                                ("Avg.Price (modi-Z, after cut-off)", "N (modi-Z, after cut-off)")]:
            n_sum = df[n_col].sum()
            summary[n_col] = n_sum
            summary[avg_col] = (df[avg_col] * df[n_col]).sum() / n_sum if n_sum else np.nan
        return pd.concat([df, pd.DataFrame([summary])], ignore_index=True)

    def gas_divergence_pct(df: pd.DataFrame) -> pd.Series:
        """Parsed +/-% out of the 'Gas Price Divergence' text cell, NaN where blank/absent."""
        if "Gas Price Divergence" not in df.columns:
            return pd.Series(np.nan, index=df.index)
        return (df["Gas Price Divergence"].fillna("")
                .str.extract(r"\(([+-]?[\d.]+)%\)", expand=False).astype(float))

    def highlight_diverging(df: pd.DataFrame, margin: float | None = None):
        moved = diverging_mask(df, margin=margin)
        gas_pct = gas_divergence_pct(df)
        gas_col = "Gas Price Divergence"
        pct_tol = 5.0
        def _row(row):
            if str(row["Car Type"]).startswith("SUMMARY"):
                return [""] * len(row)
            styles = ["background-color: #fff3b0" if moved.loc[row.name] else "" for _ in row.index]
            if gas_col in row.index:
                pct = gas_pct.loc[row.name]
                if pd.notna(pct) and abs(pct) > pct_tol:
                    color = "#ffadad" if pct > 0 else "#b9f6ca"  # red = billed above EPPO, green = below
                    styles[row.index.get_loc(gas_col)] = f"background-color: {color}"
            return styles
        return df.style.apply(_row, axis=1)

    if route_rows.empty:
        st.info("No billed trips match this vehicle type / customer combination.")
    else:
        f1, f2 = st.columns(2)
        diverge_only = f1.checkbox("Show only diverging rows (cut-off actually trimmed something)")
        fuel_only = f2.checkbox("Only routes with fuel-range data")
        if diverge_only:
            route_rows = route_rows[diverging_mask(route_rows)].reset_index(drop=True)
        if fuel_only:
            route_rows = route_rows[route_rows["Gas Price Range"].notna()].reset_index(drop=True)

        route_rows = with_summary_row(route_rows)
        dup_car = route_rows["Car Type"] == route_rows["Car Type"].shift()
        dup_ori = dup_car & (route_rows["Ori"] == route_rows["Ori"].shift())
        route_rows.loc[dup_ori & (route_rows["Ori"].notna()), "Ori"] = ""
        route_rows.loc[dup_car & ~route_rows["Car Type"].str.startswith("SUMMARY"), "Car Type"] = ""

        st.caption(f"{len(route_rows) - 1:,} routes shown. 🟡 Highlighted rows are where a cut-off method actually "
                   "trimmed something - its mean moved from the before-cut-off mean by more than 0.5%. Gas Price "
                   "Divergence cells are highlighted 🔴 red where the billed fuel rate is more than 5% above "
                   "EPPO's published price, or 🟢 green where it's more than 5% below. Last row is a "
                   "trip-weighted summary across every route currently shown.")
        st.dataframe(highlight_diverging(route_rows), hide_index=True, width="stretch",
                     column_config={**dash_column_config("THB per trip, after each method's outlier cutoff."),
                                    **OTHER_CFG, **MAP_CFG})

    st.subheader("Fitted price model (base fare + rate/km)")
    st.caption("From `data/model_summary.csv`: price = base_fare + rate_per_km × km + drop_fee × extra "
               "stops, fit per vehicle class on CONFIRM trips (trimmed least squares, so a few odd bills "
               "don't bend the line). **rate_per_km is the real marginal cost per km** - unlike the flat "
               "'price ÷ km' averages below, it isn't distorted by mixing short and long trips (a fixed "
               "base fare divided by a small vs. large distance gives wildly different ratios even at a "
               "constant rate_per_km).")
    st.info("⚠️ **Check R² before trusting a row.** It shows how much of the price this class's fit "
            "actually explains - low R² means lots of scatter the model doesn't capture, so treat its "
            "rate_per_km as rough.")
    if summary is None:
        need_model()
    else:
        model_rows = summary[summary["vehicle_class"] != ALL].copy()
        if dash_vehicle:
            model_rows = model_rows[model_rows["vehicle_class"].isin(dash_vehicle)]
        model_rows = model_rows.sort_values("vehicle_class").reset_index(drop=True)
        if model_rows.empty:
            st.info("No fitted model for this vehicle type selection.")
        else:
            st.dataframe(
                model_rows[["vehicle_class", "n", "base_fare", "rate_per_km", "drop_fee",
                            "r2", "km_min", "km_max"]],
                hide_index=True, width="stretch",
                column_config={
                    "vehicle_class": TxtCol("Car Type"),
                    "n": NumCol("N", help="Trips available for this class (before outlier trimming).",
                                format="%d"),
                    "base_fare": NumCol("Base fare (THB)", help="Fixed cost per trip, before distance.",
                                        format="%,.0f"),
                    "rate_per_km": NumCol("Rate per km (THB/km)",
                                          help="Marginal cost per extra km - the real 'price per km'.",
                                          format="%.2f"),
                    "drop_fee": NumCol("Extra-stop fee (THB)",
                                       help="Added per stop beyond a plain A→B trip.", format="%,.0f"),
                    "r2": NumCol("R²", help="How well distance (+ stops) explains price for this class "
                                 "(1.0 = perfect fit; low = lots of scatter the model doesn't capture).",
                                 format="%.2f"),
                    "km_min": NumCol("Km min", help="Shortest trip this fit was built from.", format="%.0f"),
                    "km_max": NumCol("Km max", help="Longest trip this fit was built from.", format="%.0f"),
                })

    st.subheader("Per KM")
    per_km_src = priced[priced["total_km"] > 0].copy()
    per_km_src["price_per_km"] = per_km_src["price"] / per_km_src["total_km"]
    per_km_row_list = [{"Car Type": vc, **stats_row(g["price_per_km"].to_numpy())}
                       for vc, g in per_km_src.groupby("vehicle_class")]
    if per_km_row_list:
        per_km_rows = pd.DataFrame(per_km_row_list).sort_values("Car Type").reset_index(drop=True)
        st.caption("🟡 Highlighted rows are where a cut-off method actually trimmed something - its mean "
                   "moved from the before-cut-off mean by more than 20 THB/km, i.e. this vehicle class has "
                   "a wide outlier gap between its raw and trimmed averages.")
        st.dataframe(highlight_diverging(per_km_rows, margin=20), hide_index=True, width="stretch",
                     column_config=dash_column_config("THB per km, after each method's outlier cutoff.",
                                                       decimals=2))
    else:
        st.info("No trips with road km match this vehicle type / customer combination.")

    st.subheader("Route builder (demo)")
    st.caption(
        "Demo only - pick an origin and destination, then add stop points in between. Each stop has a "
        "**Can use** toggle to mark it usable or not; nothing here feeds the price model yet, it's just "
        "a preview of the route order and the map link.")

    if "dash_stops" not in st.session_state:
        st.session_state.dash_stops = []

    place_opts = sorted(locs.index)
    r1, r2 = st.columns(2)
    demo_origin = r1.selectbox("Origin", place_opts, index=None, placeholder="Type to search…", key="dash_origin")
    demo_dest = r2.selectbox("Destination", place_opts, index=None, placeholder="Type to search…", key="dash_dest")

    if st.button("+ Add stop point"):
        st.session_state.dash_stops.append({"place": None, "usable": True})
        st.rerun()

    remove_idx = None
    for i, stop in enumerate(st.session_state.dash_stops):
        s1, s2, s3 = st.columns([5, 2, 1])
        cur_index = place_opts.index(stop["place"]) if stop["place"] in place_opts else None
        stop["place"] = s1.selectbox(f"Stop {i + 1}", place_opts, index=cur_index,
                                     placeholder="Type to search…", key=f"dash_stop_{i}")
        stop["usable"] = s2.checkbox("Can use", value=stop["usable"], key=f"dash_stop_usable_{i}")
        if s3.button("✕", key=f"dash_stop_remove_{i}", help="Remove this stop"):
            remove_idx = i
    if remove_idx is not None:
        st.session_state.dash_stops.pop(remove_idx)
        st.rerun()

    if demo_origin and demo_dest:
        used_stops = [s for s in st.session_state.dash_stops if s["place"]]
        route_places = [demo_origin] + [s["place"] for s in used_stops] + [demo_dest]
        usable_flags = [True] + [s["usable"] for s in used_stops] + [True]
        st.write(" → ".join(f"{p}" + ("" if u else " 🚫 can't use") for p, u in zip(route_places, usable_flags)))

        if not all(usable_flags):
            st.warning("One or more stops are marked **Can't use** - map link disabled until every "
                       "stop is usable (demo rule only).")
        else:
            coords = []
            for p in route_places:
                row = locs.loc[p]
                if row["lat"] and row["lon"]:
                    coords.append((row["lat"], row["lon"]))
            if len(coords) != len(route_places):
                st.caption("Some stops don't have coordinates yet, so the map link can't be built.")
            else:
                url = (f"https://www.google.com/maps/dir/?api=1&origin={coords[0][0]},{coords[0][1]}"
                      f"&destination={coords[-1][0]},{coords[-1][1]}&travelmode=driving")
                if len(coords) > 2:
                    url += "&waypoints=" + "|".join(f"{a},{b}" for a, b in coords[1:-1])
                st.link_button("🛣️ Open route on Google Maps", url)

# ---------- tab 1: origin -> destination ----------

with tab_route:
    # --- origin / destination pickers ---
    c1, c2, c3 = st.columns([5, 5, 2])
    counts_o = trips["origin_c"].value_counts()
    origin = c1.selectbox("Origin", counts_o.index, index=None, placeholder="Type to search…",
                          format_func=lambda p: f"{p}  ({counts_o[p]:,} trips)")
    both_ways = c3.checkbox("Include return direction", value=False)
    only_known = c3.checkbox("Only destinations with history", value=True)

    # destination list depends on the origin + the two checkboxes above
    if origin and only_known:
        sub = trips[trips["origin_c"] == origin]
        if both_ways:
            back = trips[trips["dest_c"] == origin].rename(columns={"origin_c": "dest_c", "dest_c": "origin_c"})
            sub = pd.concat([sub, back])
        dest_opts = sub["dest_c"].value_counts()
    else:
        dest_opts = pd.concat([trips["dest_c"], trips["origin_c"]]).value_counts()
    dest = c2.selectbox("Destination", dest_opts.index, index=None, placeholder="Type to search…",
                        format_func=lambda p: f"{p}  ({dest_opts[p]:,})")

    if origin and dest:
        # match trips against the chosen pair, plus the reverse leg if requested
        hit = (trips["origin_c"] == origin) & (trips["dest_c"] == dest)
        if both_ways:
            hit |= (trips["origin_c"] == dest) & (trips["dest_c"] == origin)
        found = trips[hit]

        if len(found):
            # --- historical trips found: show summary + full trip list ---
            f1, f2 = st.columns(2)
            vc_opts = sorted(found["vehicle_class"].dropna().unique())
            cu_opts = sorted(found["customer"].dropna().unique())
            vc = f1.multiselect("Vehicle class", vc_opts, default=vc_opts)
            cu = f2.multiselect("Customer", cu_opts, default=cu_opts)
            if vc:
                found = found[found["vehicle_class"].isin(vc)]
            if cu:
                found = found[found["customer"].isin(cu)]

            st.success(f"{len(found):,} historical trips on this route")
            st.subheader("Price by vehicle class")
            st.caption("What was **actually billed** on this route, one row per truck class. Prices in THB "
                       "per trip (ราคาขนส่ง, column T), km = Google road distance. **Typical price (median)** is "
                       "the middle price; **Low–High estimate (P10–P90)** is the normal range (the cheapest "
                       "and dearest 10% left out); "
                       "**Last price** is the most recent bill, usually the current contract rate. "
                       "**Fuel rate low/high** is the median diesel-price band billed here (partial coverage - "
                       "blank where no trip on this route/class carries a fuel clause). "
                       "Hover a column header for its exact meaning.")
            st.dataframe(price_stats(found), hide_index=True, width="stretch", column_config=STATS_CFG)
            if found["customer"].nunique() > 1:
                st.caption("Several customers use this route - filter by customer, contract rates may differ.")

            st.subheader("Trips")
            st.caption("Every billed trip on this route, newest first. Price and contractor cost in THB per "
                       "trip; road km in km (all legs summed for multi-stop trips); lat, lon in decimal "
                       "degrees.")
            cols = ["ship_date", "status", "customer", "vehicle_class", "vehicle_type", "origin_c",
                    "origin_latlon", "dest_c", "dest_latlon", "stops_txt", "stop_count", "total_km",
                    "fuel_rate_low", "fuel_rate_high", "price", "contractor_cost", "job_order_no", "bill_no",
                    "source_file"]
            st.dataframe(with_total(found.sort_values("ship_date", ascending=False)[cols],
                                    ["total_km", "price", "contractor_cost"], label_col="customer"),
                         hide_index=True, width="stretch", column_config=TRIPS_CFG)
            st.caption("Last row **TOTAL** = sum of road km, price and contractor cost over all trips "
                       "above (THB and km). Sorting by a column header moves it; click again to reset.")
        else:
            st.warning("No trips billed on this route yet - estimating from road km.")

        # --- model estimate: shown for new routes, and on request for known ones ---
        if len(found) == 0 or st.toggle("Also show model estimate", value=False):
            km, src = road_km(origin, dest)
            if src == "ask":
                st.write("This leg is not in the distance cache.")
                key_btn = st.button("Get road km from Google Routes (1 API call)")
                if key_btn:
                    st.session_state[f"allow_api_{leg_key(origin, dest)}"] = True
                    st.rerun()
                km = st.number_input("…or type the km yourself", min_value=0.0, value=None, step=10.0)
                src = "typed in" if km else src
            elif km is None:
                st.write(f"Can't get road km: {src}.")
                km = st.number_input("Type the km yourself", min_value=0.0, value=None, step=10.0)
                src = "typed in"
            if km:
                st.write(f"**Road km: {km:,.1f}** ({src})")
                if summary is None:
                    need_model()
                else:
                    try:
                        formula_card(km, 2, None)
                        st.dataframe(model_table(km, 2), hide_index=True, width="stretch",
                                     column_config=MODEL_CFG)
                    except Exception as e:
                        model_failed(e)

# ---------- tab 2: km -> price ----------

with tab_km:
    # --- inputs: km, vehicle class, stop count ---
    c1, c2, c3 = st.columns(3)
    km = c1.number_input("Road distance (km)", min_value=1.0, value=100.0, step=10.0,
                         help="Driving distance in km; for multi-stop trips add up every leg A→B→C")
    classes = (summary.loc[summary["vehicle_class"] != ALL].sort_values("n", ascending=False)["vehicle_class"].tolist()
               if summary is not None else sorted(trips["vehicle_class"].dropna().unique()))
    veh = c2.selectbox("Vehicle class", ["All classes"] + classes)
    stops = c3.number_input("Stops incl. origin (count)", min_value=2, value=2, step=1,
                            help="2 = plain A→B trip; each extra stop adds the fitted per-drop fee")

    # --- model estimate for the chosen km / class / stops ---
    if summary is None:
        need_model()
    else:
        try:
            est = model_table(km, stops)
            if veh != "All classes":
                e = estimate(km, veh, stops, summary, rates)
                m1, m2, m3 = st.columns(3)
                m1.metric("Estimated price (THB/trip)", f"{e['price']:,.0f}",
                          help="Model price for one trip of this km with this truck class")
                m2.metric("Typical range (THB/trip)", f"{e['low']:,.0f} – {e['high']:,.0f}",
                          help="Normal spread of real bills around the model price (low = 10th percentile, high = 90th percentile)")
                m3.metric("Typical error (%)", f"{e['test_mdape_pct']:.0f}%",
                          help="Median % gap between model and actual price on trips the model never saw")
                drops = f" + {e['drop_fee']:,.0f} per extra drop" if e["drop_fee"] else ""
                st.caption(f"{e['base_fare']:,.0f} + {e['rate_per_km']:.2f} THB/km{drops} · "
                           f"fitted on {int(e['n']):,} trips, {e['km_min']:.0f}–{e['km_max']:.0f} km")
                if e["extrapolated"]:
                    st.warning("This km is outside the range of trips for this vehicle - treat as a rough guide.")
                est = est[est["vehicle_class"] == veh]
            formula_card(km, stops, None if veh == "All classes" else veh)
            st.dataframe(est, hide_index=True, width="stretch", column_config=MODEL_CFG)
        except Exception as e:
            model_failed(e)

    # --- reality check: real billed trips of a similar km + stop count ---
    st.subheader(f"Real trips between {km * 0.9:,.0f} and {km * 1.1:,.0f} km")
    if trips["total_km"].notna().any():
        near = trips[trips["total_km"].between(km * 0.9, km * 1.1)]
        if veh != "All classes":
            near = near[near["vehicle_class"] == veh]
        near = near[near["stop_count"] == stops]
        st.caption(f"A reality check on the estimate: what was **actually billed** for trips of "
                   f"{km * 0.9:,.0f}–{km * 1.1:,.0f} km road distance with {stops} stops. Prices in THB per "
                   f"trip. If the model estimate sits far outside the low–high estimate here, trust these real "
                   f"prices more.")
        st.dataframe(price_stats(near), hide_index=True, width="stretch", column_config=STATS_CFG)
        st.caption("The individual trips behind the summary above, newest first (THB per trip, road km).")
        cols = ["ship_date", "customer", "vehicle_class", "origin_c", "origin_latlon", "dest_c",
                "dest_latlon", "stops_txt", "total_km", "price"]
        st.dataframe(near.sort_values("ship_date", ascending=False)[cols], hide_index=True,
                     width="stretch", column_config=TRIPS_CFG)
    else:
        st.info("No road km per trip yet - this cross-check appears once the distance step has run.")

# ---------- tab 3: price variability ----------

with tab_summary:
    st.caption("How much billed price swings from trip to trip on the same route + vehicle class, "
               "within a calendar month. A route billed the same way every time should have a low "
               "S.D. and CV; a route with mixed contracts or one-off charges will have a high one.")
    min_trips = st.number_input("Minimum trips per pair per month", min_value=2, value=3, step=1,
                                help="Months with fewer billed trips than this are left out - S.D. on "
                                     "1-2 points isn't meaningful")
    drop_outliers = st.checkbox(
        "Drop MAD-based price outliers before computing spread", value=True,
        help="For each origin → destination + vehicle class, flags trips whose price is more than "
             "3× a floored MAD-based scale away from that group's median price, then leaves them out "
             "of the S.D./CV numbers below - a few odd bills shouldn't make a route look more volatile "
             "than it is. Flagged trips are still visible (not deleted) in the per-trip table further down.")
    all_trips_for_variability = trips[(trips["price"] > 0) & trips["ship_date"].notna()]
    n_flagged = int(flag_price_outliers(all_trips_for_variability,
                                        ["origin_c", "dest_c", "vehicle_class"]).sum())
    st.caption(f"{n_flagged:,} of {len(all_trips_for_variability):,} priced trips flagged as MAD "
               f"outliers{' and left out below' if drop_outliers else ' (shown below, not left out)'}.")
    monthly = monthly_pair_stats(int(min_trips), drop_outliers)
    price_stats = pair_price_stats(drop_outliers)

    if monthly.empty:
        st.info("No pair + month has enough trips at this threshold yet - lower the minimum above.")
    else:
        # --- ranking table: all pairs, either averaged or for one selected month ---
        st.subheader("Pairs ranked by monthly price deviation")
        month_opts = ["All months (averaged)"] + sorted(monthly["month"].unique(), reverse=True)
        rollup_month = st.selectbox("Month", month_opts, key="rollup_month",
                                    help="Averaging across months can hide that a high CV came from a "
                                         "month with very few trips. Pick a month to see each pair's "
                                         "actual N (trips) and CV for that month alone.")
        if rollup_month == "All months (averaged)":
            st.caption("One row per origin → destination + vehicle class. **Price** is that pair's "
                       "single representative price - the median across its whole history (after the "
                       "outlier filter above, if on). **Avg monthly CV** is the "
                       "coefficient of variation (S.D. ÷ mean price) averaged across that pair's months - "
                       "the higher it is, the more this pair's price swings around from trip to trip in a "
                       "typical month. **Worst monthly CV** is its single worst month.")
            rollup = pair_rollup(monthly, price_stats)
            st.dataframe(medal_rank_style(rollup), hide_index=True, width="stretch", column_config=PAIR_CFG)
        else:
            st.caption("One row per origin → destination + vehicle class, for this month only. "
                       "**N** is how many billed trips that pair had this month - check it before "
                       "trusting a high CV. **Price** is that pair's overall representative price "
                       "(median across its whole history, after the outlier filter above) - compare "
                       "it against **Mean**/**Median** to see how this month's price stacks up.")
            month_rollup = monthly[monthly["month"] == rollup_month] \
                .merge(price_stats, on=["origin_c", "dest_c", "vehicle_class"], how="left") \
                .sort_values("cv_pct", ascending=False) \
                [["origin_c", "dest_c", "vehicle_class", "trips", "price", "mean", "median", "sd", "cv_pct"]]
            st.dataframe(month_rollup, hide_index=True, width="stretch", column_config=PAIR_MONTH_CFG)

        # --- drill-down: pick one pair + vehicle class to see its monthly trend and per-trip detail ---
        st.subheader("Monthly S.D. and CV, and per-trip deviation, for one pair")
        r1, r2, r3 = st.columns(3)
        origin = r1.selectbox("Origin", sorted(monthly["origin_c"].unique()), index=None,
                              placeholder="Type to search…", key="var_origin")
        dest_opts = sorted(monthly.loc[monthly["origin_c"] == origin, "dest_c"].unique()) if origin else []
        dest = r2.selectbox("Destination", dest_opts, index=None, placeholder="Type to search…",
                            key="var_dest")
        veh_opts = (sorted(monthly.loc[(monthly["origin_c"] == origin) & (monthly["dest_c"] == dest),
                                       "vehicle_class"].unique()) if origin and dest else [])
        veh = r3.selectbox("Vehicle class", veh_opts, index=None, key="var_veh")

        if origin and dest and veh:
            pair_monthly = monthly[(monthly["origin_c"] == origin) & (monthly["dest_c"] == dest)
                                   & (monthly["vehicle_class"] == veh)].sort_values("month")
            st.dataframe(pair_monthly[["month", "trips", "mean", "median", "sd", "cv_pct"]],
                        hide_index=True, width="stretch", column_config=MONTHLY_CFG)

            st.caption("Every trip on this pair + vehicle class, with its price's % deviation from the "
                       "median price billed that same month. Positive = above that month's typical "
                       "price, negative = below.")
            st.dataframe(pair_deviations(origin, dest, veh, monthly, drop_outliers), hide_index=True,
                        width="stretch", column_config=DEV_CFG)

# ---------- tab 4: fuel rate ----------

with tab_fuel:
    st.caption("The diesel-price band each route's rate is pegged to, parsed from อัตราน้ำมัน(…) in the route "
               "text (column AB) - top of the band, e.g. **30.99** THB/litre for '30-30.99'. Not every route "
               "carries a fuel clause, so coverage is partial.")
    fd = trips[trips["fuel_rate_high"].notna() & trips["ship_date"].notna()]

    if fd.empty:
        st.info("No trips have a parsed fuel rate yet.")
    else:
        # --- headline metrics ---
        m1, m2, m3 = st.columns(3)
        m1.metric("Trips with a fuel rate", f"{len(fd):,}",
                  help=f"{len(fd) / len(trips):.0%} of all trips with a route")
        m2.metric("Customers with a fuel clause", f"{fd['customer'].nunique():,}",
                  help=f"of {trips['customer'].nunique():,} customers total")
        m3.metric("Date range", f"{fd['ship_date'].min():%b %Y} – {fd['ship_date'].max():%b %Y}")

        f1, f2 = st.columns([3, 2])
        fd_customers = sorted(fd["customer"].dropna().unique())
        cmp_customers = f1.multiselect(
            "Compare specific customers (optional)", fd_customers, max_selections=MAX_FUEL_SERIES,
            help="Each customer's contract can reference a different band. Leave empty for the overall "
                 "trend across every customer pooled together.")
        veh_opts = sorted(fd["vehicle_class"].dropna().unique())
        veh_filter = f2.multiselect("Vehicle class", veh_opts, default=veh_opts)
        if veh_filter:
            fd = fd[fd["vehicle_class"].isin(veh_filter)]

        # --- time series: per-customer lines, or pooled median + min/max band ---
        st.subheader("Fuel rate over time")
        if cmp_customers:
            g = fuel_rate_monthly(fd[fd["customer"].isin(cmp_customers)], "customer")
            if g.empty:
                st.info("No fuel-rate data for that selection.")
            else:
                chart = alt.Chart(g).mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=40)).encode(
                    x=alt.X("month:T", title="Month"),
                    y=alt.Y("median:Q", title="Fuel rate (THB/litre)"),
                    color=alt.Color("customer:N", title="Customer",
                                    scale=alt.Scale(domain=cmp_customers, range=CAT_PALETTE[:len(cmp_customers)])),
                    tooltip=[alt.Tooltip("month:T", title="Month", format="%b %Y"),
                             alt.Tooltip("customer:N", title="Customer"),
                             alt.Tooltip("median:Q", title="Median rate", format=".2f"),
                             alt.Tooltip("n:Q", title="Trips")],
                ).properties(height=360)
                st.altair_chart(chart, width="stretch")
                st.caption("One line per selected customer: that month's median top-of-band fuel rate.")
        else:
            g = fuel_rate_monthly(fd)
            if g.empty:
                st.info("No fuel-rate data for that selection.")
            else:
                base = alt.Chart(g).encode(x=alt.X("month:T", title="Month"))
                band = base.mark_area(opacity=0.15, color=FUEL_BLUE).encode(
                    y=alt.Y("min:Q", title="Fuel rate (THB/litre)"), y2="max:Q")
                line = base.mark_line(strokeWidth=2, color=FUEL_BLUE,
                                      point=alt.OverlayMarkDef(size=40, color=FUEL_BLUE)).encode(
                    y=alt.Y("median:Q", title="Fuel rate (THB/litre)"),
                    tooltip=[alt.Tooltip("month:T", title="Month", format="%b %Y"),
                             alt.Tooltip("median:Q", title="Median rate", format=".2f"),
                             alt.Tooltip("min:Q", title="Min", format=".2f"),
                             alt.Tooltip("max:Q", title="Max", format=".2f"),
                             alt.Tooltip("n:Q", title="Trips")],
                )
                st.altair_chart((band + line).properties(height=360), width="stretch")
                st.caption("Line = median top-of-band fuel rate across every customer pooled together, "
                           "by month billed. Shaded band = that month's min–max across all customers/routes.")

        # --- bar chart: how many trips reference each fuel-rate band ---
        st.subheader("How common each fuel-rate band is")
        bc = fd.drop_duplicates("job_order_no")[["fuel_rate_bracket", "fuel_rate_low"]].copy()
        bc = bc.groupby("fuel_rate_bracket").agg(trips=("fuel_rate_bracket", "size"),
                                                  low=("fuel_rate_low", "first")).reset_index()
        bc["low"] = bc["low"].fillna(-np.inf)
        bc = bc.sort_values("low")
        order = bc["fuel_rate_bracket"].tolist()
        bar = alt.Chart(bc).mark_bar(color=FUEL_BLUE, size=14).encode(
            x=alt.X("fuel_rate_bracket:N", title="Fuel-rate band (THB/litre)", sort=order,
                    axis=alt.Axis(labelAngle=-60)),
            y=alt.Y("trips:Q", title="Trips"),
            tooltip=[alt.Tooltip("fuel_rate_bracket:N", title="Band"), alt.Tooltip("trips:Q", title="Trips")],
        ).properties(height=280)
        st.altair_chart(bar, width="stretch")
        st.caption("Number of billed trips that reference each fuel-rate band, low to high. Bands come "
                   "straight from the route text, so a customer's contract can use a narrow or wide band.")

    st.divider()
    st.subheader("Estimated price by distance range × vehicle class")
    st.caption("Model price (THB/trip, A→B) at a representative km within each range. Ranges are picked "
               "from the real spread of billed trip distances, so each one covers a meaningful share of "
               "actual trips. '*' = this km is outside that vehicle class's own training data - "
               "extrapolated, rough guide only.")
    if summary is None:
        need_model()
    else:
        try:
            bin_cfg = {"Range (km)": TxtCol("Range (km)"), "km": NumCol("km", help="Representative km used for the estimate in this row", format="%d")}
            st.dataframe(bin_rate_table(), hide_index=True, width="stretch", column_config=bin_cfg)
        except Exception as e:
            model_failed(e)
