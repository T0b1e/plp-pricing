"""Dashboard cost dialog: pop-up pie of the bottom-up cost of a typical trip."""
import altair as alt
import pandas as pd
import streamlit as st

from src.cost_model import COST_WORKBOOK, fuel_per_km, trip_cost
from ui.config import CAT_PALETTE, NumCol


@st.dialog("Cost breakdown", width="large")
def cost_dialog(vehicle_class: str, km: float, avg_price_per_km: float, actual_diesel: float | None):
    """Pie of the bottom-up cost of a typical trip of this class (median billable km)."""
    st.markdown(f"**{vehicle_class}** - typical trip of **{km:,.0f} km** (median of its billed trips)")

    cost = trip_cost(vehicle_class, km)
    if cost is None:
        st.info(f"'{COST_WORKBOOK.name}' has no cost column for {vehicle_class} (only 4W/6W/10W dry and reefer).")
        return

    # ── Cost items (only the ones that actually cost something) ──
    items = pd.DataFrame({"item": list(cost), "thb": list(cost.values())})
    items = items[items["thb"] > 0]
    items["share"] = items["thb"] / items["thb"].sum()

    # ── Donut chart ──
    pie = alt.Chart(items).mark_arc(innerRadius=60).encode(
        theta=alt.Theta("thb:Q", stack=True),
        color=alt.Color("item:N", title="Cost item", sort=alt.EncodingSortField("thb", order="descending"),
                        scale=alt.Scale(range=CAT_PALETTE + ["#8a8f98", "#b5b9c0", "#5c6b7a", "#c9a227"])),
        order=alt.Order("thb:Q", sort="descending"),
        tooltip=[alt.Tooltip("item:N", title="Item"), alt.Tooltip("thb:Q", title="THB/trip", format=",.0f"),
                 alt.Tooltip("share:Q", title="Share", format=".1%")],
    ).properties(height=340)
    st.altair_chart(pie, width="stretch")

    # ── Headline metrics: model cost vs what was billed ──
    total = items["thb"].sum()
    billed = avg_price_per_km * km

    m1, m2, m3 = st.columns(3)
    m1.metric("Model cost / trip", f"{total:,.0f} THB", help=f"{total / km:,.2f} THB/km")
    m2.metric("Avg billed / trip", f"{billed:,.0f} THB", help=f"{avg_price_per_km:,.2f} THB/km x {km:,.0f} km")
    m3.metric("Implied margin", f"{1 - total / billed:.1%}" if billed else "n/a", help="1 - model cost / billed price")

    # ── Fuel per km: workbook assumption vs the real EPPO diesel price ──
    assumed = fuel_per_km(vehicle_class)
    actual = fuel_per_km(vehicle_class, actual_diesel) if actual_diesel else None

    if actual:
        st.markdown("**Fuel per km: assumption vs fact**")
        d_km = actual[1] - assumed[1]

        f1, f2, f3 = st.columns(3)
        f1.metric("Assumed", f"{assumed[1]:.2f} THB/km", help=f"Workbook diesel {assumed[0]:.2f} THB/L")
        f2.metric("From fact", f"{actual[1]:.2f} THB/km", f"{d_km:+.2f} THB/km ({d_km / assumed[1]:+.1%})",
                  delta_color="inverse",
                  help=f"Median EPPO diesel {actual[0]:.2f} THB/L on this class's trip dates, same km/L")
        f3.metric("Per trip", f"{actual[1] * km:,.0f} THB", f"{d_km * km:+,.0f} THB", delta_color="inverse",
                  help=f"Running fuel over {km:,.0f} km (genset excluded)")

    # ── Itemised table ──
    st.dataframe(items.rename(columns={"item": "Cost item", "thb": "THB/trip", "share": "Share"}),
                 hide_index=True, width="stretch",
                 column_config={"THB/trip": NumCol("THB/trip", format="%,.0f"), "Share": NumCol("Share", format="percent")})
