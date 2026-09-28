"""Fuel rate tab: diesel-price bands billed over time, plus the model's price by distance range."""
import altair as alt
import streamlit as st

from src.fit_model import ALL
from ui.components import model_failed, need_model
from ui.config import CAT_PALETTE, NumCol, TxtCol
from ui.stats import bin_rate_long, bin_rate_table


def render(ctx):
    trips, summary, rates = ctx.trips, ctx.summary, ctx.rates
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
            all_classes = (summary.loc[summary["vehicle_class"] != ALL]
                            .sort_values("n", ascending=False)["vehicle_class"].tolist())
            sidebar_classes = set(trips["vehicle_class"].dropna().unique())
            chart_vehicles = [v for v in all_classes if v in sidebar_classes]
            if chart_vehicles:
                long_df = bin_rate_long(summary, rates, chart_vehicles)
                color_scale = alt.Scale(domain=chart_vehicles, range=CAT_PALETTE[:len(chart_vehicles)])
                # cap the x axis at the last plotted range; a class's km_max rule can sit far past it
                x_scale = alt.Scale(domain=[0, float(long_df["km"].max()) * 1.05], nice=False)
                line = alt.Chart(long_df).mark_line(point=True).encode(
                    x=alt.X("km:Q", title="Distance (km, representative point per range)", scale=x_scale),
                    y=alt.Y("price:Q", title="Estimated price (THB/trip)"),
                    color=alt.Color("vehicle_class:N", title="Vehicle class", scale=color_scale,
                                    legend=alt.Legend(orient="right", symbolType="stroke", symbolStrokeWidth=3,
                                                      labelLimit=260, labelFontSize=12, titleFontSize=12)),
                    strokeDash=alt.StrokeDash("extrapolated:N", title="Outside training data",
                                               scale=alt.Scale(domain=[False, True], range=[[1, 0], [4, 3]])),
                    tooltip=[alt.Tooltip("vehicle_class:N", title="Vehicle class"),
                             alt.Tooltip("Range (km):N", title="Range"),
                             alt.Tooltip("price:Q", title="Price", format=",.0f"),
                             alt.Tooltip("extrapolated:N", title="Extrapolated")],
                )

                # vertical rule at each selected class's own km_min/km_max - the real edges of its
                # training data. Left of km_min or right of km_max, that class's line is made up.
                bounds = summary.loc[summary["vehicle_class"].isin(chart_vehicles),
                                      ["vehicle_class", "km_min", "km_max"]]
                bounds_long = bounds.melt(id_vars="vehicle_class", value_vars=["km_min", "km_max"],
                                          var_name="bound", value_name="km")
                rules = alt.Chart(bounds_long).mark_rule(strokeDash=[2, 2], strokeWidth=1.5, opacity=0.6, clip=True).encode(
                    x=alt.X("km:Q", scale=x_scale),
                    color=alt.Color("vehicle_class:N", scale=color_scale, legend=None),
                    tooltip=[alt.Tooltip("vehicle_class:N", title="Vehicle class"),
                             alt.Tooltip("bound:N", title="Bound"),
                             alt.Tooltip("km:Q", title="km", format=".0f")],
                )
                st.altair_chart((line + rules).properties(height=380), width="stretch")
                st.caption("Dashed line segments are extrapolated. The thin vertical dashed lines mark each "
                           "vehicle class's own km_min/km_max - the actual edges of its real billed trips "
                           "(same color as its line); outside that range for that color, the line is made "
                           "up, not fit from data.")
            bin_cfg = {"Range (km)": TxtCol("Range (km)"), "km": NumCol("km", help="Representative km used for the estimate in this row", format="%d")}
            st.dataframe(bin_rate_table(summary, rates), hide_index=True, width="stretch", column_config=bin_cfg)
        except Exception as e:
            model_failed(e)
