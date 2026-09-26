# Fitted price model (base fare + rate/km): Q&A review

Source: `src/fit_model.py`, `src/robust.py`, `ui/stats.py`, `data/model_summary.csv`.

## 1. Is it an estimate or a prediction, and why?

It is an **estimate**, not a prediction in the forecasting sense.

**Why it's an estimate**
- `src/fit_model.py` fits `price = base_fare + rate_per_km × km + drop_fee × extra_drops` by trimmed least squares (drop |residual| > 3 MAD, refit).
- Base fare and rate/km are the **intercept and slope** of that line: regression coefficients estimated from past billed trips (status CONFIRM or POSTED).
- They are not fees from a tariff or invoice. The dashboard says so: "a fitted value, not a fee on any invoice".
- The rate/km is the average marginal THB per km across historical bills. The base fare is where the line crosses km = 0, so it is partly an artifact of the fit (km must be > 0 in the data).
- They are point estimates of unknown underlying parameters, with uncertainty stored as R² and the p10/p90 of actual/predicted ratios.
- Classes with fewer than 30 trips are "Borrowed": the all-truck line scaled by that class's median price ratio, so their coefficients are even less direct.

**Where it acts as a prediction**
- Applying the line to a **new route's km** to quote a price is prediction (out-of-sample use).
- The code validates that use: it trains on 80% of trips, holds out 20% (`test_mdape_pct`), then refits on all rows for the final coefficients.
- It is only reliable within the trained range (`km_min` to `km_max`). Outside it, the line extrapolates.

**Short version:** base fare and rate/km are *estimated* parameters. The price computed from them for a new route is a *prediction* with an error band. "Estimate" and "fitted" in the UI are accurate. Avoid calling the coefficients "predictions" or "actual rates".

## 2. What model? Linear? Why?

**Yes, linear**: ordinary least squares on `[1, km, extra_drops]`, fitted separately per vehicle class (plus one all-truck `ALL` fit). It is robustified by trimming (3 MAD, up to 3 passes, `fit_model.py:50-75`), so it is not plain OLS.

**Why linear (what the code shows)**
- **Matches how freight is quoted:** a fixed part per trip plus a per-km part. The coefficients read directly as "base fare" and "THB/km".
- **The per-km rate is a marginal cost.** A flat price ÷ km swings wildly with distance because the base fare dominates short trips. The slope is stable.
- **Stable with little data:** 3 parameters per class (drop fee only if at least 10 multi-drop trips exist). That's why classes under 30 trips can borrow the scaled `ALL` line.
- **Checkable:** `fit_model.py:158-174` flags non-positive rates and tests whether rate/km rises with truck size. A model-free distance-band median table is a cross-check.
- **Outliers handled** by the trimming step.

**What can't be confirmed from the repo**
- No test of alternatives (log-log, piecewise, tiered rates, gradient boosting), so linear was chosen for interpretability and robustness, not because it beat other models.
- Real tariffs are often **tiered** (per-km rate drops on long hauls). A single line would miss that. Check band medians and per-class `r2` / `test_mdape_pct` for curvature.
- The model only uses km, drops and vehicle class. It ignores tolls, region, customer, etc.

## 3. Cleaned data, and which cutoff?

**The model reads the cleaned file.** `load_training()` reads `TRIPS_CLEAN` (`data/trips_clean.csv`, `fit_model.py:30`), never the raw or parsed files.

**Cutoff used: a 3× floored MAD on regression residuals.**

| Cutoff | Where | Used by the model? |
|---|---|---|
| 3× floored MAD on residuals: `|resid| > 3 × max(1.4826·MAD, 10% of median price, 1 THB)`, refit up to 3 passes | `src/robust.py`, called from `fit_model.py:50-62` | **Yes** |
| Same 3× floored MAD, on each trip's deviation from its route's median price (origin, dest, vehicle class) | `flag_price_outliers`, `ui/stats.py:111`, Variability tab | No, display only |
| 3-sigma trim, and modified-Z (|z| > 3.5) trim | `cutoff_price_stats`, `ui/stats.py:26`, dashboard averages and per-km tables | No, display only |

**Caveats**
1. `trips_clean.csv` is clean in the parsing sense (stops parsed, km geocoded), not the outlier sense. `distance.py:164-169` only prints the count of implausible km (> `MAX_KM`); it doesn't drop them.
2. The model filters `km > 0`, `price > 0`, status CONFIRM/POSTED, then trims by MAD during the fit. A row with a wildly wrong km can enter the first pass and is only removed if its residual is large enough.
3. Reported R² and the ratio band are computed on kept rows only (`n_used` of `n`), so they look better than on all trips.
4. The model's MAD trim (residuals around the fitted line) differs from the 3-sigma and modified-Z cutoffs (spread around the mean/median of raw prices). The same trip can be an outlier under one and not another.

**Suggestions:** make the dashboard cutoffs use the same `mad_outlier_mask` as the model, and drop rows over `MAX_KM` before fitting.

## 4. How good is the prediction vs actual data (residual plot)

Checked on the 20% holdout: 6,271 trips unseen by the fit (same seed and per-class fits as `fit_model.py`). Plot: `residuals.png` in the repo root.

**Headline accuracy**

| Metric | Value |
|---|---|
| Median absolute % error | **15.2%** |
| Mean absolute % error | 48.2% (dragged up by a few huge misses) |
| Within ±10% of actual | 37% |
| Within ±20% | 60% |
| More than 50% off | **15.5%** |
| MAE / RMSE | 1,125 / 2,012 THB |
| Median bias | about −32 THB (no systematic over/under-shoot) |

**Residual plot**
- The centre is well behaved: the % error histogram peaks near 0, roughly symmetric, spread about ±25%. Actual vs predicted follows the diagonal.
- Heavy left tail: the spike at −100% (clipped) is trips at least 2× cheaper than predicted, a cluster billed at a few hundred to about 2,000 THB where the model predicts 2–5k. Likely flat or special-rate jobs; the cause is not identified.
- Errors fan out with size (heteroscedastic), so % error is a better yardstick than fixed ± THB.

**By vehicle class (MdAPE)**
- Good: 10W dry 2.3%, 18W dry 2.4%, 18W reefer 2.7%, 4W dry 3.9%, 22W dry 4.7% (near-fixed tariffs).
- Weak: **10W reefer 21%** and 4W reefer 14%. These carry most volume (4,535 of 6,271 test trips); only 23% of 10W reefer trips land within ±10%.
- Unreliable: 6W dry, 11 test trips, 48% MdAPE (noise).

**By distance**
- Best at 100–200 km: 11.5%.
- Under 100 km: about 17–18%.
- Over 400 km: 30–33% on few points; a linear line probably misses tiered long-haul rates.

**Takeaway:** dry classes are near-deterministic. Reefer classes, which carry most volume, have much more unexplained variance, pointing to missing drivers (customer or contract, temperature, region). Treat reefer estimates as ±20–25% guides.

**Next step (optional):** investigate the low-priced cluster by customer or job type to see if it can be split out.


## Holdout error metrics: how they are calculated

Source: `lab/ml_lab.py` (`load`, `report`, `main`). Reported numbers: median abs % error 15.2%, mean abs % error 48.2%, within ±10% 37%, within ±20% 60%, more than 50% off 15.5%, MAE / RMSE 1,125 / 2,012 THB, median bias about −32 THB.

**Setup (`ml_lab.py:70-71`)**
- Trips are sorted by ship date. The cut-off is the 80th-percentile date.
- Trips on or before the cut-off are training rows. The latest 20% are the test set, so the model is judged on the future.
- The production-style linear fit is run per vehicle class. Classes with fewer than 30 training rows use the all-truck line.
- Each test trip gets a predicted price `pred`, floored at 1 THB.

**Per-trip error (`report()`, lines 59-65)**
- `pct = (actual − pred) / actual × 100` is the signed % error. Positive means under-predicted.
- `ape = |pct|` is the absolute % error.

Running example: a trip that actually cost 10,000 THB, predicted at 8,500 THB.

- **Median absolute % error (15.2%)**
  - Per trip: |actual − predicted| ÷ actual (example: 15%). Take the middle value across all trips.
  - Half the trips are off by less than 15.2%. It is the typical error and ignores extreme misses.
- **Mean absolute % error (48.2%)**
  - The same per-trip % errors, averaged.
  - A few huge misses pull it far above the median (a 300 THB trip predicted at 3,000 counts as 900%).
- **Within ±10% / ±20% (37% / 60%)**
  - Share of trips whose absolute % error is under 10% or 20%.
  - 37 in 100 predictions land within 10% of the real price, 60 in 100 within 20%.
- **More than 50% off (15.5%)**
  - Share of trips whose absolute % error is over 50%.
  - About 1 trip in 6.5 is badly wrong: how often the estimate is unusable.
- **MAE (1,125 THB)**
  - Mean absolute error: average of |actual − predicted| in THB (example: 1,500).
  - On average the estimate is 1,125 THB from the real price. Big trips count more than small ones.
- **RMSE (2,012 THB)**
  - Root mean squared error: square each THB error, average, take the square root.
  - Squaring punishes big misses harder. RMSE well above MAE confirms a few large errors.
- **Median bias (about −32 THB)**
  - Middle value of the signed error (actual − predicted), no absolute value.
  - Over- and under-predictions cancel, so no systematic lean. Small versus the 1,125 THB MAE.

- The gap between median (15.2%) and mean (48.2%) shows a heavy tail: most trips are predicted decently, a minority are far off.
- Use the median and the within-X% figures as the fair summary.
- Caveat: `ml_lab.py` computes `bias_med_%` in percent, not THB. RMSE is not in `report()` either, so the −32 THB and RMSE figures come from a different calculation.

## Base fare and rate/km: how they are calculated

Source: `src/fit_model.py`, `fit()` (lines 50-75) and `main()`.

**1. Training rows (`load_training`)**
- Only trips with status CONFIRM or POSTED, `total_km > 0` and `price > 0`.
- `extra_drops = max(stop_count − 2, 0)`, so a plain A→B trip has 0.

**2. The model**
- `price = base_fare + rate_per_km × km + drop_fee × extra_drops`.
- Design matrix `X = [1, km, extra_drops]`. The drop column is included only if the class has at least 10 trips with extra drops; otherwise `drop_fee = 0`.

**3. Solving (`np.linalg.lstsq`)**
- Least squares picks `[base, rate, drop]` to minimise the sum of squared (actual − predicted) prices.
- `base_fare` is the intercept (line at km = 0). `rate_per_km` is the slope: average extra THB per extra km.
- The base is an extrapolation to km = 0, so it is partly an artifact of the fit.

**4. Trimming outliers (up to 3 passes)**
1. Fit on the kept rows and compute residuals.
2. Estimate a robust scale with `mad_scale` from the kept rows only.
3. Drop rows where `|residual| > 3 × scale`, then refit.
- Stops early if nothing changes, or if trimming would leave fewer than half the rows.

**5. Two fits per class (`main`)**
- Fit on a random 80% and score the remaining 20% to get `test_mdape_pct`.
- Refit on all rows of the class. Those coefficients are the published base and rate. The test score stays honest because the final fit was not what was tested.

**6. Small classes**
- Under 30 training trips is labelled "Borrowed". It takes the all-truck line and scales base, rate and drop fee by the median of `actual price / all-truck prediction` over that class's trips.

**7. Reported quality**
- `r2 = 1 − SS_res / SS_tot` on the kept rows.
- `ratio_p10` and `ratio_p90` are the 10th and 90th percentiles of `actual / predicted`. They give the band shown around an estimate.
- `km_min` and `km_max` are the fitted range. Outside it, an estimate is extrapolated (marked `*`).

**8. Using it**
- Estimated price = `base + rate × km + drop × extra_drops`.
- In the fuel-tab round-trip table, `km` is the one-way distance (half the band midpoint).
