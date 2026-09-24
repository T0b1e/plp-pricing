"""Sidebar: job-status filter and what the data can do right now."""
import pandas as pd
import streamlit as st

from ui.components import reload_button
from ui.config import STATUS_HELP


def render_sidebar(trips: pd.DataFrame, locs: pd.DataFrame, summary, km_warning, model_warning):
    """Draw the sidebar and return (trips filtered by job status, selected statuses)."""
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
    return trips, tuple(selected_statuses)
