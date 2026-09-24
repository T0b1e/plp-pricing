 Per KM pricing — investigation summary

## What was reported

The "Per KM" table in the dashboard (app.py, "Per KM" section) showed suspicious
average THB/km values, e.g.:

| Car Type      | Avg. price/km (before cut-off) |
|---------------|--------------------------------|
| 6W reefer     | 364.23                         |
| 4W reefer     | 278.64                         |
| 10W dry NGV   | 100.41                         |

## How the number is computed

`app.py:849-860` — for each `vehicle_class`, take every CONFIRM trip row with
`price > 0` and `total_km > 0`, compute `price / total_km` per row, and average
(`stats_row`/`cutoff_price_stats`, `app.py:361-388`). Three variants are shown:
plain mean ("before cut-off"), 3-sigma trimmed mean, and modified-Z trimmed mean.

## Root causes found

### 1. n=1 "average" (10W dry NGV = 100.41)
Only one CONFIRM row exists for vehicle_class "10W dry NGV" (bill `25091606`,
2130 THB / 21.212 km). The "average" is just that single trip's ratio — not an
error, just misleadingly presented as an average. `N (before cut-off)` column
already shows n=1, so this is visible in the table as-is. No code change made.

### 2. Bad geocoding inflating short-distance trips (6W/4W reefer)
Several bills going to **"DC Makro Mahachai"** (and similarly vague names like
**"โซนสมุทรสาคร"**) get geocoded to the wrong point — a retail mall ~1 km from
origin, instead of the real distribution center several km further — per
`data/geocode_cache.json`, which lists multiple ambiguous candidates for that
name. Real billed prices (1500–5376 THB) divided by these bogus ~1 km distances
produce 800–5100 THB/km outliers that dominate the untrimmed mean because they
recur across many trips/bills (e.g. bill `2607000163`, a recurring contract,
repeats this ~30+ times).

**Bills identified so far:** `2607000163`, `2607000152` (both → "DC Makro
Mahachai"), `2605000215` (→ "โซนสมุทรสาคร").

**Fix applied:** `app.py:744-754` excludes `2607000163` from the `priced`
DataFrame used for both the route-price and per-km tables, via a documented
`BAD_GEOCODE_BILL_NOS` set — **no rows were deleted from `data/trips_clean.csv`**,
only excluded from these stats with an inline comment explaining why. This
dropped 6W reefer from 364.23→79.18 and 4W reefer from 278.64→79.75.
`2607000152` and `2605000215` were found afterward and are **not yet added**
to the exclusion set.

### 3. Structural: flat price/km averaging mixes short-haul and long-haul trips
Even after excluding the known bad-geocode bills, 4W reefer still spans
2.19–1580 THB/km (mean 79.75, n=119). Trucking pricing is generally
`base_fare + rate_per_km × distance`, so:
- short trips (1–15 km) have per-km ratios inflated by the fixed base fare
  (e.g. legit-looking 100–200 THB/km trips),
- long-haul trips (200–1764 km) dilute to very low ratios (2–20 THB/km),
  e.g. bill `2605000116` "ปั้ม PT มหาชัย → Havi", 1764 km / 3857 THB = 2.19 THB/km.

This isn't a data error — it's a mismatch between a flat per-km average and a
base+variable cost model. `data/model_summary.csv` / `src/fit_model.py` already
fit `base_fare + rate_per_km` separately per vehicle class/route and don't have
this problem; the dashboard's "Per KM" table does, since it doesn't account for
distance band. 3-sigma/modi-Z trimming only partially compensates.

## Not yet done
- Add `2607000152` and `2605000215` to `BAD_GEOCODE_BILL_NOS` (or fix the
  underlying geocode).
- Consider bucketing "Per KM" by distance band instead of one flat average,
  or pointing users to `model_summary.csv`'s base+variable fit for a more
  accurate per-km read at a given distance.

## Fix applied: corrected the "DC Makro Mahachai" geocode at the source

Root cause of the 2607000163/2607000152 outliers traced further: the *query
text* "DC Makro Mahachai" is inherently ambiguous. `src/geocode.py`'s
`pick()` fuzzy-matches the query against Places API candidates and picks
the highest `rapidfuzz.fuzz.partial_ratio` score — but Google returns 3
candidates for that query, all pure-Thai display names (no Latin "DC Makro
Mahachai" text to match against), so the match is effectively noise. Scored
manually:

| candidate                          | partial_ratio vs "DC Makro Mahachai" |
|-------------------------------------|---------------------------------------|
| แม็คโคร โลตัส มอลล์ มหาชัย (wrong, picked) | 13.3                                   |
| แม็คโคร สมุทรสาคร                    | 10.0                                   |
| ศูนย์กระจายสินค้าแมคโครมหาชัย (correct)  | 0.0                                    |

The wrong one wins by default (highest of three near-zero scores), and this
persists even for a Thai-augmented query ("แมคโครมหาชัย DC Makro Mahachai")
because of a spelling variant (แม็คโคร vs แมคโคร) - confirmed by rerunning
`fuzz.partial_ratio` on both queries, wrong candidate still scores highest
(63.2 vs 58.5 for the real DC). So simply rerunning `geocode.py` would not
have fixed it - same query text deterministically produces the same wrong
pick every time, and the query is already cached in `geocode_cache.json` so
a plain rerun would not even call the API again.

**The correct location was already present in the cache** as an alternate
(lower-scored) candidate for the same query, found in
`data/geocode_cache.json`:
- Name: ศูนย์กระจายสินค้าแมคโครมหาชัย (the real Makro Mahachai distribution center)
- Coordinates: `13.5003258, 100.1285835`
- Place ID: `ChIJo0kG6grP4jARQQOh1ErWZ9E`
- Address: `64/2 64/2, ตำบล กาหลง อำเภอเมืองสมุทรสาคร สมุทรสาคร 74000`

**Fix applied in `data/locations_master.csv`** (2026-09-23): found every
non-alias row whose `place_id` matched the wrong mall
(`ChIJ0VVavO7H4jARWL8LV6L1rr4`) and whose `token` starts with "DC Makro
Mahachai" - 9 rows total (`DC Makro Mahachai`, `... (แมคโครมหาชัย)`, `...
(2025)`, and 6 smaller-frequency variants like `... ลงสินค้า`, `...
(Pre - Cool)`, `... (Datd logger)`). For each, set:
- `lat`/`lon` -> `13.5003258` / `100.1285835`
- `place_id` -> `ChIJo0kG6grP4jARQQOh1ErWZ9E`
- `formatted_address` -> the real DC's address
- `geocode_source` -> `manual` (locks the row - `geocode.py`'s
  `LOCKED_SOURCES` means future `geocode.py` runs will never overwrite it)
- `verified` -> `y`
- `note` -> explains the wrong-candidate issue and points back here

No rows were deleted; row count in `locations_master.csv` unchanged (2035).
Applied via a small pandas script matching on `place_id` + token prefix, then
`df.to_csv(..., index=False, encoding="utf-8-sig")` - same read/write
convention as `src/files.py` uses throughout the pipeline.

**Still required to take effect:** `data/trips_clean.csv`'s `total_km` for
these legs is not automatically updated by this change. The pipeline order
is `locations_master.csv` (lat/lon) -> `src/distance.py` (Routes API,
cached by *coordinate pair* in `data/distance_cache.json`) ->
`trips_clean.csv`. Since the coordinates changed, there's no cache entry yet
for the new pair, so `distance.py` will make a **fresh, billed** Google
Routes API call for it. Running `python main.py --from distance` (using the
key in `.env`) is needed to actually regenerate `trips_clean.csv` with
correct `total_km` for these legs - not run yet, pending confirmation since
it costs real API credits and rewrites data/trips_clean.csv and
data/trip_table.xlsx downstream.
