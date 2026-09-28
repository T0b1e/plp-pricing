"""Vehicles tab: VIN <-> license plate <-> driver directory, from scripts/map_vin_data.py."""
import streamlit as st

from ui.data import load_vin_mapping


def render(ctx):
    df = load_vin_mapping()
    st.caption("VIN (เลขตัวถังรถ), plate, and the driver(s) seen on that vehicle's inspection logs. "
               "Built by `scripts/map_vin_data.py` from the fleet export + vehicle registry.")

    if df.empty:
        st.info("No vehicle data found. Run `scripts/map_vin_data.py` to generate `output/vin_mapping.json`.")
        return

    n_drivers = len({d for row in df["drivers"] for d in row.split(", ") if d})
    m1, m2, m3 = st.columns(3)
    m1.metric("Vehicles", f"{len(df):,}")
    m2.metric("Drivers", f"{n_drivers:,}")
    m3.metric("Inspections mapped", f"{df['n_inspections'].sum():,}")

    query = st.text_input("Search (VIN, plate, or driver name)")
    if query:
        q = query.strip().lower()
        mask = (
            df["vin"].str.lower().str.contains(q, na=False)
            | df["license_plate"].str.lower().str.contains(q, na=False)
            | df["drivers"].str.lower().str.contains(q, na=False)
        )
        df = df[mask]

    st.dataframe(
        df.rename(columns={
            "vin": "VIN",
            "license_plate": "Plate",
            "vehicle_brand": "Brand",
            "vehicle_model": "Model",
            "vehicle_type": "Type",
            "site": "Site",
            "drivers": "Driver(s)",
            "n_inspections": "Inspections",
            "n_repair_tickets": "Repair tickets",
        }),
        hide_index=True,
        width="stretch",
    )
