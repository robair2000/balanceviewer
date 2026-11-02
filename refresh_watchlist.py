"""Monthly refresh of the watchlist.

Reads universe.txt, applies the six quality filters, then ranks the passers by
the buy rule:

    forward earnings yield >= risk-free + 1%      (cheap on earnings)
    AND price at least 15% below its 52-week high (already punished)

Writes watchlist.json (max 12 names) and appends to watchlist_log.txt.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent
UNIVERSE = ROOT / "universe.txt"
OUT = ROOT / "watchlist.json"
LOG = ROOT / "watchlist_log.txt"

MAX_NAMES = 12
DRAWDOWN_MIN = 15.0          # percent below the 52-week high
PREMIUM = 1.0                # earnings yield must beat risk-free by this much


def series(df: pd.DataFrame, *names: str) -> pd.Series | None:
    if df is None:
        return None
    for n in names:
        if n in df.index:
            s = df.loc[n].dropna()
            if len(s):
                return s.sort_index()
    return None


def risk_free() -> float:
    try:
        h = yf.download("^TNX", period="5d", progress=False,
                        auto_adjust=True)["Close"].dropna()
        return float(h.iloc[-1].iloc[0] if hasattr(h.iloc[-1], "iloc")
                     else h.iloc[-1])
    except Exception:                                      # noqa: BLE001
        return 4.9


def check(sym: str) -> dict | None:
    t = yf.Ticker(sym)
    fin, bs, cf = t.financials, t.balance_sheet, t.cashflow
    rev = series(fin, "Total Revenue")
    ni = series(fin, "Net Income", "Net Income Common Stockholders")
    gp = series(fin, "Gross Profit")
    if rev is None or ni is None or len(ni) < 3:
        return None

    f1 = int((ni > 0).sum()) >= 3

    yrs = (rev.index[-1] - rev.index[0]).days / 365.25
    cagr = (rev.iloc[-1] / rev.iloc[0]) ** (1 / yrs) - 1 if yrs > 0 else 0
    f2 = cagr > 0.05

    f3 = False
    if gp is not None and len(gp) >= 2:
        gm = gp / rev.reindex(gp.index)
        f3 = float(gm.iloc[-1]) >= float(gm.iloc[0]) - 0.01

    debt = series(bs, "Total Debt")
    cash = series(bs, "Cash And Cash Equivalents")
    ebit = series(fin, "EBIT", "Operating Income")
    da = series(cf, "Depreciation And Amortization")
    f4 = False
    if debt is not None and cash is not None and ebit is not None and da is not None:
        common = ebit.index.intersection(da.index)
        if len(common):
            ebitda = float(ebit.loc[common[-1]] + da.loc[common[-1]])
            if ebitda > 0:
                f4 = (float(debt.iloc[-1]) - float(cash.iloc[-1])) / ebitda < 2

    shares = series(bs, "Ordinary Shares Number", "Share Issued")
    f5 = False
    if shares is not None and len(shares) >= 2:
        f5 = float(shares.iloc[-1]) / float(shares.iloc[0]) - 1 <= 0.02

    equity = series(bs, "Stockholders Equity", "Common Stock Equity")
    roe = float("nan")
    f6 = False
    if equity is not None and len(equity) and float(equity.iloc[-1]) > 0:
        roe = float(ni.iloc[-1]) / float(equity.iloc[-1])
        f6 = roe > 0.10

    if not all([f1, f2, f3, f4, f5, f6]):
        return None

    info = t.info
    fpe = info.get("forwardPE")
    price = info.get("currentPrice")
    h = t.history(period="1y", auto_adjust=True)["Close"].dropna()
    if not fpe or not price or not len(h):
        return None

    high = float(h.max())
    dd = float(h.iloc[-1]) / high - 1
    ey = 100.0 / float(fpe)
    return {"ticker": sym, "name": info.get("shortName") or sym,
            "pe": float(fpe), "ey": ey, "dd": dd * 100,
            "roe": roe, "cagr": cagr}


def main() -> int:
    rf = risk_free()
    pe_max = 100.0 / (rf + PREMIUM)
    tickers = [l.strip() for l in UNIVERSE.read_text(encoding="utf-8").splitlines()
               if l.strip() and not l.startswith("#")]
    print(f"risk-free {rf:.2f}% -> P/E ceiling {pe_max:.1f}x; "
          f"{len(tickers)} names")

    rows = []
    for i, sym in enumerate(tickers, 1):
        try:
            r = check(sym)
        except Exception as exc:                            # noqa: BLE001
            print(f"  skip {sym}: {str(exc)[:40]}", file=sys.stderr)
            r = None
        if r:
            r["buy"] = r["pe"] <= pe_max and r["dd"] <= -DRAWDOWN_MIN
            r["near"] = (r["pe"] <= pe_max * 1.10
                         and r["dd"] <= -(DRAWDOWN_MIN - 5))
            rows.append(r)
        print(f"{i}/{len(tickers)}", end="\r", flush=True)

    rows.sort(key=lambda r: (r["buy"], r["near"], r["ey"]), reverse=True)
    keep = rows[:MAX_NAMES]

    rules = {"risk_free": round(rf, 2), "pe_max": round(pe_max, 1),
             "drawdown_min": DRAWDOWN_MIN, "asof": str(date.today())}
    OUT.write_text(json.dumps({"rules": rules, "names": keep}, indent=1),
                   encoding="utf-8")

    lines = [f"{date.today()} refresh: {len(rows)} passed quality, "
             f"{sum(r['buy'] for r in rows)} at BUY, kept {len(keep)}"]
    for r in keep:
        flag = "BUY" if r["buy"] else ("near" if r["near"] else "")
        lines.append(f"  {r['ticker']:10s} {r['name'][:28]:28s} "
                     f"{r['pe']:5.1f}x  dd {r['dd']:+5.0f}%  {flag}")
    LOG.write_text(("\n".join(lines) + "\n"), encoding="utf-8")

    print("\n" + "\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
