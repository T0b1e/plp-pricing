"""Supervisor trip table: one row per billed trip, every source row kept.

    data/trip_table.xlsx   (Thai + English headers, filters, frozen header)
    data/trip_table.csv    (same content, UTF-8 for other tools)

Columns: date, job, customer, vehicle, origin + lat/lon, destination + lat/lon, stops,
road km, fuel-rate bracket (ราคาน้ำมัน), transport price, plus how reliable the
coordinates and km are.
"""
import pandas as pd
from openpyxl.utils import get_column_letter

from .aliases import canonical_map
from .files import DataFileError, read_csv, write_csv
from .paths import DATA, LOCATIONS, TRIPS_CLEAN

TRIP_XLSX = DATA / "trip_table.xlsx"
TRIP_CSV = DATA / "trip_table.csv"

COLUMNS = {   # trips_clean column -> header
    "ship_date": "วันที่ขึ้นสินค้า (ship date)",
    "job_order_no": "เลขที่ใบสั่งปฏิบัติงาน (job no.)",
    "customer": "ลูกค้า (customer)",
    "vehicle_type": "ประเภทรถ (vehicle)",
    "origin": "ต้นทาง (origin)",
    "origin_latlon": "ต้นทาง lat, lon (origin)",
    "origin_lat": "ต้นทาง lat",
    "origin_lon": "ต้นทาง lon",
    "destination": "ปลายทาง (destination)",
    "dest_latlon": "ปลายทาง lat, lon (destination)",
    "dest_lat": "ปลายทาง lat",
    "dest_lon": "ปลายทาง lon",
    "stops": "เส้นทางทุกจุด (all stops)",
    "stop_count": "จำนวนจุด (stops)",
    "total_km": "ระยะทางประมาณ กม. (route km)",
    "km_source": "ที่มาระยะทาง (km source)",
    "fuel_rate_bracket": "ราคาน้ำมัน ช่วงเรท บาท/ลิตร (fuel rate bracket)",
    "fuel_rate_low": "ราคาน้ำมัน ต่ำสุด (fuel low)",
    "fuel_rate_high": "ราคาน้ำมัน สูงสุด (fuel high)",
    "price": "ราคาขนส่ง บาท (transport price)",
    "origin_confidence": "ความแม่นพิกัดต้นทาง (origin geocode)",
    "dest_confidence": "ความแม่นพิกัดปลายทาง (dest geocode)",
    "route_raw": "เส้นทางขนส่ง ต้นฉบับ (original AB)",
    "source_file": "ไฟล์ต้นฉบับ (source file)",
}


def build() -> pd.DataFrame:
    df = read_csv(TRIPS_CLEAN, dtype={"job_order_no": str})
    loc = read_csv(LOCATIONS, dtype=str).fillna("")
    conf = dict(zip(loc["token"], loc["geocode_confidence"].replace("", "not geocoded")))
    canon = canonical_map()
    for end, col in [("origin", "origin"), ("dest", "destination")]:
        df[f"{end}_confidence"] = df[col].map(lambda t: conf.get(canon.get(t, t), "") if isinstance(t, str) else "")
    for end in ["origin", "dest"]:
        # "13.584565, 100.276712" - pastes straight into Google Maps search
        df[f"{end}_latlon"] = [f"{la:.6f}, {lo:.6f}" if pd.notna(la) and pd.notna(lo) else ""
                               for la, lo in zip(df[f"{end}_lat"], df[f"{end}_lon"])]
    df["ship_date"] = pd.to_datetime(df["ship_date"]).dt.date
    df["total_km"] = df["total_km"].round(1)
    # trips with a located route first, then by date; rows with no route in the report sink to the bottom
    df["_no_route"] = df["origin_latlon"].eq("") | df["dest_latlon"].eq("")
    df = df.sort_values(["_no_route", "ship_date", "job_order_no"])
    return df[list(COLUMNS)].rename(columns=COLUMNS)


def write_xlsx(t: pd.DataFrame):
    with pd.ExcelWriter(TRIP_XLSX, engine="openpyxl") as xw:
        t.to_excel(xw, sheet_name="trips", index=False)
        ws = xw.sheets["trips"]
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for i, col in enumerate(t.columns, 1):
            sample = t[col].head(500).astype(str).str.len().fillna(0)
            width = min(max(len(col) * 0.9, sample.quantile(0.9) if len(sample) else 10) + 2, 50)
            ws.column_dimensions[get_column_letter(i)].width = width
        money = list(t.columns).index(COLUMNS["price"]) + 1
        for (cell,) in ws.iter_rows(min_row=2, min_col=money, max_col=money):
            cell.number_format = "#,##0.00"


def main():
    t = build()
    write_csv(t, TRIP_CSV, index=False)
    try:
        write_xlsx(t)
    except PermissionError:
        raise DataFileError(f"Cannot write {TRIP_XLSX.name} - it is probably open in Excel. "
                            f"Close it and rerun this step.") from None
    km = t[COLUMNS["total_km"]].notna()
    fuel = t[COLUMNS["fuel_rate_bracket"]].fillna("") != ""
    print(f"[export] {len(t):,} trips -> {TRIP_XLSX.name}, {TRIP_CSV.name}")
    print(f"         with origin+destination coordinates and km: {km.sum():,} ({km.mean():.0%})")
    print(f"         with fuel-rate bracket: {fuel.sum():,} ({fuel.mean():.0%})")


if __name__ == "__main__":
    main()
