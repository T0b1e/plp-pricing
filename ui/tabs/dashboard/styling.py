"""Dashboard table styling: diverging-row detection, SUMMARY row, highlighting."""
import numpy as np
import pandas as pd

from .constants import AVG_COLS, COLOR_AMBER, COLOR_GREEN, COLOR_RED, N_COLS
from .stats import gas_divergence_pct


def diverging_mask(df: pd.DataFrame, tol: float = 0.005, margin: float | None = None) -> pd.Series:
    """True where a cut-off mean moved away from the before-cut-off mean by more than either a
    relative tolerance (tol, default 0.5%) or an absolute margin (e.g. 20 THB/km) - i.e. that
    row actually had outliers trimmed, as opposed to a flat/too-small-to-trim group. `margin` suits
    per-km THB values better than a relative %, since a THB/km gap is meaningful at a fixed size
    regardless of whether the average itself is small or large."""
    before = df["Avg.Price (before cut-off)"]
    threshold = margin if margin is not None else tol * before.abs().clip(lower=1)
    moved = pd.Series(False, index=df.index)

    # Compare each "after cut-off" mean against the untrimmed one
    for col in ["Avg.Price (3-sigma, after cut-off)", "Avg.Price (modi-Z, after cut-off)"]:
        moved |= (df[col] - before).abs() > threshold

    return moved


def with_summary_row(df: pd.DataFrame, unit: str = "routes") -> pd.DataFrame:
    """Append a SUMMARY row: total trips + trip-weighted average price per method, overall trimmed %."""
    if df.empty:
        return df

    summary = {"Car Type": f"SUMMARY ({len(df):,} {unit})"}

    # Trip-weighted mean = sum(avg x N) / sum(N), one per cut-off method
    for avg_col, n_col in zip(AVG_COLS, N_COLS):
        n_sum = df[n_col].sum()
        summary[n_col] = n_sum
        summary[avg_col] = (df[avg_col] * df[n_col]).sum() / n_sum if n_sum else np.nan

    return pd.concat([df, pd.DataFrame([summary])], ignore_index=True)


def highlight_diverging(df: pd.DataFrame, margin: float | None = None):
    """Style rows amber where a cut-off trimmed something, and colour the Gas Price Divergence
    cell red / green when the billed fuel rate is > 5% above / below EPPO's price."""
    moved = diverging_mask(df, margin=margin)
    gas_pct = gas_divergence_pct(df)
    gas_col = "Gas Price Divergence"
    pct_tol = 5.0

    def _row(row):
        # The SUMMARY row is never highlighted
        if str(row["Car Type"]).startswith("SUMMARY"):
            return [""] * len(row)

        styles = [f"background-color: {COLOR_AMBER}" if moved.loc[row.name] else "" for _ in row.index]

        # Gas divergence cell: red = billed above EPPO, green = below
        if gas_col in row.index:
            pct = gas_pct.loc[row.name]
            if pd.notna(pct) and abs(pct) > pct_tol:
                color = COLOR_RED if pct > 0 else COLOR_GREEN
                styles[row.index.get_loc(gas_col)] = f"background-color: {color}"

        return styles

    return df.style.apply(_row, axis=1)


def blank_repeated_labels(df: pd.DataFrame, has_summary: bool = False) -> None:
    """In place: show Car Type / Ori only on the first of consecutive rows that repeat them."""
    dup_car = df["Car Type"] == df["Car Type"].shift()
    dup_ori = dup_car & (df["Ori"] == df["Ori"].shift())

    if has_summary:
        # Keep the SUMMARY label, and leave a missing Ori as-is
        df.loc[dup_ori & (df["Ori"].notna()), "Ori"] = ""
        df.loc[dup_car & ~df["Car Type"].str.startswith("SUMMARY"), "Car Type"] = ""
    else:
        df.loc[dup_ori, "Ori"] = ""
        df.loc[dup_car, "Car Type"] = ""
