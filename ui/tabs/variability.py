"""Price variability tab: how much billed price swings within a month, per route + vehicle class."""
import streamlit as st

from ui.components import medal_rank_style
from ui.config import DEV_CFG, MONTHLY_CFG, PAIR_CFG, PAIR_MONTH_CFG
from ui.stats import flag_price_outliers, monthly_pair_stats, pair_deviations, pair_price_stats, pair_rollup


def render(ctx):
    trips = ctx.trips
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
    monthly = monthly_pair_stats(trips, ctx.statuses, int(min_trips), drop_outliers)
    price_stats = pair_price_stats(trips, ctx.statuses, drop_outliers)

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
            st.dataframe(pair_deviations(trips, origin, dest, veh, monthly, drop_outliers), hide_index=True,
                        width="stretch", column_config=DEV_CFG)
