"""Is price linear in distance? price vs km, and ln(price) vs km, with fit lines + binned medians."""
import sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
sys.path.insert(0, ".")
from src import fit_model as fm

df = fm.load_training()
df = df[(df["total_km"] > 0) & (df["price"] > 0)]
cls = df["vehicle_class"].value_counts().index[0]
d = df[df["vehicle_class"] == cls]
print(cls, len(d))
x, y = d["total_km"].to_numpy(), d["price"].to_numpy()

fig, ax = plt.subplots(1, 2, figsize=(14, 5.5))
out = []
for a, (yy, lab) in zip(ax, [(y, "price (THB)"), (np.log(y), "ln(price)")]):
    b, a0 = np.polyfit(x, yy, 1)
    r2 = np.corrcoef(x, yy)[0, 1] ** 2
    a.scatter(x, yy, s=6, alpha=.25, color="#4c78a8")
    bins = pd.qcut(x, 20, duplicates="drop")
    m = pd.DataFrame({"x": x, "y": yy}).groupby(bins, observed=True).median()
    a.plot(m["x"], m["y"], "o-", color="#e45756", label="binned median")
    xs = np.linspace(x.min(), x.max(), 100)
    a.plot(xs, a0 + b * xs, "k--", label=f"linear fit  R²={r2:.3f}")
    a.set_xlim(0, 300)
    a.set_ylim(*np.percentile(yy[x <= 300], [0.5, 99.5]))
    a.set_xlabel("distance (km)"); a.set_ylabel(lab); a.legend(); a.grid(alpha=.3)
    out.append(r2)
ax[0].set_title(f"price vs distance  (R²={out[0]:.3f})")
ax[1].set_title(f"ln(price) vs distance  (R²={out[1]:.3f})")
fig.suptitle(f"{cls}  n={len(d)}")
fig.tight_layout(rect=(0, 0.05, 1, 1))
fig.text(0.5, 0.01,
         "Binned median: all trips sorted by distance and split into 20 groups with equal trip counts "
         f"(pd.qcut(x, 20), ~{len(d) // 20} trips each, n={len(d):,}); each red dot = median km, median value of one group.",
         ha="center", fontsize=9, color="#444")
fig.savefig("linear_check_zoom.png", dpi=130)
print(out)
