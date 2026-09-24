"""Web UI for the price lookup:  python -m streamlit run app.py

Tab 1: origin + destination -> historical trips on that route, or a model estimate if none.
Tab 2: km + vehicle -> model estimate, cross-checked against real trips of similar length.

Reads the pipeline outputs in data/. The only API call is an optional Routes lookup for a
route that has never been billed, and its result goes into the same distance cache.

The code lives in ui/: data.py (loaders), stats.py (computations), components.py / config.py
(shared widgets and column labels), sidebar.py, and one module per tab in ui/tabs/.
"""
import streamlit as st

st.set_page_config(page_title="Transport price lookup", page_icon="🚚", layout="wide")

from ui.components import reload_button   # noqa: E402  (after set_page_config, which must run first)
from ui.context import Ctx   # noqa: E402
from ui.data import load_locations, load_model, load_trips   # noqa: E402
from ui.sidebar import render_sidebar   # noqa: E402
from ui.tabs import dashboard, fuel, route, variability   # noqa: E402

try:
    trips, km_warning = load_trips()
    locs = load_locations()
except Exception as e:   # the page is useless without these; say why instead of a traceback
    st.error(f"Could not load the trip data: {e}")
    st.caption("If the pipeline is running right now, wait for it to finish, then reload.")
    reload_button()
    st.stop()

# the price model is optional: historical route lookups (tab 1) still work without it
model_warning = None
try:
    summary, rates = load_model()
except Exception as e:   # historical lookups still work without the model
    summary, rates = None, None
    model_warning = f"Price model not loaded: {e}"

with st.sidebar:
    trips, statuses = render_sidebar(trips, locs, summary, km_warning, model_warning)
ctx = Ctx(trips, locs, summary, rates, statuses)

st.title("Transport price lookup")
# The old "By km" tab is disabled (unused); its code is in git history before the ui/ split.
tab_dashboard, tab_route, tab_summary, tab_fuel = st.tabs(
    ["Dashboard", "Route history", "Price variability", "Fuel rate"])
with tab_dashboard:
    dashboard.render(ctx)
with tab_route:
    route.render(ctx)
with tab_summary:
    variability.render(ctx)
with tab_fuel:
    fuel.render(ctx)
