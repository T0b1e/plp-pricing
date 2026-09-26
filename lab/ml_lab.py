"""ML lab (standalone - not wired into app.py or src/).

Compares, on one time-based holdout (train = earlier 80% of ship dates, test = latest 20%):
  linear  : current per-class base fare + rate/km fit (src.fit_model.fit)
  lgbm    : LightGBM on log(price), Huber loss
  hybrid  : linear line, then LightGBM learns log(actual / linear)
  rf      : random forest sanity check
Run:  python lab/ml_lab.py   (needs lightgbm, scikit-learn)
"""
import sys
import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.ensemble import RandomForestRegressor

sys.path.insert(0, ".")
from src import fit_model as fm
from src.paths import EPPO_DIESEL

CATS = ["vehicle_class", "customer", "job_type", "origin", "destination", "route"]
NUMS = ["total_km", "direct_km", "extra_drops", "stop_count", "fuel_rate_low", "fuel_rate_high", "diesel", "dow"]


def load():
    df = fm.load_training()                       # same rows the production fit uses
    df["ship_date"] = pd.to_datetime(df["ship_date"])
    df = df[df["ship_date"].notna()].sort_values("ship_date").reset_index(drop=True)
    eppo = pd.read_json(EPPO_DIESEL, orient="index")[["date", "price"]]
    eppo["date"] = pd.to_datetime(eppo["date"]); eppo = eppo.sort_values("date").rename(columns={"price": "diesel"})
    df = pd.merge_asof(df, eppo, left_on="ship_date", right_on="date", direction="nearest")
    df["dow"] = df["ship_date"].dt.dayofweek
    df["route"] = df["origin"].astype(str) + ">" + df["destination"].astype(str)
    for c in CATS:
        df[c] = df[c].fillna("?").astype("category")
    return df


def linear_predict(train, test):
    """Production-style fit per class; classes with < MIN_N train rows use the ALL line."""
    fits = {v: fm.fit(g) for v, g in train.groupby("vehicle_class", observed=True) if len(g) >= fm.MIN_N}
    allfit = fm.fit(train)
    out = np.empty(len(test))
    for i, (v, km, dr) in enumerate(zip(test["vehicle_class"], test["total_km"], test["extra_drops"])):
        out[i] = fm.predict(fits.get(v, allfit), km, dr)
    return np.maximum(out, 1.0)


def train_lgb(X, y, objective="huber", alpha=None, seed=0):
    p = dict(objective=objective, n_estimators=600, learning_rate=0.04, num_leaves=31, min_child_samples=20,
             subsample=0.8, subsample_freq=1, colsample_bytree=0.8, cat_smooth=20, min_data_per_group=20,
             verbose=-1, random_state=seed)
    if objective == "huber":
        p["alpha"] = 0.3            # huber delta on log-price scale (~30% error)
    if objective == "quantile":
        p["alpha"] = alpha
    return lgb.LGBMRegressor(**p).fit(X, y)


def report(name, pred, test, rows):
    pct = (test["price"].to_numpy() - pred) / test["price"].to_numpy() * 100
    ape = np.abs(pct)
    rows.append({"model": name, "MdAPE_%": np.median(ape), "MAPE_%": ape.mean(), "within10_%": (ape < 10).mean() * 100,
                 "within20_%": (ape < 20).mean() * 100, "over50off_%": (ape > 50).mean() * 100,
                 "bias_med_%": np.median(pct), "MAE_THB": np.abs(test["price"].to_numpy() - pred).mean()})
    return ape


def main():
    df = load()
    cut = df["ship_date"].quantile(0.8)
    train, test = df[df["ship_date"] <= cut].copy(), df[df["ship_date"] > cut].copy()
    print(f"rows {len(df):,}  train {len(train):,} (<= {cut.date()})  test {len(test):,}")
    seen = set(train["customer"]); print(f"test trips whose customer is new: {(~test['customer'].isin(seen)).mean()*100:.1f}%")

    feats = CATS + NUMS
    rows, apes = [], {}
    lin_tr, lin_te = linear_predict(train, train), linear_predict(train, test)
    apes["linear"] = report("linear (current)", lin_te, test, rows)

    m = train_lgb(train[feats], np.log(train["price"]))
    p_lgb = np.exp(m.predict(test[feats])); apes["lgbm"] = report("LightGBM", p_lgb, test, rows)

    tr_h, te_h = train.assign(lin=np.log(lin_tr)), test.assign(lin=np.log(lin_te))
    mh = train_lgb(tr_h[feats + ["lin"]], np.log(train["price"]) - np.log(lin_tr))
    p_h = lin_te * np.exp(mh.predict(te_h[feats + ["lin"]])); apes["hybrid"] = report("hybrid (linear + LGBM residual)", p_h, test, rows)

    Xr = lambda d: d[feats].assign(**{c: d[c].cat.codes for c in CATS})
    rf = RandomForestRegressor(300, min_samples_leaf=5, n_jobs=-1, random_state=0).fit(Xr(train), np.log(train["price"]))
    p_rf = np.exp(rf.predict(Xr(test))); apes["rf"] = report("random forest", p_rf, test, rows)

    res = pd.DataFrame(rows).set_index("model").round(1)
    print("\n== overall (time-based holdout) ==\n" + res.to_string())

    print("\n== MdAPE % by vehicle class (n = test trips) ==")
    t = test.assign(linear=apes["linear"], lgbm=apes["lgbm"], hybrid=apes["hybrid"], rf=apes["rf"])
    by = t.groupby("vehicle_class", observed=True).agg(n=("price", "size"), linear=("linear", "median"), lgbm=("lgbm", "median"),
                                                       hybrid=("hybrid", "median"), rf=("rf", "median"))
    print(by[by.n >= 20].sort_values("n", ascending=False).round(1).to_string())

    print("\n== MdAPE % by km band ==")
    t["kmb"] = pd.cut(t["total_km"], [0, 50, 100, 200, 400, 3000])
    print(t.groupby("kmb", observed=True).agg(n=("price", "size"), linear=("linear", "median"), lgbm=("lgbm", "median"),
                                              hybrid=("hybrid", "median")).round(1).to_string())


    seen_route = set(train["route"])
    t["route_seen"] = t["route"].isin(seen_route).to_numpy()
    print("\n== MdAPE % by whether the route appeared in training ==")
    print(t.groupby("route_seen").agg(n=("price", "size"), linear=("linear", "median"), lgbm=("lgbm", "median"),
                                      hybrid=("hybrid", "median"), rf=("rf", "median"),
                                      lgbm_over50=("lgbm", lambda x: (x > 50).mean() * 100)).round(1).to_string())

    lo, hi = train_lgb(train[feats], np.log(train["price"]), "quantile", 0.1), train_lgb(train[feats], np.log(train["price"]), "quantile", 0.9)
    plo, phi = np.exp(lo.predict(test[feats])), np.exp(hi.predict(test[feats]))
    cov = ((test["price"] >= plo) & (test["price"] <= phi)).mean() * 100
    print(f"\nLGBM q10-q90 band: covers {cov:.1f}% of test trips (target 80%), median width {np.median((phi-plo)/p_lgb)*100:.0f}% of prediction")
    fi = pd.Series(m.booster_.feature_importance("gain"), index=feats); fi = (fi / fi.sum() * 100).sort_values(ascending=False)
    print("\nLGBM feature importance (% gain):\n" + fi.round(1).to_string())
    t.assign(p_linear=lin_te, p_lgbm=p_lgb, p_hybrid=p_h)[["ship_date", "vehicle_class", "customer", "total_km", "price",
                                                            "p_linear", "p_lgbm", "p_hybrid"]].to_csv("lab/ml_lab_test_predictions.csv", index=False)


if __name__ == "__main__":
    main()
