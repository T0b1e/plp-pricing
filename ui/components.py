"""Small shared widgets and styles."""
import pandas as pd
import streamlit as st

from src.estimate import estimate
from src.fit_model import ALL
from ui.config import MEDAL_COLORS


def _tex_num(x: float, decimals: int = 0) -> str:
    """1234.5 -> '1{,}235' so LaTeX doesn't add a space after the thousands comma."""
    return f"{x:,.{decimals}f}".replace(",", "{,}")


def formula_card(km: float, stops: int, vehicle: str | None, summary: pd.DataFrame, rates: pd.DataFrame):
    """Card above the model table: the formula, what each term means, and this km worked through."""
    with st.expander("🧮 How the estimate is calculated", expanded=False):
        st.latex(r"\text{Price (THB/trip)} = \text{Base fare} + \text{Rate per km} \times \text{Road km}"
                 r" + \text{Drop fee} \times \text{Extra drops}")
        t1, t2, t3, t4 = st.columns(4)
        t1.markdown("**Base fare** (THB)  \nFixed part of every trip, even a very short one: loading, "
                    "waiting, minimum charge.")
        t2.markdown("**Rate per km** (THB/km)  \nHow much the price rises for each extra km driven.")
        t3.markdown("**Road km** (km)  \nGoogle driving distance; multi-stop trips add up every leg "
                    "A→B→C.")
        t4.markdown("**Extra drops** (count)  \nStops beyond a plain A→B trip = stops − 2. "
                    "Drop fee is THB per extra drop.")

        # worked example: the chosen truck, or the class with the most trips
        name = vehicle or summary.loc[summary["vehicle_class"] != ALL].sort_values("n", ascending=False)[
            "vehicle_class"].iloc[0]
        e = estimate(km, name, stops, summary, rates)
        extra = max(stops - 2, 0)
        drop = ""
        if e["drop_fee"] and extra:
            sign = "-" if e["drop_fee"] < 0 else "+"
            drop = rf" {sign} {_tex_num(abs(e['drop_fee']))} \times {extra}"
        st.markdown(f"**Worked example: {name}, {km:,.0f} km, {stops} stops**"
                    + ("" if vehicle else " (the truck class with the most trips; each row in the table "
                                          "uses its own numbers)"))
        st.latex(rf"{_tex_num(e['base_fare'])} + {_tex_num(e['rate_per_km'], 2)} \times {_tex_num(km)}"
                 rf"{drop} = \mathbf{{{_tex_num(e['price'])}}}\ \text{{THB}}")

        st.markdown(
            f"- **Low – High** ({e['low']:,.0f} – {e['high']:,.0f} THB here): the normal range. About 8 in "
            f"10 past bills fell inside it once scaled to their own estimate.\n"
            f"- **Typical error** ({e['test_mdape_pct']:.0f}% here): how far off the model was on trips it "
            f"was never shown. Lower = more trustworthy.\n"
            f"- **Based on**: *Own trips* = the line is built from this truck class's own bills; "
            f"*Borrowed – few trips* = under 30 bills, so the all-trucks line is adjusted to fit. "
            f"Rough guide only.\n"
            f"- Reefer prices follow fixed per-route contracts, so their ranges are wider than for dry "
            f"trucks. For a route billed before, the real price in tab 1 beats this estimate.")


def medal_rank_style(df: pd.DataFrame):
    """Styler for a df with a 'rank' column: top 3 ranks get a gold/silver/bronze square badge."""
    def _cell(v):
        color = MEDAL_COLORS.get(v)
        if not color:
            return ""
        return (f"background-color: {color}; color: #1a1a1a; font-weight: 700; "
                f"text-align: center; border-radius: 3px;")
    return df.style.map(_cell, subset=["rank"])


def reload_button():
    if st.button("Reload data"):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.rerun()


def need_model():
    """Shown wherever a model estimate is requested but data/model_summary.csv hasn't been built yet."""
    st.info("The price model is not built yet (no `data/model_summary.csv`). Finish geocoding, "
            "then run `python main.py --from distance`. Historical prices by route still work.")


def model_failed(e: Exception):
    """Shown wherever estimate()/model_table() raises - usually a stale or malformed model file."""
    st.error(f"Model estimate failed: {type(e).__name__}: {e}")
    st.caption("`data/model_summary.csv` may be from an older pipeline version - rerun "
               "`python main.py --from model`, then Reload data.")
