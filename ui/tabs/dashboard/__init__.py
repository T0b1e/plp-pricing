"""Dashboard tab: per-route price averages, per-km, route builder.

Package layout:
    constants.py         known-bad data, colours, cut-off column names, column configs
    stats.py             per-route / per-class price statistics, fuel divergence
    formatters.py        Google Maps link, EPPO / gas-price cell text
    styling.py           diverging-row detection, SUMMARY row, highlighting
    cost_dialog.py       pop-up pie of the bottom-up trip cost
    pair_divergence.py   "Per KM and existing pair price" section
    route_table.py       per-route price averages table
    per_km.py            per-class THB/km table
"""
from .constants import BAD_GEOCODE_BILL_NOS
from .pair_divergence import pair_divergence_section
from .per_km import per_km_section
from .route_table import route_table_section


def render(ctx):
    trips = ctx.trips

    # Billable trips: known class, both ends present, positive price, minus known-bad geocodes
    priced = trips[trips["vehicle_class"].notna() & (trips["origin_c"] != "") & (trips["dest_c"] != "")
                   & (trips["price"] > 0) & ~trips["bill_no"].astype(str).isin(BAD_GEOCODE_BILL_NOS)]

    # 1) Per-route price averages
    route_table_section(priced)

    # 2) Per-km vs existing pair price (needs a road distance to compute THB/km)
    per_km_src = priced[priced["total_km"] > 0].copy()
    per_km_src["price_per_km"] = per_km_src["price"] / per_km_src["total_km"]
    pair_divergence_section(per_km_src)

    # 3) Per-class THB/km + cost breakdown dialog
    per_km_section(per_km_src)
