"""Dashboard per-route price averages table (with fuel / EPPO columns)."""
import pandas as pd
import streamlit as st

from .constants import MAP_CFG, OTHER_CFG
from .formatters import (eppo_date_text, eppo_price_range_text, eppo_price_text, gas_median_range_text,
                         maps_route_url, gas_divergence_text)
from .stats import dash_column_config, gas_divergence_resolved, stats_row
from .styling import blank_repeated_labels, diverging_mask, highlight_diverging, with_summary_row


def build_route_rows(priced: pd.DataFrame) -> pd.DataFrame:
    """One row per (car type, origin, destination): price stats, road km, fuel / EPPO columns."""
    rows = []

    for (vc, o, d), g in priced.groupby(["vehicle_class", "origin_c", "dest_c"]):
        sr = stats_row(g["price"].to_numpy())
        resolved = gas_divergence_resolved(g)

        rows.append({
            # Route identity
            "Car Type": vc, "Ori": o, "Dest": d,
            "Total KM": g["total_km"][g["total_km"] > 0].median(),
            "Route Map": maps_route_url(g["origin_latlon"].iloc[0], g["dest_latlon"].iloc[0]),

            # Price stats per cut-off method
            **sr,
            "Existing pair price (min-max)": f"{g['price'].min():,.0f}-{g['price'].max():,.0f}",

            # Billed fuel band vs EPPO published price
            "Gas Price Range": (f"{g['fuel_rate_low'].median():.2f}-{g['fuel_rate_high'].median():.2f}"
                                if g["fuel_rate_low"].notna().any() and g["fuel_rate_high"].notna().any()
                                else None),
            "Gas Price Median (min-max)": gas_median_range_text(g),
            "EPPO Base Price": eppo_price_text(g),
            "EPPO Price Range": eppo_price_range_text(g),
            "EPPO Reading Date": eppo_date_text(g["eppo_date"]),
            "Gas Price Divergence": gas_divergence_text(resolved),
        })

    if not rows:
        return pd.DataFrame(rows)

    return pd.DataFrame(rows).sort_values(
        ["Car Type", "Ori", "N (before cut-off)"], ascending=[True, True, False]).reset_index(drop=True)


def route_table_section(priced: pd.DataFrame) -> None:
    """Per-route price averages table with its two filters and colour legend."""
    route_rows = build_route_rows(priced)

    if route_rows.empty:
        st.info("No billed trips match this vehicle type / customer combination.")
        return

    # ── Filters ──
    f1, f2 = st.columns(2)
    diverge_only = f1.checkbox("Show only diverging rows (cut-off actually trimmed something)")
    fuel_only = f2.checkbox("Only routes with fuel-range data")

    if diverge_only:
        route_rows = route_rows[diverging_mask(route_rows)].reset_index(drop=True)
    if fuel_only:
        route_rows = route_rows[route_rows["Gas Price Range"].notna()].reset_index(drop=True)

    # ── Summary row, then blank repeated Car Type / Ori labels ──
    route_rows = with_summary_row(route_rows)
    blank_repeated_labels(route_rows, has_summary=True)

    # ── Table ──
    st.caption(f"{len(route_rows) - 1:,} routes shown. 🟡 Highlighted rows are where a cut-off method actually "
               "trimmed something - its mean moved from the before-cut-off mean by more than 0.5%. Gas Price "
               "Divergence cells are highlighted 🔴 red where the billed fuel rate is more than 5% above "
               "EPPO's published price, or 🟢 green where it's more than 5% below. Last row is a "
               "trip-weighted summary across every route currently shown.")
    st.dataframe(highlight_diverging(route_rows), hide_index=True, width="stretch",
                 column_config={**dash_column_config("THB per trip, after each method's outlier cutoff."),
                                **OTHER_CFG, **MAP_CFG})
