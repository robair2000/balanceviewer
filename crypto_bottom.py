"""Crypto bottoming screen: deep, near the floor, turning, volatility falling.

Signals (all measured on daily KuCoin candles):
  1 deep        >= 70% below the all-time high
  2 near floor  within 25% of the 52-week low
  3 turning     30-day return positive
  4 basing      30-day volatility below its own 1-year volatility
Totals 0-4; 4/4 = BOTTOM.
"""
from __future__ import annotations

import json
import math
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

HDR = {"User-Agent": "Mozilla/5.0 research"}
OUT = Path(__file__).resolve().parent / "crypto_watch.json"
CG = ("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd"
      "&order=market_cap_desc&per_page=200&page=1&sparkline=false")
KU = "https://api.kucoin.com/api/v1/market/candles"

STABLE = {"usdt", "usdc", "dai", "usde", "usds", "fdusd", "tusd", "busd",
          "frax", "lusd", "pyusd", "usdd", "usd1", "rlusd", "susds", "susde",
          "gho", "crvusd", "usdf", "usdg", "usdt0", "usd0", "usdy", "usdx",
          "xusd", "usd+"}
WRAPPED = {"wbtc", "weth", "steth", "wsteth", "cbeth", "reth", "weeth",
           "wbeth", "cbbtc", "lseth", "meth", "ezeth", "rseth"}


def candles(sym: str) -> list[list[float]] | None:
    now = int(time.time())
    try:
        r = requests.get(KU, params={"type": "1day", "symbol": f"{sym}-USDT",
                                     "startAt": now - 365 * 86400,
                                     "endAt": now}, headers=HDR, timeout=20)
        data = r.json().get("data") or []
    except Exception:                                      # noqa: BLE001
        return None
    if len(data) < 120:
        return None
    # oldest first; [time, open, close, high, low, volume, turnover]
    return [list(map(float, c)) for c in reversed(data)]


def vol(rets: list[float]) -> float:
    if len(rets) < 5:
        return float("nan")
    return statistics.pstdev(rets) * math.sqrt(365)


def main() -> int:
    mk = requests.get(CG, headers=HDR, timeout=30).json()
    rows = []
    for c in mk:
        sym = (c.get("symbol") or "").lower()
        if sym in STABLE or sym in WRAPPED:
            continue
        if (c.get("market_cap") or 0) < 5e8:
            continue
        k = candles(sym.upper())
        time.sleep(0.12)
        if not k:
            continue

        closes = [x[2] for x in k]
        lows = [x[4] for x in k]
        price = closes[-1]
        lo52 = min(lows)
        rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
        r30 = price / closes[-31] - 1 if len(closes) > 31 else float("nan")
        v30 = vol(rets[-30:])
        v1y = vol(rets)

        dd_ath = c.get("ath_change_percentage") or 0.0
        from_low = price / lo52 - 1

        checks = [dd_ath <= -70.0, from_low <= 0.25, r30 > 0,
                  v30 == v30 and v1y == v1y and v30 < 0.85 * v1y]
        score = sum(checks)

        rows.append({"sym": sym.upper(), "name": c.get("name"),
                     "mcap_bn": (c.get("market_cap") or 0) / 1e9,
                     "vol_ratio": ((c.get("total_volume") or 0)
                                   / (c.get("market_cap") or 1)) * 100,
                     "price": price, "lo52": lo52,
                     "from_low": from_low * 100, "r30": r30 * 100,
                     "dd_ath": dd_ath, "v30": v30 * 100, "v1y": v1y * 100,
                     "score": score, "checks": checks})

    rows.sort(key=lambda r: (r["score"], r["mcap_bn"]), reverse=True)
    OUT.write_text(json.dumps({"generated": str(datetime.now(timezone.utc).date()),
                               "coins": rows}, indent=1), encoding="utf-8")

    print(f"{'coin':8s} {'mcap$bn':>8s} {'score':>5s} {'vs low':>7s} "
          f"{'30d':>7s} {'vs ATH':>7s} {'vol30/1y':>9s}")
    for r in rows[:20]:
        print(f"{r['sym']:8s} {r['mcap_bn']:8.1f} {r['score']:5d} "
              f"{r['from_low']:+7.0f}% {r['r30']:+7.0f}% {r['dd_ath']:+7.0f}% "
              f"{r['v30']:4.0f}/{r['v1y']:4.0f}")
    print(f"\n{len(rows)} coins scanned, "
          f"{sum(1 for r in rows if r['score'] == 4)} at 4/4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
