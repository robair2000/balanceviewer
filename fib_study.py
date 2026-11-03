"""Where do real pullbacks end? (absolute depth + fib ratio, 5y of daily bars)

Event: a new 100-day high after a 20%+ advance. Follow price until it either
reclaims the high or loses 45%. Only dips of 5%+ count as pullbacks.

Writes nothing; the five numbers it prints are copied into FIB_ROWS at the top
of wallpaper.py (the panel under CATALYSTS). Re-run it yearly, not weekly.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import yfinance as yf

UNIVERSE = r"C:\Users\robai\Documents\Standaardproject\balanceviewer\universe.txt"
N = 600

with open(UNIVERSE, encoding="utf-8") as fh:
    tickers = [l.strip() for l in fh
               if l.strip() and not l.lstrip().startswith("#")][:N]

data = yf.download(tickers, period="5y", interval="1d", auto_adjust=True,
                   progress=False, threads=True, group_by="column")["Close"]
data = data.dropna(axis=1, thresh=int(len(data) * 0.8))
print(f"{data.shape[1]} tickers, {len(data)} bars")

rows = []
for sym in data.columns:
    px = data[sym].to_numpy(dtype=float)
    n = len(px)
    for t in range(100, n - 5):
        high = px[t]
        if high < px[max(0, t - 100):t].max():
            continue
        low = px[max(0, t - 100):t].min()
        leg = (high - low) / high
        if leg < 0.20:
            continue
        depth, done = 0.0, False
        for j in range(t + 1, min(t + 250, n)):
            depth = max(depth, (high - px[j]) / high)
            if px[j] > high:
                done = True
                break
            if depth > 0.45:
                break
        if depth >= 0.05:
            rows.append((depth, depth / leg, done))

df = pd.DataFrame(rows, columns=["depth", "retr", "recovered"])
df = df[df["retr"] <= 1.5]
print(f"{len(df)} pullbacks of 5%+\n")

print("how deep the dip went (absolute):")
for lo, hi in ((0.05, 0.10), (0.10, 0.15), (0.15, 0.20), (0.20, 0.30),
               (0.30, 1.0)):
    sub = df[(df["depth"] >= lo) & (df["depth"] < hi)]
    print(f"  {lo:4.0%}-{hi:4.0%}  {len(sub) / len(df):4.0%}   "
          f"median fib {sub['retr'].median():.2f}   "
          f"recovered {sub['recovered'].mean():3.0%}")

print(f"\nmedian depth {df['depth'].median():.1%} | "
      f"25th {df['depth'].quantile(.25):.1%} | "
      f"75th {df['depth'].quantile(.75):.1%}")
print(f"median fib ratio {df['retr'].median():.2f}")
for lvl in (0.236, 0.382, 0.5, 0.618, 0.786):
    print(f"  retraced >= {lvl:.3f}: {(df['retr'] >= lvl).mean():4.0%}")

print("\nof dips that reach -10%, chance of reaching:")
base = df[df["depth"] >= 0.10]
for lvl in (0.15, 0.20, 0.25, 0.30):
    print(f"  -{lvl:.0%}: {(base['depth'] >= lvl).mean():4.0%}")
