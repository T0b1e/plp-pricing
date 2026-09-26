"""Dashboard constants: known-bad data, colours, cut-off column names, column configs."""
import streamlit as st

from ui.config import NumCol, TxtCol


# Known bad data: bill_no 2607000163 (TUF -> "DC Makro Mahachai") has origin/destination
# geocoded to the wrong "DC Makro Mahachai" candidate (a retail mall ~1km from origin,
# instead of the actual distribution center ~10km+ away - see geocode_cache.json, which lists
# both candidates). total_km for that leg is computed as ~1.05km against a real billed price
# of 2016-5376 THB, producing 1900-5100 THB/km outlier ratios that this bill repeats 30+ times
# (recurring contract), dominating the untrimmed 4W/6W reefer per-km averages below. The raw
# rows are kept in trips_clean.csv untouched; they are only excluded here from the price/km
# stats until the geocode is corrected at the source.
BAD_GEOCODE_BILL_NOS = {"2607000163"}

# Row background colours (shared by the route table and the pair table)
COLOR_AMBER = "rgba(255, 193, 7, 0.25)"    # cut-off actually trimmed something
COLOR_RED = "rgba(255, 82, 82, 0.40)"      # higher than the reference price
COLOR_GREEN = "rgba(0, 200, 83, 0.35)"     # lower than the reference price
COLOR_BLUE = "rgba(33, 150, 243, 0.35)"    # matched / almost the same

# The three cut-off methods, as (average column, N column) pairs
AVG_COLS = ["Avg.Price (before cut-off)", "Avg.Price (3-sigma, after cut-off)", "Avg.Price (modi-Z, after cut-off)"]
N_COLS = ["N (before cut-off)", "N (3-sigma, after cut-off)", "N (modi-Z, after cut-off)"]

# Column configs for the fuel / EPPO columns of the route table
OTHER_CFG = {
    "Gas Price Range": TxtCol(
        "Gas Price Range",
        help="Median fuel-rate band (THB/litre) billed on this route - partial coverage, blank "
             "where no trip on this route/class carries a fuel clause."),
    "Gas Price Median (min-max)": TxtCol(
        "Gas Price Median (min-max)",
        help="Median billed top-of-band fuel rate (THB/litre) on this route, with the lowest band "
             "floor - highest band ceiling seen across its trips in brackets. Blank where no trip "
             "carries a fuel clause."),
    "EPPO Base Price": TxtCol(
        "EPPO Base Price",
        help="Median EPPO published HSD B7 diesel retail price (THB/litre) nearest each trip's ship "
             "date (data/eppo_diesel_hsd_b7.json, run scripts/fetch_eppo_diesel.py to update) - the "
             "base the divergence next to it is measured against. Shown for every route, including "
             "those with no billed fuel clause."),
    "EPPO Price Range": TxtCol(
        "EPPO Price Range",
        help="Min-max of the EPPO diesel prices (THB/litre) matched to this route's trips. "
             "'EPPO Base Price' is the median of these; a single value means all trips matched "
             "the same price."),
    "EPPO Reading Date": TxtCol(
        "EPPO Reading Date",
        help="Publish date of the EPPO reading(s) behind 'EPPO Base Price' (nearest to each trip's "
             "ship date). A range means the route's trips matched different readings."),
    "Gas Price Divergence": TxtCol(
        "Gas Price Divergence",
        help="Median gap between the billed top-of-band fuel rate and the EPPO base price (see "
             "'EPPO Base Price' column) - THB/litre and as a % of the EPPO price. Positive = billed "
             "above the published market price. Blank where the route has no billed fuel clause "
             "(no Gas Price Range). Each trip uses EPPO's nearest published reading, even past "
             "the newest date EPPO has published so far."),
    "Existing pair price (min-max)": TxtCol(
        "Existing pair price (min-max)",
        help="Min-max range actually billed on this route. The alternative calculation method - "
             "estimated km (from the geocoded addresses) x model-estimated price/km, for a car "
             "type / route with no billed history yet - is not wired up here."),
}

# Column configs for the Google-Maps link and road distance
MAP_CFG = {
    "Route Map": st.column_config.LinkColumn(
        "Route Map", help="Open the full driving route (origin → destination) on Google Maps.",
        display_text="🛣️ Open"),
    "Total KM": NumCol("Total KM", help="Estimated road distance (median across trips on this route, "
                                        "Google Routes API, excludes live traffic).", format="%,.0f"),
}
