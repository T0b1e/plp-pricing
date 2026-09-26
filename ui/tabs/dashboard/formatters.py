"""Dashboard text formatters: Google Maps link and the EPPO / gas-price table cells."""
import pandas as pd


def maps_route_url(o_latlon: str, d_latlon: str) -> str | None:
    """o_latlon/d_latlon are 'lat, lon' (from the geocode cache) or '' if never geocoded."""
    if not o_latlon or not d_latlon:
        return None

    o_lat, o_lon = (x.strip() for x in o_latlon.split(","))
    d_lat, d_lon = (x.strip() for x in d_latlon.split(","))

    return (f"https://www.google.com/maps/dir/?api=1&origin={o_lat},{o_lon}"
            f"&destination={d_lat},{d_lon}&travelmode=driving")


def gas_price_base_text(resolved: tuple[float, float, float, bool] | None) -> str | None:
    """EPPO base price of a resolved divergence, tagged '(est.)' when it came from the fallback."""
    if resolved is None:
        return None

    _, _, base, estimated = resolved

    return f"{base:.2f}{' (est.)' if estimated else ''}"


def gas_divergence_text(resolved: tuple[float, float, float, bool] | None) -> str | None:
    """Signed divergence cell, e.g. '+1.20 (+3.5%)' (with '(est.)' when from the fallback)."""
    if resolved is None:
        return None

    diff, pct, _, estimated = resolved
    sign = "+" if diff >= 0 else ""

    return f"{sign}{diff:.2f} ({sign}{pct:.1f}%){' (est.)' if estimated else ''}"


def gas_median_range_text(g: pd.DataFrame) -> str | None:
    """Median billed top-of-band fuel rate with the min-max across this route's trips,
    e.g. '34.50 (32.00-36.00)'. None where no trip carries a fuel clause."""
    lo, hi = g["fuel_rate_low"].dropna(), g["fuel_rate_high"].dropna()
    if lo.empty or hi.empty:
        return None

    return f"{hi.median():.2f} ({lo.min():.2f}-{hi.max():.2f})"


def eppo_date_text(dates: pd.Series) -> str | None:
    """Publish date(s) of the EPPO readings behind a route's base price, e.g. '20-Apr-2026' or
    '02-Jan-2026 to 20-Apr-2026' when its trips matched different readings."""
    d = dates.dropna()
    if d.empty:
        return None

    lo, hi = d.min().strftime("%d-%b-%Y"), d.max().strftime("%d-%b-%Y")

    return lo if lo == hi else f"{lo} to {hi}"


def eppo_price_text(g: pd.DataFrame) -> str:
    """Median EPPO price, or the reason there isn't one for this route."""
    if g["eppo_price"].notna().any():
        return f"{g['eppo_price'].median():.2f}"
    if g["ship_date"].isna().all():
        return "n/a - trips have no ship date"
    if g["eppo_date"].isna().all():
        return "n/a - no EPPO data loaded (run scripts/fetch_eppo_diesel.py)"

    return "n/a - EPPO reading has no price"


def eppo_price_range_text(g: pd.DataFrame) -> str | None:
    """Min-max of the EPPO prices matched to this route's trips, e.g. '32.94-34.94'."""
    p = g["eppo_price"].dropna()

    return None if p.empty else f"{p.min():.2f}-{p.max():.2f}"
