"""
Map เลขตัวถังรถ (VIN/chassis number) -> vehicle -> drivers -> inspections -> repair tickets.

VIN lives only in excel/ข้อมูลรถ 250969.xlsx (keyed by license plate, ทะเบียน).
None of the CSV export tables carry a VIN column, so the join goes:

    VIN (xlsx, key: license_plate)
      -> vehicles.license_plate -> vehicles.id
        -> inspection_logs.vehicle_id -> inspection_logs.id (+ driver_id)
          -> repair_tickets.inspection_log_id -> repair_tickets (started_at, completed_at)

Output: output/vin_mapping.json, one object per VIN.
"""
import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CSV_DIR = ROOT / "reference" / "drive-download-20260926T043613Z-1-001"
XLSX_PATH = ROOT / "excel" / "ข้อมูลรถ 250969.xlsx"
OUT_PATH = ROOT / "output" / "vin_mapping.json"


def normalize_plate(plate) -> str:
    """Normalize a license plate string so xlsx/CSV variants match (strip spaces, unify dashes)."""
    if plate is None or (isinstance(plate, float) and pd.isna(plate)):
        return ""
    s = str(plate).strip()
    s = re.sub(r"\s+", "", s)
    s = s.replace("–", "-").replace("—", "-")
    return s


def to_iso(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return None
    return ts.isoformat()


def repair_duration_minutes(started_at, completed_at):
    start = pd.Timestamp(started_at) if started_at is not None else pd.NaT
    end = pd.Timestamp(completed_at) if completed_at is not None else pd.NaT
    if pd.isna(start) or pd.isna(end):
        return None
    return round((end - start).total_seconds() / 60, 1)


def load_vehicle_vin_table() -> pd.DataFrame:
    df = pd.read_excel(XLSX_PATH, sheet_name="ALL", header=1)
    df = df.rename(
        columns={
            "ทะเบียน": "license_plate",
            "เลขตัวถังรถ": "vin",
            "เลขเครื่องยนต์": "engine_no",
            "ยี่ห้อรถ": "vin_brand",
            "รุ่นรถ": "vin_model",
            "เชื้อเพลิง": "fuel",
            "ประเภทรถ": "vin_vehicle_type",
            "พื้นที่": "vin_site",
        }
    )
    df = df.dropna(subset=["vin"])
    df["plate_key"] = df["license_plate"].map(normalize_plate)
    return df[
        ["plate_key", "license_plate", "vin", "engine_no", "vin_brand", "vin_model", "fuel", "vin_vehicle_type", "vin_site"]
    ]


def main():
    vin_table = load_vehicle_vin_table()
    vin_by_plate = {row.plate_key: row for row in vin_table.itertuples(index=False)}

    vehicles = pd.read_csv(CSV_DIR / "vehicles_256909251502.csv")
    vehicles["plate_key"] = vehicles["license_plate"].map(normalize_plate)

    drivers = pd.read_csv(CSV_DIR / "drivers_256909251459.csv")
    drivers_by_id = {row.id: row for row in drivers.itertuples(index=False)}

    inspections = pd.read_csv(CSV_DIR / "inspection_logs_256909251500.csv")
    repairs = pd.read_csv(CSV_DIR / "repair_tickets_256909251500.csv")
    repairs_by_inspection = {}
    for row in repairs.itertuples(index=False):
        repairs_by_inspection.setdefault(row.inspection_log_id, []).append(row)

    inspections_by_vehicle = {}
    for row in inspections.itertuples(index=False):
        inspections_by_vehicle.setdefault(row.vehicle_id, []).append(row)

    result = {}
    unmatched_vehicles = []
    total_inspections_mapped = 0
    total_repairs_mapped = 0
    repairs_with_null_duration = 0

    for vrow in vehicles.itertuples(index=False):
        vin_row = vin_by_plate.get(vrow.plate_key)
        if vin_row is None:
            unmatched_vehicles.append(vrow.license_plate)
            continue

        vehicle_inspections = inspections_by_vehicle.get(vrow.id, [])
        driver_ids_seen = sorted({i.driver_id for i in vehicle_inspections if pd.notna(i.driver_id)})

        drivers_list = []
        for did in driver_ids_seen:
            d = drivers_by_id.get(int(did))
            if d is not None:
                drivers_list.append(
                    {
                        "driver_id": int(did),
                        "full_name": d.full_name,
                        "employee_id": d.employee_id,
                        "phone_number": d.phone_number,
                    }
                )

        inspections_list = []
        for irow in vehicle_inspections:
            tickets = repairs_by_inspection.get(irow.id, [])
            tickets_list = []
            for trow in tickets:
                tickets_list.append(
                    {
                        "ticket_id": int(trow.id),
                        "ticket_no": trow.ticket_no,
                        "status": trow.status,
                        "repair_type": trow.repair_type,
                        "vendor_name": trow.vendor_name if pd.notna(trow.vendor_name) else None,
                        "repair_cost": trow.repair_cost if pd.notna(trow.repair_cost) else None,
                        "started_at": to_iso(trow.started_at),
                        "completed_at": to_iso(trow.completed_at),
                        "repair_duration_minutes": repair_duration_minutes(trow.started_at, trow.completed_at),
                    }
                )
                total_repairs_mapped += 1
                if tickets_list[-1]["repair_duration_minutes"] is None:
                    repairs_with_null_duration += 1

            inspections_list.append(
                {
                    "inspection_id": int(irow.id),
                    "inspection_date": to_iso(irow.inspection_date),
                    "driver_id": int(irow.driver_id) if pd.notna(irow.driver_id) else None,
                    "overall_result": irow.overall_result if pd.notna(irow.overall_result) else None,
                    "is_passed": bool(irow.is_passed) if pd.notna(irow.is_passed) else None,
                    "repair_tickets": tickets_list,
                }
            )
            total_inspections_mapped += 1

        result[vin_row.vin] = {
            "license_plate": vin_row.license_plate,
            "engine_no": vin_row.engine_no if pd.notna(vin_row.engine_no) else None,
            "vehicle_brand": vin_row.vin_brand if pd.notna(vin_row.vin_brand) else None,
            "vehicle_model": vin_row.vin_model if pd.notna(vin_row.vin_model) else None,
            "fuel": vin_row.fuel if pd.notna(vin_row.fuel) else None,
            "vehicle_type": vin_row.vin_vehicle_type if pd.notna(vin_row.vin_vehicle_type) else None,
            "site": vin_row.vin_site if pd.notna(vin_row.vin_site) else None,
            "vehicle_id": int(vrow.id),
            "vehicle_code": vrow.vehicle_code if pd.notna(vrow.vehicle_code) else None,
            "drivers": drivers_list,
            "inspections": inspections_list,
        }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"VINs mapped: {len(result)}")
    print(f"Vehicles with no VIN match: {len(unmatched_vehicles)}")
    if unmatched_vehicles:
        print(f"  unmatched license plates: {unmatched_vehicles}")
    print(f"Inspections mapped: {total_inspections_mapped}")
    print(f"Repair tickets mapped: {total_repairs_mapped} (null duration: {repairs_with_null_duration})")
    print(f"Output written to: {OUT_PATH}")


if __name__ == "__main__":
    main()
