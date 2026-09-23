"""Reconcile row counts and money totals per source file across every pipeline stage.

The source workbooks are re-read here with openpyxl directly (not via load.py), so a
loader bug cannot hide itself. Every stage must carry the same rows and the same
price / contractor_cost totals as the xlsx; the model's training set is then broken
down by exclusion reason so used + excluded adds back up to the source.

    python -m src.checksum
"""
from decimal import Decimal, InvalidOperation

import openpyxl
import pandas as pd

from .files import DataFileError, write_csv
from .paths import DATA, SOURCE_FILES, TRIPS_CLEAN, TRIPS_PARSED, TRIPS_RAW
from .vehicles import vehicle_class

CHECKSUM = DATA / "checksum.csv"
EXCLUSIONS = DATA / "checksum_exclusions.csv"
COL_PRICE, COL_CONTRACTOR, COL_JOB = 19, 34, 1    # T, AI, B (0-based)
MONEY = ["price", "contractor_cost"]


def dec(v) -> Decimal:
    if v is None or v == "":
        return Decimal(0)
    try:
        return Decimal(str(v).replace(",", ""))
    except InvalidOperation:
        return Decimal(0)


def read_source(path) -> dict:
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as e:
        raise DataFileError(f"Cannot open {path.name}: {type(e).__name__}: {e}") from e
    ws = wb.worksheets[0]
    rows, price, contractor, jobs = 0, Decimal(0), Decimal(0), set()
    for r in ws.iter_rows(min_row=3, values_only=True):
        if all(v is None or str(v).strip() == "" for v in r):
            continue
        rows += 1
        price += dec(r[COL_PRICE])
        contractor += dec(r[COL_CONTRACTOR])
        jobs.add(r[COL_JOB])
    wb.close()
    return {"rows": rows, "price": price, "contractor_cost": contractor, "distinct_jobs": len(jobs)}


def stage_totals(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("source_file")
    out = g.size().rename("rows").to_frame()
    for m in MONEY:
        out[m] = g[m].apply(lambda s: sum((dec(x) for x in s if pd.notna(x)), Decimal(0)))
    out["distinct_jobs"] = g["job_order_no"].nunique()
    return out


def exclusion_reason(df: pd.DataFrame) -> pd.Series:
    """Why each row is (not) in the model training set. Order = first reason that applies."""
    vc = df["vehicle_type"].fillna("").map(vehicle_class)
    reason = pd.Series("used_in_model", index=df.index)
    km_missing = df["total_km"].isna() | (df["total_km"] <= 0) if "total_km" in df else True
    for mask, label in [
        (df["price"].le(0) | df["price"].isna(), "price_zero_or_blank"),
        (km_missing, "km_missing (ungeocoded stop / failed leg / same place)"),
        (vc.isna(), "vehicle_excluded (S3_DUMMY_HL_TN / blank)"),
        (df["stop_count"] == 1, "single_stop (fee or distribution line, no destination)"),
        (df["stop_count"] == 0, "no_route (column AB blank)"),
    ]:
        reason[mask] = label       # later rules win, so the most basic reason is reported
    return reason


def main():
    print(f"[checksum] reading {len(SOURCE_FILES)} source workbooks directly ...")
    src = pd.DataFrame({p.name: read_source(p) for p in SOURCE_FILES}).T
    src.index.name = "source_file"

    stages = {"xlsx": src}
    for name, path in [("trips_raw", TRIPS_RAW), ("trips_parsed", TRIPS_PARSED), ("trips_clean", TRIPS_CLEAN)]:
        if not path.exists():
            print(f"  (stage {name} not built yet - skipped)")
            continue
        df = pd.read_parquet(path) if path.suffix == ".parquet" else \
            pd.read_csv(path, encoding="utf-8-sig", dtype={"job_order_no": str})
        stages[name] = stage_totals(df)
    from .export import COLUMNS, TRIP_CSV
    if TRIP_CSV.exists():   # trip table has Thai headers and no contractor cost; map back and compare rows + price
        back = {v: k for k, v in COLUMNS.items()}
        df = pd.read_csv(TRIP_CSV, encoding="utf-8-sig", dtype={COLUMNS["job_order_no"]: str}).rename(columns=back)
        df["contractor_cost"] = None
        t = stage_totals(df)
        t["contractor_cost"] = src["contractor_cost"]   # not carried; excluded from the match below
        stages["trip_table"] = t

    rows, ok = [], True
    for stage, t in stages.items():
        for f in src.index:
            r = t.loc[f] if f in t.index else pd.Series({"rows": 0, "price": Decimal(0),
                                                         "contractor_cost": Decimal(0), "distinct_jobs": 0})
            match = all(r[c] == src.loc[f, c] for c in ["rows", "price", "contractor_cost"])
            ok &= match
            rows.append({"stage": stage, "source_file": f, "rows": int(r["rows"]),
                         "distinct_jobs": int(r["distinct_jobs"]),
                         "price_sum": r["price"], "contractor_cost_sum": r["contractor_cost"],
                         "matches_xlsx": "OK" if match else "MISMATCH"})
    out = pd.DataFrame(rows)
    total = (out.groupby("stage", sort=False)[["rows", "distinct_jobs", "price_sum", "contractor_cost_sum"]]
             .sum().reset_index().assign(source_file="TOTAL"))
    total["matches_xlsx"] = [("OK" if (out[out.stage == s].matches_xlsx == "OK").all() else "MISMATCH")
                             for s in total.stage]
    out = pd.concat([out, total], ignore_index=True)
    write_csv(out, CHECKSUM, index=False)

    pd.set_option("display.width", 200)
    fmt = out.copy()
    for c in ["price_sum", "contractor_cost_sum"]:
        fmt[c] = fmt[c].map(lambda d: f"{d:,.2f}")
    print(fmt[fmt.source_file == "TOTAL"].drop(columns="source_file").to_string(index=False))
    print("\nper file (xlsx vs last stage):")
    last = list(stages)[-1]
    print(fmt[(fmt.stage.isin(["xlsx", last])) & (fmt.source_file != "TOTAL")]
          .pivot(index="source_file", columns="stage", values=["rows", "price_sum"]).to_string())
    bad = out[out.matches_xlsx == "MISMATCH"]
    if len(bad):
        print("\n!! MISMATCHES:\n" + bad.to_string(index=False))

    # where the rows go between trips_clean and the model
    if TRIPS_CLEAN.exists():
        df = pd.read_csv(TRIPS_CLEAN, encoding="utf-8-sig")
        df["reason"] = exclusion_reason(df)
        ex = (df.groupby("reason").agg(rows=("price", "size"), price_sum=("price", "sum"))
              .sort_values("rows", ascending=False))
        ex.loc["TOTAL"] = ex.sum()
        ex["pct_rows"] = (ex["rows"] / ex.loc["TOTAL", "rows"] * 100).round(1)
        write_csv(ex, EXCLUSIONS)
        print("\nmodel coverage (trips_clean -> training set):")
        print(ex.assign(price_sum=ex.price_sum.map("{:,.2f}".format)).to_string())
        assert ex.loc["TOTAL", "rows"] == len(df)

    print(f"\n[checksum] {'ALL STAGES MATCH SOURCE' if ok else 'MISMATCH FOUND'} -> {CHECKSUM.name}")
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
