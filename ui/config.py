"""Column labels/tooltips for the tables and the chart palette."""
import streamlit as st


# ---------- column labels: unit in the header, meaning in the hover tooltip ----------

NumCol, TxtCol, DateCol = st.column_config.NumberColumn, st.column_config.TextColumn, st.column_config.DateColumn


def thb(label, help, decimals=0):
    return NumCol(f"{label} (THB)", help=help, format=f"%,.{decimals}f")


MEDAL_COLORS = {1: "#FFD700", 2: "#C0C0C0", 3: "#CD7F32"}   # gold, silver, bronze

# Fixed categorical order (never cycled/reordered) - validated for CVD-safe adjacent pairs.
CAT_PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
FUEL_BLUE = CAT_PALETTE[0]
MAX_FUEL_SERIES = 5


VEHICLE_HELP = ("Truck class from the bill: 4W/6W/10W/18W/22W = number of wheels; reefer = refrigerated, "
                "dry = not refrigerated, head = tractor head pulling a trailer/container")

STATUS_HELP = ("Job status from the source report: OPEN = job order created, not necessarily run or billed "
               "(closest to 'quoted'); POSTED = job done, not yet sent for billing; CONFIRM = sent for "
               "billing, complete. The sidebar's status filter defaults to CONFIRM only, since OPEN/POSTED "
               "jobs may never have shipped and can skew prices.")

STATS_CFG = {   # price_stats(): one row per vehicle class
    "vehicle_class": TxtCol("Vehicle class", help=VEHICLE_HELP),
    "trips": NumCol("Trips (count)", help="Number of billed trips in this group", format="%d"),
    "median": thb("Typical price (median)", "Middle billed price: half the trips were billed less, half more"),
    "p10": thb("Low estimate (10th percentile)", "10% of trips were billed at or below this - the cheap end"),
    "p90": thb("High estimate (90th percentile)", "90% of trips were billed at or below this - the expensive end"),
    "min": thb("Min price", "Lowest billed price in this group"),
    "max": thb("Max price", "Highest billed price in this group"),
    "last_price": thb("Last price", "Price on the most recent trip - usually the current contract rate"),
    "last_date": DateCol("Last trip date", help="Ship date of the most recent trip", format="DD/MM/YYYY"),
    "median_km": NumCol("Median road km (km)", format="%.0f",
                        help="Google road distance, all legs summed for multi-stop trips"),
    "fuel_rate_low": NumCol("Fuel rate low (THB/litre)", format="%.2f",
                            help="Median bottom-of-band diesel rate billed on this route + vehicle class"),
    "fuel_rate_high": NumCol("Fuel rate high (THB/litre)", format="%.2f",
                             help="Median top-of-band diesel rate billed on this route + vehicle class - "
                                  "the rate normally used to invoice"),
}

TRIPS_CFG = {   # one row per billed trip
    "ship_date": DateCol("Ship date", help="วันที่ขึ้นสินค้า (column C)", format="DD/MM/YYYY"),
    "status": TxtCol("Status", help=STATUS_HELP),
    "customer": TxtCol("Customer", help="ชื่อลูกค้า (column K)"),
    "vehicle_class": TxtCol("Vehicle class", help=VEHICLE_HELP),
    "vehicle_type": TxtCol("Vehicle (as billed)", help="ประเภทรถ exactly as in the report (column AG)"),
    "origin_c": TxtCol("Origin", help="First stop of the route, spelling variants merged"),
    "origin_latlon": TxtCol("Origin lat, lon (°)", help="Latitude, longitude in decimal degrees (WGS84)"),
    "dest_c": TxtCol("Destination", help="Last stop of the route, spelling variants merged"),
    "dest_latlon": TxtCol("Destination lat, lon (°)", help="Latitude, longitude in decimal degrees (WGS84)"),
    "stops_txt": TxtCol("Stops (in order)", help="Every stop parsed from เส้นทางขนส่ง (column AB)"),
    "stop_count": NumCol("Stops (count)", help="2 = plain A→B; more = multi-drop", format="%d"),
    "total_km": NumCol("Road km (km)", format="%.1f",
                       help="Google Routes driving distance, sum of every leg A→B→C…"),
    "fuel_rate_low": NumCol("Fuel rate low (THB/litre)", format="%.2f",
                            help="Diesel-price band the rate is pegged to, from อัตราน้ำมัน(...) in the route "
                                 "(column AB) - bottom of the band, e.g. 30.00 for '30-30.99'"),
    "fuel_rate_high": NumCol("Fuel rate high (THB/litre)", format="%.2f",
                             help="Diesel-price band the rate is pegged to, from อัตราน้ำมัน(...) in the route "
                                  "(column AB) - top of the band, e.g. 30.99 for '30-30.99' - the rate actually "
                                  "billed"),
    "price": thb("Price", "ราคาขนส่ง (column T), excluding VAT and other charges", decimals=2),
    "contractor_cost": thb("Contractor cost", "ค่าใช้จ่ายผู้รับเหมา (column AI)", decimals=2),
    "job_order_no": TxtCol("Job order no.", help="เลขที่ใบสั่งปฏิบัติงาน (column B)"),
    "bill_no": TxtCol("Bill no.", help="เลขที่บิล (column A)"),
    "source_file": TxtCol("Source file", help="Which Report AR_AP workbook the row came from"),
}

PAIR_PRICE_COL = thb("Price", "Median billed price for this pair across all its history, after the "
                              "MAD-outlier filter above (if on) - the single representative price "
                              "for this pair", decimals=0)

PAIR_CFG = {   # pair_rollup(): one row per origin -> destination + vehicle class
    "rank": NumCol("Rank", format="%d", help="Position by avg monthly CV, worst first"),
    "origin_c": TxtCol("Origin", help="First stop of the route, spelling variants merged"),
    "dest_c": TxtCol("Destination", help="Last stop of the route, spelling variants merged"),
    "vehicle_class": TxtCol("Vehicle class", help=VEHICLE_HELP),
    "months": NumCol("Months with data (count)", format="%d",
                     help="Calendar months that had at least the minimum trip count set above"),
    "trips": NumCol("Trips (count)", format="%,d", help="Total billed trips across those months"),
    "price": PAIR_PRICE_COL,
    "avg_cv_pct": NumCol("Avg monthly CV (%)", format="%.1f%%",
                         help="Coefficient of variation (S.D. ÷ mean price) within each month, "
                              "averaged across months - higher means this pair's price swings around "
                              "more from trip to trip in a typical month"),
    "max_cv_pct": NumCol("Worst monthly CV (%)", format="%.1f%%",
                         help="The single month with the highest S.D. ÷ mean price for this pair"),
}

PAIR_MONTH_CFG = {   # pair_rollup() filtered to one calendar month
    "origin_c": TxtCol("Origin", help="First stop of the route, spelling variants merged"),
    "dest_c": TxtCol("Destination", help="Last stop of the route, spelling variants merged"),
    "vehicle_class": TxtCol("Vehicle class", help=VEHICLE_HELP),
    "trips": NumCol("N - trips this month", format="%,d",
                    help="Billed trips this pair had in the selected month - the sample size behind "
                         "the CV next to it. A high CV on a low N is easy to get by chance"),
    "price": PAIR_PRICE_COL,
    "mean": thb("Mean price", "Average billed price this month"),
    "median": thb("Median price", "Middle billed price this month"),
    "sd": thb("S.D.", "Standard deviation of billed price this month"),
    "cv_pct": NumCol("CV (%)", format="%.1f%%",
                     help="S.D. ÷ mean, as a percentage - how spread out prices were this month "
                          "relative to their average"),
}

MONTHLY_CFG = {   # monthly stats for one selected pair
    "month": TxtCol("Month"),
    "trips": NumCol("Trips (count)", format="%d", help="Billed trips this month"),
    "mean": thb("Mean price", "Average billed price this month"),
    "median": thb("Median price", "Middle billed price this month"),
    "sd": thb("S.D.", "Standard deviation of billed price this month"),
    "cv_pct": NumCol("CV (%)", format="%.1f%%",
                     help="S.D. ÷ mean, as a percentage - how spread out prices were this month "
                          "relative to their average"),
}

DEV_CFG = {   # trips for one selected pair, with % deviation from monthly median
    "ship_date": DateCol("Ship date", help="วันที่ขึ้นสินค้า (column C)", format="DD/MM/YYYY"),
    "customer": TxtCol("Customer", help="ชื่อลูกค้า (column K)"),
    "price": thb("Price", "ราคาขนส่ง (column T), excluding VAT and other charges", decimals=2),
    "month": TxtCol("Month"),
    "monthly_median": thb("Monthly median", "Median price of this pair + vehicle class in this calendar month"),
    "pct_dev": NumCol("% dev. from monthly median", format="%.1f%%",
                      help="(Price − monthly median) ÷ monthly median, as a percentage. "
                           "Positive = billed above the typical price that month, negative = below"),
    "is_mad_outlier": st.column_config.CheckboxColumn(
        "MAD outlier?",
        help="Flagged as a price outlier for this pair + vehicle class: |price − group median| exceeds "
             "3× a floored MAD-based scale, pooled across every month shown here"),
    "job_order_no": TxtCol("Job order no.", help="เลขที่ใบสั่งปฏิบัติงาน (column B)"),
}

MODEL_CFG = {   # model_table(): one row per vehicle class
    "vehicle_class": TxtCol("Vehicle class", help=VEHICLE_HELP),
    "estimate": thb("Estimate", "Model price = base fare + THB/km × km + drop fee × extra drops"),
    "low": thb("Low", "Cheap end of normal: 1 in 10 past trips was billed this far below its model price, or more"),
    "high": thb("High", "Expensive end of normal: 1 in 10 past trips was billed this far above its model price, or more"),
    "trips_in_model": NumCol("Trips in model (count)", format="%,d",
                             help="Billed trips with road km used to fit this vehicle class"),
    "method": TxtCol("Based on", help="Own trips = line built from this truck class's own bills (30+ trips). "
                                      "Borrowed – few trips = under 30 trips, so the all-trucks line is "
                                      "scaled to this class's prices; less reliable"),
    "holdout_error_%": NumCol("Typical error (%)", format="%.0f%%",
                              help="Median % gap between model and actual price on 20% of trips kept "
                                   "aside and not used to fit - how far off a typical estimate is"),
    "note": TxtCol("Note", help="'outside data range' = no trips this short/long for this vehicle; "
                                "treat the estimate as a rough guide"),
}
