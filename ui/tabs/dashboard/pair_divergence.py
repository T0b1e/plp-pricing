"""Dashboard "Per KM and existing pair price" section: per-km price vs billed price per pair."""
import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from ui.config import NumCol
from ui.stats import cutoff_price_stats

from .constants import COLOR_BLUE, COLOR_GREEN, COLOR_RED, MAP_CFG, OTHER_CFG
from .formatters import maps_route_url
from .styling import blank_repeated_labels


def _join_unique(values: pd.Series) -> str:
    """Sorted, de-duplicated, comma-joined non-blank values (for invoice / job-order cells)."""
    return ", ".join(sorted({str(v) for v in values.dropna() if str(v).strip()}))


def pair_divergence_frame(per_km_src: pd.DataFrame) -> pd.DataFrame:
    """One row per origin -> destination + vehicle class: the class THB/km rate (modified-Z mean),
    the pair's plain average billed price, and the gap between road km x rate and that price."""
    # Class-wide THB/km rate, after the modified-Z cut-off
    rate = per_km_src.groupby("vehicle_class")["price_per_km"].apply(
        lambda s: cutoff_price_stats(s.to_numpy())["avg_modiz"])

    # One row per (origin, destination, class) pair
    pairs = per_km_src.groupby(["origin_c", "dest_c", "vehicle_class"]).agg(
        km=("total_km", "median"), existing=("price", "mean"), p_min=("price", "min"),
        p_max=("price", "max"), trips=("price", "size"),
        o_ll=("origin_latlon", "first"), d_ll=("dest_latlon", "first"),
        bills=("bill_no", _join_unique), jobs=("job_order_no", _join_unique)).reset_index()

    # Per-km method price vs what was actually billed
    pairs["per_km"] = pairs["vehicle_class"].map(rate)
    pairs["per_km_price"] = pairs["per_km"] * pairs["km"]
    pairs["diverge"] = pairs["per_km_price"] - pairs["existing"]
    pairs["diverge_pct"] = pairs["diverge"] / pairs["existing"] * 100

    return pairs


def _pair_table_frame(pairs: pd.DataFrame) -> pd.DataFrame:
    """Rename / order the pair frame into the display table (still one row per pair)."""
    table = pairs.sort_values(["vehicle_class", "origin_c", "trips"], ascending=[True, True, False]).rename(columns={
        "origin_c": "Ori", "dest_c": "Dest", "vehicle_class": "Car Type", "km": "Total KM",
        "per_km": "Per km (THB/km)", "existing": "Existing pair price", "per_km_price": "Per-km price",
        "diverge": "Divergence (THB)", "diverge_pct": "Divergence (%)", "trips": "Trips"}).reset_index(drop=True)

    table["Existing pair price (min-max)"] = table["p_min"].map("{:,.0f}".format) + "-" + table["p_max"].map("{:,.0f}".format)

    show = table[["Car Type", "Ori", "Dest", "Total KM", "Per km (THB/km)", "Per-km price", "Existing pair price",
                  "Existing pair price (min-max)", "Divergence (THB)", "Divergence (%)", "Trips"]].copy()
    show["Invoice / bill no."] = table["bills"]
    show["Job order"] = table["jobs"]
    show["Route Map"] = [maps_route_url(o, d) for o, d in zip(table["o_ll"], table["d_ll"])]

    return show


def _pair_summary_row(show: pd.DataFrame) -> dict:
    """Last-row totals: per-km price and existing price summed over every pair shown."""
    tot_per_km, tot_existing = show["Per-km price"].sum(), show["Existing pair price"].sum()

    return {"Car Type": f"SUMMARY ({len(show):,} pairs)", "Per-km price": tot_per_km,
            "Existing pair price": tot_existing, "Divergence (THB)": tot_per_km - tot_existing,
            "Divergence (%)": (tot_per_km - tot_existing) / tot_existing * 100 if tot_existing else np.nan,
            "Trips": show["Trips"].sum()}


def _pair_invoices(per_km_src: pd.DataFrame, vc: str, o: str, d: str) -> None:
    """Drill-down: the billed trips (invoices) behind one pair's existing price."""
    inv = per_km_src[(per_km_src["vehicle_class"] == vc) & (per_km_src["origin_c"] == o)
                     & (per_km_src["dest_c"] == d)].sort_values("ship_date", ascending=False)

    st.markdown(f"**Invoices - {vc}: {o} → {d}** ({len(inv):,} trips, "
                f"{inv['bill_no'].nunique():,} bills)")
    st.dataframe(inv[["bill_no", "job_order_no", "ship_date", "customer", "status", "plate", "total_km", "price"]]
                 .rename(columns={"bill_no": "Invoice / bill no.", "job_order_no": "Job order",
                                  "ship_date": "Ship date", "customer": "Customer", "status": "Status",
                                  "plate": "Plate", "total_km": "Road km", "price": "Billed price"}),
                 hide_index=True, width="stretch",
                 column_config={"Road km": NumCol("Road km", format="%,.0f"),
                                "Billed price": NumCol("Billed price", format="%,.0f")})


def _pair_charts(pairs: pd.DataFrame, tol: float) -> None:
    """Band-share bar chart + divergence-vs-distance and per-km-vs-existing scatters for the pair table."""
    # ── Band each pair: matched / per-km above / per-km below ──
    band_order = [f"Matched (within ±{tol:g}%)", f"Per-km above (>{tol:g}%)", f"Per-km below (>{tol:g}%)"]
    band_scale = alt.Scale(domain=band_order, range=["#2196f3", "#ff5252", "#00c853"])

    charts = pairs.dropna(subset=["diverge_pct"]).copy()
    charts["Band"] = np.where(charts["diverge_pct"].abs() <= tol, band_order[0],
                              np.where(charts["diverge_pct"] > 0, band_order[1], band_order[2]))
    charts["Route"] = charts["origin_c"] + " → " + charts["dest_c"]

    # ── Share of pairs by band (stacked 100% bar per car type) ──
    st.markdown("**Share of pairs by band**")
    bar = alt.Chart(charts).mark_bar().encode(
        y=alt.Y("vehicle_class:N", title="Car type"),
        x=alt.X("count():Q", stack="normalize", title="Share of pairs", axis=alt.Axis(format="%")),
        color=alt.Color("Band:N", scale=band_scale, title="Band", sort=band_order),
        order=alt.Order("Band:N"),
        tooltip=[alt.Tooltip("vehicle_class:N", title="Car type"), alt.Tooltip("Band:N"),
                 alt.Tooltip("count():Q", title="Pairs")])
    st.altair_chart(bar.properties(height=max(80, 50 * charts["vehicle_class"].nunique())), width="stretch")

    # ── Divergence vs distance (scatter) ──
    # st.markdown("**Divergence vs distance**")
    # st.caption("Each marker is one route pair (colour = band, shape = car type). Short routes low and long routes high means the class-wide rate "
    #            f"under-prices short trips and over-prices long ones. The shaded strip is the ±{tol:g}% matched zone.")

    # scatter_opts = sorted(charts["vehicle_class"].unique())
    # scatter_cars = st.multiselect("Car type (chart)", scatter_opts, default=scatter_opts, key="pair_div_scatter_cars",
    #                               help="Show only these truck classes in the scatter. All selected by default.")
    # scatter = charts[charts["vehicle_class"].isin(scatter_cars)]

    # if scatter.empty:
    #     st.info("Select at least one car type for the chart.")
    #     return

    # y_max = st.slider("Zoom: top of Divergence axis (%)", 50, int(max(300, scatter["diverge_pct"].max())), 300,
    #                   step=50, key="pair_div_ymax",
    #                   help="Cap the y-axis so a few extreme pairs don't flatten the rest. Dots above it are "
    #                        "hidden from the chart (still in the table). You can also scroll to zoom and drag to pan.")
    # hidden = int((scatter["diverge_pct"] > y_max).sum())
    # if hidden:
    #     st.caption(f"{hidden:,} pair(s) above {y_max:,}% are off the chart - raise the slider to see them.")

    # y_scale = alt.Scale(domain=[-100, y_max], clamp=False)

    # Shaded ±tol "matched" strip behind the dots
    # zone = alt.Chart(pd.DataFrame({"lo": [-tol], "hi": [tol]})).mark_rect(color="#2196f3", opacity=0.10).encode(
    #     y=alt.Y("lo:Q", scale=y_scale), y2="hi:Q")

    # dots = alt.Chart(scatter).mark_point(filled=True, opacity=0.75, clip=True).encode(
    #     x=alt.X("km:Q", title="Road km (median)"),
    #     y=alt.Y("diverge_pct:Q", title="Divergence (%)", scale=y_scale),
    #     shape=alt.Shape("vehicle_class:N", title="Car type"),
    #     size=alt.Size("trips:Q", title="Trips", scale=alt.Scale(range=[30, 400])),
    #     color=alt.Color("Band:N", scale=band_scale, title="Band", sort=band_order),
    #     tooltip=[alt.Tooltip("vehicle_class:N", title="Car type"), alt.Tooltip("Route:N"),
    #              alt.Tooltip("km:Q", title="Road km", format=",.0f"),
    #              alt.Tooltip("per_km_price:Q", title="Per-km price", format=",.0f"),
    #              alt.Tooltip("existing:Q", title="Existing price", format=",.0f"),
    #              alt.Tooltip("diverge_pct:Q", title="Divergence %", format="+.1f"),
    #              alt.Tooltip("trips:Q", title="Trips")])
    # st.altair_chart((zone + dots).properties(height=360).interactive(), width="stretch")

    # ── Per-km price vs existing pair price (direct comparison, log scales) ──
    # st.markdown("**Per-km price vs existing pair price**")
    # st.caption("Each marker is one route pair: what it has actually been billed (x) against what the per-km rate "
    #            "would quote (y). On the diagonal = same price; above it the per-km rate quotes higher, below it "
    #            f"lower. Dashed lines are the ±{tol:g}% matched limits. Log scales, so cheap and expensive routes "
    #            "are both readable.")
    # cmp_pts = scatter[(scatter["existing"] > 0) & (scatter["per_km_price"] > 0)]
    # if cmp_pts.empty:
    #     return
    # lo = float(min(cmp_pts["existing"].min(), cmp_pts["per_km_price"].min())) * 0.8
    # hi = float(max(cmp_pts["existing"].max(), cmp_pts["per_km_price"].max())) * 1.25
    # axis_scale = alt.Scale(type="log", domain=[lo, hi], nice=False)
    # line_x = pd.DataFrame({"x": [lo, hi]})

    # def ref_line(factor: float, dash: list[int], opacity: float):
    #     return alt.Chart(line_x.assign(y=line_x["x"] * factor)).mark_line(
    #         color="#8a8f98", strokeDash=dash, opacity=opacity, clip=True).encode(
    #         x=alt.X("x:Q", scale=axis_scale), y=alt.Y("y:Q", scale=axis_scale))

    # cmp_dots = alt.Chart(cmp_pts).mark_point(filled=True, opacity=0.75, clip=True).encode(
    #     x=alt.X("existing:Q", title="Existing pair price (THB, avg billed)", scale=axis_scale),
    #     y=alt.Y("per_km_price:Q", title="Per-km price (THB)", scale=axis_scale),
    #     shape=alt.Shape("vehicle_class:N", title="Car type"),
    #     size=alt.Size("trips:Q", title="Trips", scale=alt.Scale(range=[30, 400])),
    #     color=alt.Color("Band:N", scale=band_scale, title="Band", sort=band_order),
    #     tooltip=[alt.Tooltip("vehicle_class:N", title="Car type"), alt.Tooltip("Route:N"),
    #              alt.Tooltip("km:Q", title="Road km", format=",.0f"),
    #              alt.Tooltip("per_km_price:Q", title="Per-km price", format=",.0f"),
    #              alt.Tooltip("existing:Q", title="Existing price", format=",.0f"),
    #              alt.Tooltip("diverge_pct:Q", title="Divergence %", format="+.1f"),
    #              alt.Tooltip("trips:Q", title="Trips")])
    # st.altair_chart((ref_line(1.0, [], 0.9) + ref_line(1 + tol / 100, [4, 4], 0.7)
    #                  + ref_line(max(1 - tol / 100, 0.01), [4, 4], 0.7) + cmp_dots
    #                  ).properties(height=420).interactive(), width="stretch")


def pair_divergence_section(per_km_src: pd.DataFrame):
    """Per origin -> destination + class: per-km price vs existing billed price, with drill-down."""
    st.subheader("Per KM and existing pair price")

    if per_km_src.empty:
        st.info("No trips with road km match this vehicle type / customer combination.")
        return

    # ── Car-type filter ──
    car_opts = sorted(per_km_src["vehicle_class"].unique())
    car_sel = st.multiselect("Car type", car_opts, default=car_opts, key="pair_div_car_type",
                             help="Show only these truck classes. All selected by default.")
    per_km_src = per_km_src[per_km_src["vehicle_class"].isin(car_sel)]
    if per_km_src.empty:
        st.info("Select at least one car type.")
        return

    # ── Build the table ──
    pairs = pair_divergence_frame(per_km_src)
    # st.caption("**Per km** = the class's THB/km (modified-Z cut-off) x the pair's median road km, compared with the "
    #            "**existing pair price** (plain average billed). **Divergence** = per-km price - existing pair price, "
    #            "in THB and as a % of the existing price; positive = per-km method quotes higher.")
    show = _pair_table_frame(pairs)

    # ── Controls: match tolerance + ranking ──
    rank_col, tol_col = st.columns([3, 1])
    tol = tol_col.number_input("Matched within (±%)", min_value=0.0, max_value=100.0, value=5.0, step=1.0,
                               key="pair_div_tol",
                               help="Per-km price counts as matched (blue) when its divergence is within this "
                                    "many % of the existing pair price. Above it is red, below it is green.")
    rank_by = rank_col.radio("Rank divergence by", ["Default order", "Per-km highest above (%)", "Per-km furthest below (%)",
                                                    "Largest gap either way (%)"], horizontal=True, key="pair_div_rank",
                             help="Sort the pair table by how far the per-km price is from the existing pair price.")

    if rank_by != "Default order":
        pct = show["Divergence (%)"]
        key = {"Per-km highest above (%)": -pct, "Per-km furthest below (%)": pct}.get(rank_by, -pct.abs())
        show = show.loc[key.sort_values(kind="stable").index].reset_index(drop=True)
        show.insert(0, "Rank", range(1, len(show) + 1))

    # Remember which pair each row is, before repeated labels are blanked out
    row_keys = list(zip(show["Car Type"], show["Ori"], show["Dest"]))

    if rank_by == "Default order":
        blank_repeated_labels(show)

    n_pairs = len(show)
    show = pd.concat([show, pd.DataFrame([_pair_summary_row(show)])], ignore_index=True)

    # ── Styling: blue = matched, red = per-km higher, green = per-km lower ──
    def _div_color(pct):
        if pd.isna(pct):
            return ""
        if abs(pct) <= tol:
            return f"background-color: {COLOR_BLUE}"
        return f"background-color: {COLOR_RED if pct > 0 else COLOR_GREEN}"

    styled = show.style.apply(
        lambda col: [_div_color(p) for p in show["Divergence (%)"]], subset=["Divergence (%)", "Divergence (THB)"])

    # ── Table (click a row to drill into its invoices) ──
    st.caption(f"{n_pairs:,} pairs shown. Last row sums Per-km price and Existing pair price over every pair shown; "
               "its divergence is the total gap. Divergence cells: 🔵 blue = per-km price matches the existing pair "
               f"price (within ±{tol:g}%), 🔴 red = per-km price is more than {tol:g}% higher, 🟢 green = per-km "
               f"price is more than {tol:g}% lower.")
    st.caption("Click a row to list the billed trips (invoices) behind its existing pair price.")

    sel = st.dataframe(styled, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                       key="pair_div_table", column_config={
        **MAP_CFG, "Existing pair price (min-max)": OTHER_CFG["Existing pair price (min-max)"],
        "Per km (THB/km)": NumCol("Per km (THB/km)", format="%,.2f"),
        "Per-km price": NumCol("Per-km price", format="%,.0f"),
        "Existing pair price": NumCol("Existing pair price", format="%,.0f"),
        "Divergence (THB)": NumCol("Divergence (THB)", format="%+,.0f"),
        "Divergence (%)": NumCol("Divergence (%)", format="%+.1f"),
        "Trips": NumCol("Trips", format="%d"), "Rank": NumCol("Rank", format="%d")})

    # The SUMMARY row (last) has no invoices, so only real pair rows count
    picked_rows = [i for i in sel.selection.rows if i < n_pairs]
    if picked_rows:
        _pair_invoices(per_km_src, *row_keys[picked_rows[0]])

    # st.download_button("Download table (CSV)", show.to_csv(index=False).encode("utf-8-sig"),
    #                    file_name="per_km_vs_existing_pair_price.csv", mime="text/csv", key="pair_div_download")

    # ── Charts ──
    _pair_charts(pairs, tol)

    # st.markdown("**Pivot by vehicle class**")
    # metric = st.radio("Pivot value", ["Divergence (%)", "Divergence (THB)", "Existing pair price", "Per-km price"],
    #                   horizontal=True, key="pair_div_pivot_metric")
    # pivot = table.pivot_table(index=["Ori", "Dest"], columns="Car Type", values=metric, aggfunc="mean")
    # fmt = "%+.1f" if metric == "Divergence (%)" else "%+,.0f" if metric == "Divergence (THB)" else "%,.0f"
    # pivot = pivot.reset_index()
    # for col in ["Ori", "Dest"]:   # show a repeated place only on the first of consecutive rows
    #     pivot.loc[pivot[col] == pivot[col].shift(), col] = ""
    # st.dataframe(pivot, hide_index=True, width="stretch",
    #              column_config={c: NumCol(c, format=fmt) for c in pivot.columns if c not in ("Ori", "Dest")})
