"""Daily zone scan across the whole universe.

Prices only: one batched download for every name in universe.txt, then the
basing rule applied to each. No .info / .financials calls, which is what makes
the full fundamental scan too heavy to run daily.

    python zone_scan.py            # all names
    python zone_scan.py --limit 200

Writes zone_watch.json:
  {"generated": date, "count": n, "zone": [...], "new": [...]}
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
UNIVERSE = ROOT / "universe.txt"
OUT = ROOT / "zone_watch.json"


def rsi_series(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def load_universe() -> list[str]:
    with open(UNIVERSE, encoding="utf-8") as fh:
        return [l.strip() for l in fh
                if l.strip() and not l.lstrip().startswith("#")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    import yfinance as yf

    tickers = load_universe()
    if args.limit:
        tickers = tickers[:args.limit]
    started = datetime.now()
    print(f"downloading {len(tickers)} names...", flush=True)

    data = yf.download(tickers, period="1y", interval="1d", auto_adjust=True,
                       progress=False, threads=True, group_by="column")["Close"]
    if isinstance(data, pd.Series):
        data = data.to_frame(tickers[0])
    data = data.dropna(axis=1, thresh=int(len(data) * 0.6))
    print(f"got {data.shape[1]} series, {len(data)} bars, "
          f"{(datetime.now() - started).seconds}s", flush=True)

    rows = []
    for sym in data.columns:
        s = data[sym].dropna()
        if len(s) < 120:
            continue
        hi, lo = float(s.max()), float(s.min())
        close = float(s.iloc[-1])
        dd = close / hi - 1
        from_low = close / lo - 1
        r = s.pct_change().dropna()
        rsi = float(rsi_series(s).iloc[-1])
        ma20 = float(s.tail(20).mean())
        ma50 = float(s.tail(50).mean())
        v30 = float(r.tail(30).std())
        v1y = float(r.std())
        zone = bool(dd <= -0.30 and from_low <= 0.20 and 28 <= rsi <= 58
                    and v1y == v1y and v30 < 0.85 * v1y
                    and abs(close / ma20 - 1) <= 0.06 and close < ma50)
        if zone:
            rows.append({"ticker": sym, "price": round(close, 3),
                         "dd": round(dd * 100, 1),
                         "from_low": round(from_low * 100, 1),
                         "rsi": round(rsi, 1),
                         "above_ma50": round((close / ma50 - 1) * 100, 1)})

    rows.sort(key=lambda r: r["from_low"])
    prev: set[str] = set()
    try:
        prev = set(json.loads(OUT.read_text(encoding="utf-8")).get("zone") or [])
    except Exception:                                     # noqa: BLE001
        prev = set()
    for r in rows:
        r["new"] = r["ticker"] not in prev

    OUT.write_text(json.dumps({"generated": str(date.today()),
                               "count": len(rows),
                               "zone": rows}, indent=1), encoding="utf-8")

    new = [r["ticker"] for r in rows if r["new"]]
    print(f"{len(rows)} in zone ({len(new)} new)")
    for r in rows[:15]:
        print(f"  {'NEW ' if r['new'] else '    '}{r['ticker']:10s} "
              f"{r['price']:9.2f}  dd {r['dd']:+5.1f}%  "
              f"low {r['from_low']:+4.1f}%  rsi {r['rsi']:4.1f}")
    if len(rows) > 15:
        print(f"  ... {len(rows) - 15} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
