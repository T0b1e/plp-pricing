"""Dashboard stats helpers: per-route / per-class price statistics and fuel divergence."""
import numpy as np
import pandas as pd

from ui.config import NumCol
from ui.stats import cutoff_price_stats

from .constants import AVG_COLS, N_COLS


def stats_row(values: np.ndarray) -> dict:
    """One table row of mean + N for each cut-off method (none, 3-sigma, modified-Z)."""
    s = cutoff_price_stats(values)

    return {"Avg.Price (before cut-off)": s["avg"], "N (before cut-off)": s["n"],
            "Avg.Price (3-sigma, after cut-off)": s["avg_3sigma"], "N (3-sigma, after cut-off)": s["n_3sigma"],
            "Avg.Price (modi-Z, after cut-off)": s["avg_modiz"], "N (modi-Z, after cut-off)": s["n_modiz"]}


def dash_column_config(price_help: str, decimals: int = 0) -> dict:
    """Column configs for the price (THB or THB/km) and N columns of the stats tables."""
    cfg = {label: NumCol(label, help=price_help, format=f"%,.{decimals}f") for label in AVG_COLS}
    cfg.update({label: NumCol(label, help="Trips that mean is based on.", format="%d") for label in N_COLS})

    return cfg


def gas_divergence_value(g: pd.DataFrame) -> tuple[float, float, float] | None:
    """(diff, pct, eppo_base) median (billed top-of-band fuel rate) - (EPPO HSD B7 price on that
    trip's ship date), THB/litre and as a % of the EPPO price, plus the EPPO base price itself.
    None where no row has both sides present."""
    mask = g["fuel_rate_low"].notna() & g["fuel_rate_high"].notna() & g["eppo_price"].notna()
    if not mask.any():
        return None

    base = g.loc[mask, "eppo_price"].median()
    diff = (g.loc[mask, "fuel_rate_high"] - g.loc[mask, "eppo_price"]).median()
    pct = diff / base * 100 if base else np.nan

    return diff, pct, base


def gas_divergence_resolved(
    g: pd.DataFrame, fallback: tuple[float, float, float] | None = None
) -> tuple[float, float, float, bool] | None:
    """(diff, pct, base, estimated) for this route, falling back to a same-car-type/global
    estimate when this route has no trip with both a billed fuel rate and an EPPO price."""
    own = gas_divergence_value(g)
    if own is not None:
        return (*own, False)
    if fallback is not None:
        return (*fallback, True)

    return None


def gas_divergence_pct(df: pd.DataFrame) -> pd.Series:
    """Parsed +/-% out of the 'Gas Price Divergence' text cell, NaN where blank/absent."""
    if "Gas Price Divergence" not in df.columns:
        return pd.Series(np.nan, index=df.index)

    return (df["Gas Price Divergence"].fillna("")
            .str.extract(r"\(([+-]?[\d.]+)%\)", expand=False).astype(float))
