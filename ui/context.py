"""What every tab needs: the (status-filtered) trips, places, and the optional price model."""
from dataclasses import dataclass

import pandas as pd


@dataclass
class Ctx:
    trips: pd.DataFrame
    locs: pd.DataFrame
    summary: pd.DataFrame | None
    rates: pd.DataFrame | None
    statuses: tuple[str, ...]   # selected job statuses; part of the cache key for trip-derived stats
