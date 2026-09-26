"""Dashboard per-class THB/km table; clicking a row opens the cost breakdown dialog."""
import pandas as pd
import streamlit as st

from .cost_dialog import cost_dialog
from .stats import dash_column_config, stats_row
from .styling import highlight_diverging, with_summary_row


def per_km_section(per_km_src: pd.DataFrame) -> None:
    """Per-class THB/km table; clicking a row opens its bottom-up cost breakdown dialog."""
    st.subheader("Per KM")

    per_km_row_list = [{"Car Type": vc, **stats_row(g["price_per_km"].to_numpy())}
                       for vc, g in per_km_src.groupby("vehicle_class")]
    if not per_km_row_list:
        st.info("No trips with road km match this vehicle type / customer combination.")
        return

    per_km_rows = pd.DataFrame(per_km_row_list).sort_values("Car Type").reset_index(drop=True)

    st.caption("Click a row to see its cost breakdown.")
    sel = st.dataframe(highlight_diverging(with_summary_row(per_km_rows, "vehicle types"), margin=20),
                       hide_index=True, width="stretch",
                       column_config=dash_column_config("THB per km, after each method's outlier cutoff.",
                                                         decimals=2),
                       on_select="rerun", selection_mode="single-row", key="per_km_table")

    # The SUMMARY row (last) has no breakdown
    picked = [i for i in sel.selection.rows if i < len(per_km_rows)]

    if not picked:
        # Selection cleared: the same row may open again
        st.session_state.pop("per_km_shown", None)
    elif st.session_state.get("per_km_shown") != picked[0]:
        # Only open on a *new* selection, so a rerun doesn't reopen a closed dialog
        st.session_state["per_km_shown"] = picked[0]

        row = per_km_rows.iloc[picked[0]]
        g = per_km_src[per_km_src["vehicle_class"] == row["Car Type"]]
        eppo = g["eppo_price"].median()

        cost_dialog(row["Car Type"], float(g["total_km"].median()), float(row["Avg.Price (before cut-off)"]),
                    float(eppo) if pd.notna(eppo) else None)
