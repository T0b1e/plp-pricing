"""Step 1: read the Report AR_AP workbooks into one tidy table.

Row 1 is the "Report AR_AP" title, row 2 the Thai header. Columns are named by
position because the header repeats (วันที่ขึ้นสินค้า appears in both C and X).
"""
import pandas as pd

from .files import DataFileError
from .paths import EXCEL, SOURCE_FILES, TRIPS_RAW

# Column A..AP, in order.
COLUMNS = [
    "bill_no",            # A  เลขที่บิล
    "job_order_no",       # B  เลขที่ใบสั่งปฏิบัติงาน
    "ship_date",          # C  วันที่ขึ้นสินค้า
    "saq_doc",            # D  SaqDoc.
    "shipment_no",        # E  Shipment No
    "status",             # F  Status
    "job_type",           # G  ประเภทใบงาน
    "bl",                 # H  BL
    "container_no",       # I  Container No
    "customer_code",      # J  รหัสลูกค้า
    "customer",           # K  ชื่อลูกค้า
    "customer_address",   # L  ที่อยู่
    "phone",              # M  เบอร์โทร
    "fax",                # N  Fax
    "credit_term",        # O  Credit Term
    "product_code",       # P  รหัสสินค้า
    "product_name",       # Q  ชื่อสินค้า
    "qty",                # R  จำนวน
    "unit",               # S  เที่ยว
    "price",              # T  ราคาขนส่ง
    "other_code",         # U  รหัสขนส่งอื่นๆ
    "other_name",         # V  ชื่อขนส่งอื่นๆ
    "other_price",        # W  ค่าขนส่งอื่นๆ
    "ship_date_2",        # X  วันที่ขึ้นสินค้า
    "expense_code",       # Y  รหัสค่าใช้จ่าย
    "expense_name",       # Z  ชื่อค่าใช้จ่าย
    "expense_price",      # AA ราคา
    "route_raw",          # AB เส้นทางขนส่ง
    "contractor_code",    # AC รหัสผู้รับเหมา
    "contractor",         # AD ชื่อผู้รับเหมา
    "driver_code",        # AE รหัสพนักงานขับรถ
    "driver",             # AF ชื่อพนักงานขับรถ
    "vehicle_type",       # AG ประเภทรถ
    "plate",              # AH ทะเบียนรถ
    "contractor_cost",    # AI ค่าใช้จ่ายผู้รับเหมา
    "driver_cost",        # AJ ค่าใช้จ่ายสำหรับคนรถ
    "remark",             # AK หมายเหตุ
    "acc_ref_1",          # AL
    "acc_ref_2",          # AM
    "acc_ref_3",          # AN
    "acc_ref_4",          # AO
    "created_by",         # AP ชื่อบัญชีผู้สร้าง
]

NUMERIC = ["qty", "price", "other_price", "expense_price", "contractor_cost", "driver_cost"]


def warn_unparsed(df: pd.DataFrame, col: str, parsed: pd.Series):
    """Cells that had text but did not convert become blank; say so instead of dropping them silently."""
    bad = df[col].notna() & (df[col].str.strip() != "") & parsed.isna()
    if bad.any():
        sample = df.loc[bad, [col, "source_file"]].head(3).to_dict("records")
        print(f"  !! {col}: {bad.sum():,} values could not be read and were left blank, e.g. {sample}")


def load_one(path) -> pd.DataFrame:
    try:
        df = pd.read_excel(path, header=None, skiprows=2, dtype=str)
    except PermissionError:
        raise DataFileError(f"{path.name} is locked - close it in Excel and rerun.") from None
    except Exception as e:   # truncated download, wrong format saved as .xlsx, ...
        raise DataFileError(f"Cannot read {path.name}: {type(e).__name__}: {e}") from e
    if df.shape[1] != len(COLUMNS):
        raise DataFileError(f"{path.name}: expected {len(COLUMNS)} columns (A..AP), got {df.shape[1]} - "
                            f"has the report layout changed?")
    df.columns = COLUMNS
    df = df.dropna(how="all")
    df["source_file"] = path.name
    return df


def load_all() -> pd.DataFrame:
    if not SOURCE_FILES:
        raise DataFileError(f"No 'Report AR_AP_*.xlsx' files found in {EXCEL}.")
    df = pd.concat([load_one(p) for p in SOURCE_FILES], ignore_index=True)
    for col in NUMERIC:
        num = pd.to_numeric(df[col], errors="coerce")
        warn_unparsed(df, col, num)
        df[col] = num
    for col in ["ship_date", "ship_date_2"]:
        dt = pd.to_datetime(df[col], format="%d/%m/%Y", errors="coerce")
        warn_unparsed(df, col, dt)
        df[col] = dt
    for col in ["route_raw", "vehicle_type", "job_type", "customer"]:
        df[col] = df[col].fillna("").str.strip()
    return df


def main():
    df = load_all()
    df.to_parquet(TRIPS_RAW, index=False)
    print(f"[load] {len(df):,} rows from {len(SOURCE_FILES)} files -> {TRIPS_RAW.name}")
    print(df.groupby("source_file").size().to_string())
    return df


if __name__ == "__main__":
    main()
