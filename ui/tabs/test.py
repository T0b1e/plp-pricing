"""Test tab: a temporary rate card (data/test_6w_rates.json) against the model's 6W round-trip estimates."""
import json

import altair as alt
import pandas as pd
import streamlit as st

from src.paths import DATA
from ui.components import model_failed, need_model
from ui.config import CAT_PALETTE
from ui.stats import ROUND_TRIP_RANGES, bin_rate_table

TEST_RATES = DATA / "test_6w_rates.json"


def render(ctx):
    summary, rates = ctx.summary, ctx.rates
    if not TEST_RATES.exists():
        st.info(f"{TEST_RATES.name} not found.")
        return
    if summary is None:
        need_model()
        return
    card = json.loads(TEST_RATES.read_text(encoding="utf-8"))
    st.caption(f"Temporary rate card (fuel band {card['fuel_band']}) vs the model's estimated price by "
               "round-trip distance range. Both are priced per trip; x = one-way km used by the model.")
    try:
        tbl = bin_rate_table(summary, rates, ranges=ROUND_TRIP_RANGES)
        classes = [c for c in tbl.columns if c.startswith("6W")]
        wide = pd.DataFrame({"Range (km)": tbl["Range (km)"], "km": tbl["km"],
                             "Test rate card": tbl["Range (km)"].map(card["prices"])})
        for c in classes:
            wide[f"Model: {c}"] = tbl[c].str.rstrip("*").str.replace(",", "").astype(float)
        picked = st.multiselect("Model classes to compare", classes, default=classes[:1])
        series = ["Test rate card"] + [f"Model: {c}" for c in picked]
        long = wide.melt(id_vars=["Range (km)", "km"], value_vars=series, var_name="series", value_name="price")
        chart = alt.Chart(long).mark_line(point=True).encode(
            x=alt.X("km:Q", title="One-way km (half the round-trip band midpoint)"),
            y=alt.Y("price:Q", title="Price (THB/trip)"),
            color=alt.Color("series:N", title="Series",
                            scale=alt.Scale(domain=series, range=CAT_PALETTE[:len(series)])),
            tooltip=[alt.Tooltip("series:N", title="Series"), alt.Tooltip("Range (km):N", title="Round-trip range"),
                     alt.Tooltip("price:Q", title="Price", format=",.0f")],
        ).properties(height=400)
        st.altair_chart(chart, width="stretch")
        for c in picked:
            wide[f"Card - {c} (THB)"] = wide["Test rate card"] - wide[f"Model: {c}"]
        st.dataframe(wide, hide_index=True, width="stretch")
    except Exception as e:
        model_failed(e)
