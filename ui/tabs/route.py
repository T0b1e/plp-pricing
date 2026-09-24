"""Route history tab: past billed trips for an origin/destination, or a model estimate if none."""
import pandas as pd
import streamlit as st

from ui.components import formula_card, model_failed, need_model
from ui.config import MODEL_CFG, STATS_CFG, TRIPS_CFG
from ui.routing import leg_key, road_km
from ui.stats import model_table, price_stats, with_total


def render(ctx):
    trips, locs, summary, rates = ctx.trips, ctx.locs, ctx.summary, ctx.rates
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
            km, src = road_km(locs, origin, dest)
            if src == "ask":
                st.write("This leg is not in the distance cache.")
                key_btn = st.button("Get road km from Google Routes (1 API call)")
                if key_btn:
                    st.session_state[f"allow_api_{leg_key(locs, origin, dest)}"] = True
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
                        formula_card(km, 2, None, summary, rates)
                        st.dataframe(model_table(summary, rates, km, 2), hide_index=True, width="stretch",
                                     column_config=MODEL_CFG)
                    except Exception as e:
                        model_failed(e)
