"""Dashboard tab: quick estimate for a new route, per-route price averages, fitted model, per-km, route builder."""
import re

import numpy as np
import pandas as pd
import streamlit as st

from src.aliases import canonical_map
from src.estimate import estimate
from src.fit_model import ALL, TRAIN_STATUSES
from ui.components import model_failed, need_model
from ui.config import NumCol, TxtCol
from ui.data import load_trips
from ui.routing import leg_key, road_km
from ui.stats import cutoff_price_stats


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
    mask = g["fuel_rate_low"].notna() & g["fuel_rate_high"].notna() & g["eppo_price"].notna()
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


def eppo_date_text(dates: pd.Series) -> str | None:
    """Publish date(s) of the EPPO readings behind a route's base price, e.g. '20-Apr-2026' or
    '02-Jan-2026 to 20-Apr-2026' when its trips matched different readings."""
    d = dates.dropna()
    if d.empty:
        return None
    lo, hi = d.min().strftime("%d-%b-%Y"), d.max().strftime("%d-%b-%Y")
    return lo if lo == hi else f"{lo} to {hi}"


def eppo_price_text(g: pd.DataFrame) -> str:
    """Median EPPO price, or the reason there isn't one for this route."""
    if g["eppo_price"].notna().any():
        return f"{g['eppo_price'].median():.2f}"
    if g["ship_date"].isna().all():
        return "n/a - trips have no ship date"
    if g["eppo_date"].isna().all():
        return "n/a - no EPPO data loaded (run scripts/fetch_eppo_diesel.py)"
    return "n/a - EPPO reading has no price"


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
             "base the divergence next to it is measured against. Shown for every route, including "
             "those with no billed fuel clause."),
    "EPPO Reading Date": TxtCol(
        "EPPO Reading Date",
        help="Publish date of the EPPO reading(s) behind 'EPPO Base Price' (nearest to each trip's "
             "ship date). A range means the route's trips matched different readings."),
    "Gas Price Divergence": TxtCol(
        "Gas Price Divergence",
        help="Median gap between the billed top-of-band fuel rate and the EPPO base price (see "
             "'EPPO Base Price' column) - THB/litre and as a % of the EPPO price. Positive = billed "
             "above the published market price. Blank where the route has no billed fuel clause "
             "(no Gas Price Range). Each trip uses EPPO's nearest published reading, even past "
             "the newest date EPPO has published so far."),
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
        styles = ["background-color: rgba(255, 193, 7, 0.25)" if moved.loc[row.name] else "" for _ in row.index]
        if gas_col in row.index:
            pct = gas_pct.loc[row.name]
            if pd.notna(pct) and abs(pct) > pct_tol:
                color = "rgba(255, 82, 82, 0.40)" if pct > 0 else "rgba(0, 200, 83, 0.35)"  # red = billed above EPPO, green = below
                styles[row.index.get_loc(gas_col)] = f"background-color: {color}"
        return styles
    return df.style.apply(_row, axis=1)


def render(ctx):
    trips, locs, summary = ctx.trips, ctx.locs, ctx.summary
    st.subheader("Quick price estimate (new route)")
    st.caption("Pick an origin and destination, fetch the real road distance from Google Routes, then "
               "then show two prices: (1) road km x the existing average billed THB/km, and (2) the "
               "fitted price model (base fare + THB/km). Distance is "
               "cached after the first fetch, so only genuinely new routes cost an API call.")
    qe1, qe2, qe3 = st.columns([4, 4, 3])
    # Skip junk "places" typed into the place field of the source sheet: distances "(165 KM.)" /
    # "75.81KM", size ranges "(301 - 350)", temperature notes "(Pre - cool)".
    junk_place = re.compile(r"\(?\s*\d+(\.\d+)?\s*KM\.?\s*\)?|\(\s*\d+\s*-\s*\d+\s*\)|\(\s*Pre\s*-\s*cool\s*\)", re.I)
    place_opts_qe = sorted(p for p in locs.index if not junk_place.fullmatch(str(p)))
    default_origin = "PCS (ห้องเย็นแปซิฟิค)"   # the main origin; changeable / clearable afterwards
    if "qe_origin_init" not in st.session_state:   # pre-select once per session, not on every rerun
        st.session_state["qe_origin_init"] = True
        if default_origin in place_opts_qe:
            st.session_state["qe_origin"] = default_origin
    q_origin = qe1.selectbox("Origin", place_opts_qe, index=None, placeholder="Type to search…", key="qe_origin")
    q_dest = qe2.selectbox("Destination", place_opts_qe, index=None, placeholder="Type to search…", key="qe_dest")
    q_vehicle = qe3.selectbox("Vehicle class", sorted(trips["vehicle_class"].dropna().unique()), key="qe_vehicle")

    if q_origin and q_dest and q_vehicle:
        km, src = road_km(locs, q_origin, q_dest)
        if src == "ask":
            st.write("This leg is not in the distance cache.")
            if st.button("Fetch road km from Google Routes (1 API call)", key="qe_fetch"):
                st.session_state[f"allow_api_{leg_key(locs, q_origin, q_dest)}"] = True
                st.rerun()
        elif km is None:
            st.write(f"Can't get road km: {src}.")
        else:
            # 1. existing average: road km x the billed THB/km of trips of this class at a similar
            # distance. A class-wide THB/km mixes short trips (high per km: fixed cost over few km) with
            # long ones and overprices long routes. Statuses match the model's training set.
            all_trips, _ = load_trips()
            billed = all_trips[(all_trips["vehicle_class"] == q_vehicle) & (all_trips["price"] > 0)
                               & (all_trips["total_km"] > 0) & all_trips["status"].isin(TRAIN_STATUSES)]
            rate_src = billed[billed["total_km"].between(km * 0.8, km * 1.2)]
            scope = f"trips of {km * 0.8:,.0f}-{km * 1.2:,.0f} km"
            if len(rate_src) < 5:   # too few near this distance: fall back to the whole class
                rate_src, scope = billed, "all distances"
            m1, m2, m3 = st.columns(3)
            m1.metric("Road km", f"{km:,.1f}", help=f"Source: {src}")

            # if this exact pair + class was already billed, its plain average is the price to quote:
            # same trips and same number as the 'Avg.Price (before cut-off)' column in the route table
            canon = canonical_map()
            pair = trips[(trips["vehicle_class"] == q_vehicle) & (trips["price"] > 0)
                         & (trips["origin_c"] == canon.get(q_origin, q_origin))
                         & (trips["dest_c"] == canon.get(q_dest, q_dest))
                         & ~trips["bill_no"].astype(str).isin(BAD_GEOCODE_BILL_NOS)]
            if not pair.empty:
                m2.metric("1. Existing avg price (THB)", f"{pair['price'].mean():,.0f}")
                m2.caption(f"**Route already billed:** plain average of {len(pair):,} {q_vehicle} trips on this "
                           f"origin → destination (range {pair['price'].min():,.0f}-{pair['price'].max():,.0f} THB) "
                           "- same as 'Avg.Price (before cut-off)' in the route table below, for the statuses "
                           "ticked in the sidebar.")
            elif rate_src.empty:
                m2.metric("1. Existing avg price (THB)", "n/a",
                          help=f"No billed trips with road km for {q_vehicle} yet - no per-km rate to apply.")
            else:
                rate_stats = cutoff_price_stats((rate_src["price"] / rate_src["total_km"]).to_numpy())
                m2.metric("1. Existing avg price (THB)", f"{km * rate_stats['avg_modiz']:,.0f}")
                m2.caption(f"New route: road km x **{rate_stats['avg_modiz']:,.2f} THB/km** - Modified-Z-trimmed "
                           f"mean of billed price÷km for {q_vehicle} {scope} (n={rate_stats['n_modiz']:,} trips, "
                           f"{' + '.join(TRAIN_STATUSES)}). Plain untrimmed average "
                           f"({rate_stats['avg']:,.2f} THB/km) would give {km * rate_stats['avg']:,.0f} THB.")

            # 2. modelled: base fare + per-km line fitted on billed trips (src/estimate.py)
            if summary is None:
                m3.metric("2. Modelled price (THB)", "n/a", help="The price model is not built yet.")
                need_model()
            else:
                try:
                    e = estimate(km, q_vehicle, 2, summary, ctx.rates)
                except Exception as ex:
                    m3.metric("2. Modelled price (THB)", "n/a")
                    model_failed(ex)
                else:
                    m3.metric("2. Modelled price (THB)", f"{e['price']:,.0f}")
                    m3.caption(
                        f"**Price = base fare + rate per km x road km**  \n"
                        f"= {e['base_fare']:,.0f} THB (base fare) + {e['rate_per_km']:.2f} THB/km (rate per km) "
                        f"x {km:,.1f} km (road km)  \n"
                        f"Fitted on {int(e['n']):,} billed trips ({e['vehicle']}, {e['method'].lower()}).  \n"
                        "Base fare and rate per km are the intercept and slope of a line fitted through those "
                        "trips' bills (trimmed least squares) - a fitted value, not a fee on any invoice. "
                        "See the 'Fitted price model' table below for every class.  \n"
                        f"Typical range: {e['low']:,.0f} THB (low) - {e['high']:,.0f} THB (high).")
                    if e["extrapolated"]:
                        st.caption(f"{km:,.0f} km is outside the data for {e['vehicle']} "
                                   f"({e['km_min']:.0f}-{e['km_max']:.0f} km) - treat the modelled price as a rough guide.")

            # reality check: what similar-length trips of this class were actually billed (any status)
            all_trips, _ = load_trips()
            sim = all_trips[(all_trips["vehicle_class"] == q_vehicle) & (all_trips["price"] > 0)
                            & all_trips["total_km"].between(km * 0.8, km * 1.2)]
            if len(sim) >= 3:
                q10, q50, q90 = sim["price"].quantile([0.1, 0.5, 0.9])
                st.info(f"**Reality check:** {len(sim):,} billed {q_vehicle} trips of {km * 0.8:,.0f}-{km * 1.2:,.0f} km "
                        f"(all job statuses): median **{q50:,.0f} THB** (p10-p90 {q10:,.0f} - {q90:,.0f}).")
    st.divider()

    dash_f1, dash_f2 = st.columns(2)
    dash_vehicle_opts = sorted(trips["vehicle_class"].dropna().unique())
    dash_customer_opts = sorted(trips["customer"].dropna().unique())
    dash_vehicle = dash_f1.multiselect(
        "Vehicle type", dash_vehicle_opts, default=dash_vehicle_opts,
        help="Show only these truck classes. All selected by default.")
    dash_customer = dash_f2.multiselect(
        "Customer", dash_customer_opts, default=dash_customer_opts,
        help="Show only routes billed to these customers. All selected by default.")

    priced = trips[trips["vehicle_class"].notna() & (trips["origin_c"] != "") & (trips["dest_c"] != "")
                   & (trips["price"] > 0) & ~trips["bill_no"].astype(str).isin(BAD_GEOCODE_BILL_NOS)]
    if dash_vehicle:
        priced = priced[priced["vehicle_class"].isin(dash_vehicle)]
    if dash_customer:
        priced = priced[priced["customer"].isin(dash_customer)]
    route_row_list = []
    for (vc, o, d), g in priced.groupby(["vehicle_class", "origin_c", "dest_c"]):
        sr = stats_row(g["price"].to_numpy())
        resolved = gas_divergence_resolved(g)
        route_row_list.append({
            "Car Type": vc, "Ori": o, "Dest": d,
            "Total KM": g["total_km"][g["total_km"] > 0].median(),
            "Route Map": maps_route_url(g["origin_latlon"].iloc[0], g["dest_latlon"].iloc[0]),
            **sr,
            "Existing pair price (min-max)": f"{g['price'].min():,.0f}-{g['price'].max():,.0f}",
            "Gas Price Range": (f"{g['fuel_rate_low'].median():.2f}-{g['fuel_rate_high'].median():.2f}"
                                if g["fuel_rate_low"].notna().any() and g["fuel_rate_high"].notna().any()
                                else None),
            "EPPO Base Price": eppo_price_text(g),
            "EPPO Reading Date": eppo_date_text(g["eppo_date"]),
            "Gas Price Divergence": gas_divergence_text(resolved),
        })
    if route_row_list:
        route_rows = pd.DataFrame(route_row_list).sort_values(
            ["Car Type", "Ori", "N (before cut-off)"], ascending=[True, True, False]).reset_index(drop=True)
    else:
        route_rows = pd.DataFrame(route_row_list)

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
        st.dataframe(highlight_diverging(per_km_rows, margin=20), hide_index=True, width="stretch",
                     column_config=dash_column_config("THB per km, after each method's outlier cutoff.",
                                                       decimals=2))
    else:
        st.info("No trips with road km match this vehicle type / customer combination.")