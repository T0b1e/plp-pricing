"""Fuel rate tab: diesel-price bands billed over time, plus the model's price by distance range."""
import altair as alt
import numpy as np
import streamlit as st

from src.fit_model import ALL
from ui.components import model_failed, need_model
from ui.config import CAT_PALETTE, FUEL_BLUE, MAX_FUEL_SERIES, NumCol, TxtCol
from ui.stats import bin_rate_long, bin_rate_table, fuel_rate_monthly


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
            all_classes = (summary.loc[summary["vehicle_class"] != ALL]
                            .sort_values("n", ascending=False)["vehicle_class"].tolist())
            chart_vehicles = st.multiselect(
                "Compare vehicle classes", all_classes, default=all_classes[:5],
                help="Lines shown on the chart below. The table further down always lists every class.")
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
