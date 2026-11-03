"""Crypto screen v1: supply, liquidity, survival — and a test of the fee thesis.

Universe: CoinGecko top 250 by market cap (stablecoins and wrapped assets out).
Filters : market cap, volume/market-cap, dilution headroom, age proxy.
Trigger : drawdown from all-time high.
Also    : correlates DefiLlama fee yield with 12-month return, to see whether
          fees carry any information at all.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import requests

HDR = {"User-Agent": "Mozilla/5.0 research", "Accept": "application/json"}
OUT = Path(__file__).resolve().parent / "crypto_watch.json"

CG = ("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd"
      "&order=market_cap_desc&per_page=250&page=1&sparkline=false"
      "&price_change_percentage=1y")
LLAMA = ("https://api.llama.fi/overview/fees"
         "?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true")

STABLE = {"usdt", "usdc", "dai", "usde", "usds", "fdusd", "tusd", "busd",
          "usdt.e", "usdc.e", "frax", "lusd", "pyusd", "usdd", "usd1",
          "rlusd", "susds", "susde", "gho", "crvusd", "usdf", "usdg",
          "usdt0", "usd0", "usdy", "usdx", "usdq", "usdr", "xusd",
          "usd+"}
WRAPPED = {"wbtc", "weth", "steth", "wsteth", "cbeth", "reth", "weeth",
           "wbeth", "cbbtc", "lseth", "meth", "ezeth", "rseth", "wsteth"}


def main() -> int:
    mk = requests.get(CG, headers=HDR, timeout=30)
    mk.raise_for_status()
    coins = mk.json()

    rows = []
    for c in coins:
        sym = (c.get("symbol") or "").lower()
        if sym in STABLE or sym in WRAPPED:
            continue
        mcap = c.get("market_cap") or 0
        vol = c.get("total_volume") or 0
        circ = c.get("circulating_supply") or 0
        mx = c.get("max_supply") or 0
        if mcap < 1e9 or not circ:
            continue

        dilution = ((mx - circ) / circ * 100) if mx else None
        ath_dd = c.get("ath_change_percentage") or 0
        age_days = None
        atl = c.get("atl_date")
        if atl:
            try:
                d = datetime.fromisoformat(atl.replace("Z", "+00:00"))
                age_days = (datetime.now(timezone.utc) - d).days
            except ValueError:
                pass

        rows.append({
            "sym": sym.upper(), "name": c.get("name"),
            "mcap_bn": mcap / 1e9,
            "vol_ratio": vol / mcap * 100 if mcap else 0,
            "dilution": dilution,
            "ath_dd": ath_dd,
            "age_days": age_days,
            "r1y": c.get("price_change_percentage_1y_in_currency"),
        })

    # fees from DefiLlama, matched by symbol or name
    try:
        feed = requests.get(LLAMA, headers=HDR, timeout=60).json()
        protos = feed.get("protocols", feed if isinstance(feed, list) else [])
    except Exception:                                      # noqa: BLE001
        protos = []
    fee = {}
    for p in protos:
        f = p.get("total24h") or p.get("fees24h")
        if not f:
            continue
        key = (p.get("name") or "").lower()
        fee[key] = f
    for r in rows:
        for k, v in fee.items():
            if r["sym"].lower() in k or (r["name"] or "").lower() in k:
                r["fee_yield"] = v * 365 / (r["mcap_bn"] * 1e9) * 100
                break

    rows.sort(key=lambda r: r["mcap_bn"], reverse=True)
    print(f"{'sym':7s} {'name':22s} {'mcap$bn':>8s} {'vol%':>6s} "
          f"{'dilut':>7s} {'ATH dd':>7s} {'r1y':>7s} {'fee y':>7s}")
    for r in rows[:30]:
        dil = f"{r['dilution']:.0f}%" if r["dilution"] is not None else "-"
        fee_y = f"{r['fee_yield']:.1f}%" if r.get("fee_yield") else "-"
        r1 = f"{r['r1y']:+.0f}%" if isinstance(r["r1y"], (int, float)) else "-"
        print(f"{r['sym']:7s} {(r['name'] or '')[:22]:22s} {r['mcap_bn']:8.1f} "
              f"{r['vol_ratio']:6.1f} {dil:>7s} {r['ath_dd']:6.0f}% {r1:>7s} "
              f"{fee_y:>7s}")

    with_fee = [r for r in rows if r.get("fee_yield")
                and isinstance(r.get("r1y"), (int, float))]
    if len(with_fee) > 4:
        import statistics
        hs = [r["r1y"] for r in with_fee if r["fee_yield"] >=
              statistics.median(x["fee_yield"] for x in with_fee)]
        ls = [r["r1y"] for r in with_fee if r["fee_yield"] <
              statistics.median(x["fee_yield"] for x in with_fee)]
        print(f"\nfee test: high fee-yield tokens averaged "
              f"{sum(hs)/len(hs):+.0f}% over 12m vs {sum(ls)/len(ls):+.0f}% "
              f"for low ({len(with_fee)} matched)")

    OUT.write_text(json.dumps({"generated": str(datetime.now().date()),
                               "coins": rows}, indent=1), encoding="utf-8")
    print(f"\nwrote {OUT.name}: {len(rows)} coins")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
