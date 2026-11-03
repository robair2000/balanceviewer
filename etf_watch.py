"""ETF diversifier screen.

Universe: a curated list of UCITS funds an EU retail account can actually buy.
Hard filters: TER <= 0.40%, AUM >= EUR 500m (or a known-large fund), EUR-listed.
Ranking: correlation with the AI/tech factor (QQQ) — lowest first, because the
point is to add something that does not move with the rest of the book.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

OUT = Path(__file__).resolve().parent / "etf_watch.json"

UNIVERSE = {
    "XGLE.DE": "Eurozone Govt Bonds",
    "EXSA.DE": "STOXX Europe 600",
    "EUNK.DE": "MSCI Europe",
    "XDWH.DE": "World Health Care",
    "XDWS.DE": "World Consumer Staples",
    "XDWU.DE": "World Utilities",
    "XMME.DE": "Emerging Markets",
    "ZPRV.DE": "US Small Cap Value",
    "IWDA.AS": "MSCI World",
    "VWCE.DE": "FTSE All-World",
    "4GLD.DE": "Gold (ETC)",
}
KNOWN_AUM = {"IWDA.AS": 9.5e10, "4GLD.DE": 1.5e10}
SUMMARY = {"XGLE.DE": "govt bonds, 0.07%", "EXSA.DE": "600 European companies",
           "EUNK.DE": "Europe, large/mid", "XDWH.DE": "health care",
           "XDWS.DE": "staples", "XDWU.DE": "utilities",
           "XMME.DE": "emerging markets", "ZPRV.DE": "US small value",
           "IWDA.AS": "developed world", "VWCE.DE": "whole world",
           "4GLD.DE": "physical gold"}


def main() -> int:
    px = yf.download(list(UNIVERSE) + ["QQQ"], period="3y", progress=False,
                     auto_adjust=True)["Close"].ffill()
    rets = px.pct_change().dropna()
    rows = []
    for t, name in UNIVERSE.items():
        if t not in px.columns:
            continue
        h = px[t].dropna()
        if len(h) < 100:
            continue
        i = yf.Ticker(t).info
        ter = i.get("netExpenseRatio")
        aum = i.get("totalAssets") or KNOWN_AUM.get(t, 0)
        corr = float(rets[t].corr(rets["QQQ"]))
        rows.append({
            "ticker": t, "name": name, "note": SUMMARY.get(t, ""),
            "price": float(h.iloc[-1]),
            "dd3y": float(h.iloc[-1] / h.max() - 1) * 100,
            "corr": corr,
            "ter": float(ter) if ter else None,
            "aum_bn": float(aum) / 1e9,
        })

    # hard filters
    rows = [r for r in rows if (r["ter"] is None or r["ter"] <= 0.40)
            and r["aum_bn"] >= 0.5]
    rows.sort(key=lambda r: r["corr"])

    OUT.write_text(json.dumps({"generated": str(date.today()), "etfs": rows},
                              indent=1), encoding="utf-8")
    print(f"{'fund':26s} {'price':>8s} {'3y dd':>7s} {'corr':>6s} {'TER':>6s}")
    for r in rows:
        ter = f"{r['ter']:.2f}%" if r["ter"] is not None else "-"
        print(f"{r['name']:26s} {r['price']:8.2f} {r['dd3y']:+7.0f}% "
              f"{r['corr']:6.2f} {ter:>6s}")
    print(f"\n{len(rows)} funds written to {OUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
