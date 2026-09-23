# Transport pricing: route cleanup + km → price estimator

Turns the `Report AR_AP_*.xlsx` billing exports into a clean trip table with coordinates and
road km, then fits a price curve per vehicle class.

## Setup

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

`.env` must contain `GOOGLE_MAPS_API_KEY=...`, with **Places API (New)**, **Geocoding API** and
**Routes API** enabled on its Google Cloud project.

## Run

```powershell
.\venv\Scripts\python.exe main.py                      # full pipeline
.\venv\Scripts\python.exe main.py --from distance      # after fixing locations by hand
.\venv\Scripts\python.exe -m src.estimate --km 120 --vehicle "10W reefer"
.\venv\Scripts\python.exe -m src.estimate --km 250 --vehicle 22 --stops 4
.\venv\Scripts\python.exe -m src.estimate --list
.\venv\Scripts\python.exe -m pytest tests
```

## Web UI (for testing)

```powershell
.\venv\Scripts\python.exe -m streamlit run app.py --server.address 0.0.0.0
```

Opens at `http://localhost:8501`; others on the office network use `http://<this-PC-IP>:8501`
(allow Python through Windows Firewall when asked). Two tabs:

- **By origin → destination**: every billed trip on that route, with price per vehicle class
  (median, p10–p90, last price) and filters for vehicle and customer. For a route with no history
  it estimates from road km (distance cache, or one Routes API call on a button click).
- **By km**: model estimate per vehicle class plus real trips within ±10% of that km.

Parts that need the model show a notice until `main.py --from distance` has been run. After
rerunning the pipeline, press **Reload data** in the sidebar.

New monthly files dropped into the folder are picked up automatically. All Google results are
cached in `data/pricing.db` (the `distance_cache`/`geocode_cache` tables), so reruns only pay for
places and legs not seen before - each cache row is upserted individually, so a Streamlit user
clicking "look up road km" never races the batch pipeline (or another user) over a whole-file
rewrite the way the old `data/*_cache.json` files could.

## Pipeline

Every stage but `geocode` (which edits `locations_master.csv` directly) hands its output to the
next one through `data/pricing.db` (see `src/db.py`) rather than a CSV/parquet file - a table is
fully rebuilt on every run, same as the old files were. `locations_master.csv` is the one
exception: it stays a plain file because it is hand-edited in Excel to fix geocoding (see below).

| Step | Module | Output |
|---|---|---|
| load | `src/load.py` | `trips_raw` table |
| parse | `src/parse_route.py`: splits column AB `เส้นทางขนส่ง` into ordered stops | `trips_parsed` table, `data/locations_master.csv` |
| aliases | `src/aliases.py`: merges spellings of one place | `alias_of` column, `data/alias_suggestions.csv` |
| geocode | `src/geocode.py`: Places Text Search → Geocoding fallback | lat/lon in `locations_master.csv` |
| distance | `src/distance.py`: Routes API, legs summed for multi-stop | `trips_clean` table |
| model | `src/fit_model.py`: `price = base + rate × km + fee × extra_drops` per vehicle | `model_summary`, `rate_table` tables |
| checksum | `src/checksum.py`: row count + price/contractor totals per file, every stage vs the xlsx (read independently); training-set exclusions by reason | `data/checksum.csv`, `data/checksum_exclusions.csv` |

`export` (`src/export.py`) reads the `trips_clean` table and writes the human-facing deliverable,
`data/trip_table.csv`/`.xlsx`.

Upgrading an existing checkout that still has the old CSV/parquet/JSON files? Run
`python -m scripts.migrate_to_sqlite` once to import them into `data/pricing.db` without
re-running (and re-paying for) the geocode/distance API steps. The original files are left as-is.

## How the price model works

### The idea in one line

For each truck class, draw the best straight line through past bills of **price vs. road km**:

```
estimated price (THB) = base fare + rate per km × road km + drop fee × extra drops
```

- **Base fare** (THB): the fixed part of a trip, paid even for a very short run
  (loading, waiting, minimum charge).
- **Rate per km** (THB/km): how much the price goes up for each extra km of driving.
- **Drop fee** (THB): extra per stop beyond a plain A → B trip. `extra drops = stops − 2`, so
  A → B → C has 1 extra drop.
- **Road km**: Google Routes driving distance. For multi-stop trips it's the sum of every leg
  (A → B → C = A→B + B→C).

Example, 10W reefer: `2,022 + 33.16 × 120 km` ≈ **6,000 THB** for a 120 km A → B trip.

### Why one line per truck class

Truck size matters far more than distance: a 22-wheel truck costs several times a 4-wheel truck
over the same km. So instead of one line for everything, there's one line per **vehicle class**,
built from column AG `ประเภทรถ`:

| Part of the label | Meaning |
|---|---|
| `4W`, `6W`, `10W`, `18W`, `22W` | number of wheels (truck size) |
| `reefer` | refrigerated body (`เย็น`) |
| `dry` | not refrigerated |
| `head` | tractor head pulling a trailer / container (`หัวลาก`) |
| `NGV`, `trailer` | fuel type / towed trailer, where the bill says so |

`S3_DUMMY_HL_TN` (a placeholder code, not a truck) and blank vehicle types are left out.

### What the "Based on" column means

It says whose bills a truck class's price line was built from:

| Based on | When | In plain words |
|---|---|---|
| **Own trips** | the truck class has **30 or more** trips | The line is built from this truck class's own bills. Trustworthy, within the error shown. |
| **Borrowed – few trips** | **fewer than 30** trips (e.g. `10W dry NGV` has only 5) | Too few bills to build a line of its own. So it borrows the line built from *all* trucks, then raises or lowers it to match how this class's few real bills compare with it. Treat as a rough guide; its error figure is the all-trucks one. |

`ALL` is that all-trucks line: one line through every trip regardless of truck size. It's used
only for borrowing, and as a rough answer when no vehicle is chosen.

(In `data/model_summary.csv` this is the `method` column. Older runs wrote `linear` for Own
trips and `fallback_scaled_all` for Borrowed.)

### Is depreciation in here?

Not as a separate number. The reports only hold the **price billed to the customer** (ราคาขนส่ง)
and the **contractor cost** (ค่าใช้จ่ายผู้รับเหมา). There is no depreciation, fuel, driver or
maintenance breakdown. Depreciation of the trucks is buried inside those amounts:

- Roughly, the **base fare** covers costs paid per trip regardless of distance (truck
  depreciation and finance per day, driver, insurance, loading), and the **rate per km** covers
  costs that grow with distance (fuel, tyres, wear and tear).
- That split is an interpretation, not something the data measures. The model can't tell how
  much of the base fare is depreciation.

To price depreciation explicitly, it would have to come from the fleet/asset register (truck
cost, useful life, km or trips per year) and be added as its own input.

### How the line is fitted

1. **Training data**: every trip with a price > 0, road km > 0 and a known vehicle class
   (26,268 of 29,592 rows; see *Data checksum* for where the rest go).
2. **Least squares**: the standard "line of best fit", which picks the base fare, rate per km
   and drop fee that make the squared gaps between line and real prices as small as possible.
3. **Trimming odd bills**: after a first fit, trips far from the line (more than about 3× the
   typical gap; the cutoff is never smaller than 30% of the class's median price, so flat-rate
   classes aren't over-trimmed) are set aside and the line is
   refitted, up to 3 times. This stops a few one-off bills (special charges, wrong locations,
   cancellations) from bending the line. `n_used` in `data/model_summary.csv` is how many trips
   remained. The trim never drops below half of a class's trips.
4. The drop-fee term is only fitted when a class has at least 10 multi-stop trips. Otherwise it's 0.

### How good is it: the numbers shown

- **Typical error (%)**, `test_mdape_pct`: before the final fit, 20% of trips are held back at
  random. The line is fitted on the other 80%, then asked to price the held-back trips it never
  saw. This is the **median % gap** between its estimates and the real bills: half the estimates
  were closer than this, half further off. Lower is better, and it's the most honest measure here.
- **Low – High range**: across past trips, the ratio *real price ÷ model price* was worked out.
  Low = estimate × the ratio only 10% of trips fell below. High = estimate × the ratio only 10%
  of trips were above. So about 8 in 10 real bills fell inside the range.
- **r²** (in `model_summary.csv`): the share of price variation the line explains, from 0 to 1.
  It's measured on the trimmed trips, so it flatters the model a little. Rely on typical error
  instead.

### Current results (run of 2026-09-22)

| Vehicle class | Based on | Trips | Base fare (THB) | Rate (THB/km) | Typical error |
|---|---|---:|---:|---:|---:|
| 10W reefer | Own trips | 11,963 | 2,022 | 33.2 | 22% |
| 4W reefer | Own trips | 7,405 | 2,008 | 7.3 | 13% |
| 6W reefer | Own trips | 2,020 | 3,900 | 16.8 | 14% |
| 22W head | Own trips | 1,879 | 6,783 | 24.8 | 10% |
| 10W dry | Own trips | 897 | 1,220 | 45.6 | 3% |
| 18W dry | Own trips | 678 | 7,105 | 30.6 | 6% |
| 22W dry | Own trips | 529 | 7,518 | 31.6 | 6% |
| 18W reefer | Own trips | 375 | 7,555 | 28.3 | 5% |
| 4W dry | Own trips | 320 | 1,894 | 9.6 | 9% |
| 22W reefer | Own trips | 120 | 7,463 | 33.0 | 2% |
| 6W dry | Own trips | 46 | 2,973 | 19.1 | 31% |
| 18W head | Borrowed – few trips | 20 | 3,349 | 42.5 | (34%, all trucks) |
| 6W dry trailer | Borrowed – few trips | 11 | 2,856 | 36.2 | (34%, all trucks) |
| 10W dry NGV | Borrowed – few trips | 5 | 1,991 | 25.2 | (34%, all trucks) |

### How to read the results, and the limits

- **Dry trucks follow km closely** (2–9% error): their contracts are basically priced per km.
- **Reefer trucks less so** (13–22%): prices are fixed **per route and per customer contract**.
  The same route bills the same price almost every time, but two routes of equal km can differ
  a lot. For a route that has been billed before, the **historical price** (Web UI tab 1) beats
  the model.
- Checked against real routes, estimates came out **10–20% below** actual bills
  (e.g. Jpac → Mars Chonburi, 50.6 km, 10W reefer: model 3,700 vs. billed 4,635). Treat an
  estimate as a starting point, not a quote.
- **Drop fees are not reliable yet.** Some classes show a *negative* drop fee. Multi-stop runs
  mix different kinds of work (distribution runs, round trips), so the fee picks up those
  differences rather than a true per-stop charge. Use `--stops` with care.
- **Outside the data range**: if the km asked for is shorter or longer than any trip of that
  class, the estimate is extended beyond what the data covers and flagged "outside data range".
- The **fuel-rate bracket** (`อัตราน้ำมัน(...)`) is not in the model yet. It exists on only
  ~44% of trips, but it's the obvious next factor to add.
- Accuracy depends on **locations being right**. One wrongly placed site (e.g. `CW2` landing in
  Nakhon Sawan) once pushed the 10W reefer rate down from 33 to 4 THB/km. After fixing locations
  in `data/locations_master.csv`, rerun `python main.py --from aliases`.

### Model-free cross-check: `data/rate_table.csv`

Alongside the line, the real bills are grouped per vehicle class into distance bands
(0–25, 25–50, 50–100, 100–200, 200–400, 400–800, 800–1500 km), each with trip count, median price,
P10–P90 range and median THB/km. No model, just what was billed. The CLI's "cross-check" line
and the Web UI's "Real trips between … km" tables show the same idea. If the model and the real
band disagree a lot, trust the real band.

## Data checksum

Every pipeline stage is reconciled against the source workbooks, which `src/checksum.py` re-reads
directly with openpyxl rather than through the loader. Totals are exact decimal sums, so a
difference of even 0.01 THB is flagged. Re-run with `python -m src.checksum`; the full result is
in `data/checksum.csv`.

Last run: 2026-09-22 (9 files, Jan–Sep 2026). **All stages match the source.**

| Stage | Rows | Distinct job orders | Σ ราคาขนส่ง (T) | Σ ค่าใช้จ่ายผู้รับเหมา (AI) | Match |
|---|---:|---:|---:|---:|---|
| xlsx (source) | 29,592 | 29,592 | 134,268,613.87 | 55,715,405.29 | — |
| trips_raw | 29,592 | 29,592 | 134,268,613.87 | 55,715,405.29 | OK |
| trips_parsed | 29,592 | 29,592 | 134,268,613.87 | 55,715,405.29 | OK |
| trips_clean | 29,592 | 29,592 | 134,268,613.87 | 55,715,405.29 | OK |
| trip_table (supervisor xlsx/csv) | 29,592 | 29,592 | 134,268,613.87 | (not carried) | OK |

Per source file:

| File | Period | Rows | Σ ราคาขนส่ง (T) |
|---|---|---:|---:|
| Report AR_AP_01.xlsx | Jan 2026 | 2,927 | 13,138,304.77 |
| Report AR_AP_02.xlsx | Feb 2026 | 2,859 | 11,585,603.73 |
| Report AR_AP_03.xlsx | Mar 2026 | 3,230 | 13,355,694.46 |
| Report AR_AP_04.xlsx | Apr 2026 | 3,275 | 15,523,283.04 |
| Report AR_AP_05.xlsx | May 2026 | 3,593 | 17,682,713.23 |
| Report AR_AP_06.xlsx | Jun 2026 | 3,514 | 16,850,207.25 |
| Report AR_AP_07.xlsx | Jul 2026 | 3,552 | 16,104,174.60 |
| Report AR_AP_08.xlsx | Aug 2026 | 3,844 | 16,432,778.74 |
| Report AR_AP_09.xlsx | 1–27 Sep 2026 | 2,798 | 13,595,854.05 |
| **Total** | | **29,592** | **134,268,613.87** |

Which rows reach the price model (`data/checksum_exclusions.csv`), adding back up to the source:

| Reason | Rows | % | Σ ราคาขนส่ง |
|---|---:|---:|---:|
| used in model | 26,268 | 88.8 | 128,088,855.71 |
| no route (column AB blank) | 1,645 | 5.6 | 3,578,257.32 |
| single stop (fee / distribution line, no destination) | 1,553 | 5.2 | 2,333,118.84 |
| price zero or blank | 81 | 0.3 | 0.00 |
| km missing (ungeocoded stop) | 45 | 0.2 | 268,382.00 |
| **Total** | **29,592** | 100 | **134,268,613.87** |

## Fixing a location by hand

In `data/locations_master.csv` (open in Excel, keep UTF-8):

- **Wrong match**: either edit `search_query` and rerun from `geocode`, or type the correct
  `lat`/`lon` and set `geocode_source` to `manual`, which is never overwritten.
- **Same place, two spellings**: add a row `token,alias_of` to `data/alias_overrides.csv`.
- Then run `main.py --from aliases`.
