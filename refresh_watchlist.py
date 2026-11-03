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
import time
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


def median_pe(t, info) -> float | None:
    """Median historical P/E from annual EPS and the price at each year-end."""
    fin = t.financials
    if fin is None or "Basic EPS" not in fin.index:
        return None
    eps = fin.loc["Basic EPS"].dropna().sort_index()
    if len(eps) < 3:
        return None
    h = t.history(period="6y", auto_adjust=True)["Close"].dropna()
    if not len(h):
        return None
    try:                       # financial-year dates are tz-naive
        h.index = h.index.tz_localize(None)
    except (TypeError, AttributeError):
        pass
    pes = []
    for d, e in eps.items():
        if not e or e <= 0:
            continue
        try:
            d = pd.Timestamp(d).tz_localize(None)
        except (TypeError, ValueError):
            pass
        px = h[h.index <= d]
        if not len(px):
            continue
        pe = float(px.iloc[-1]) / float(e)
        if pe > 300:                  # pence vs pounds
            pe /= 100
        if 0 < pe < 300:
            pes.append(pe)
    if not pes:
        return None
    return float(pd.Series(pes).median())


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
    cagr = ((rev.iloc[-1] / rev.iloc[0]) ** (1 / yrs) - 1
            if yrs > 0 and rev.iloc[0] > 0 else 0)
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
    cap = info.get("marketCap") or 0
    if cap < 2e9:
        return None
    fpe = info.get("forwardPE")
    price = info.get("currentPrice")
    h = t.history(period="1y", auto_adjust=True)["Close"].dropna()
    if not fpe or not price or not len(h):
        return None

    high = float(h.max())
    dd = float(h.iloc[-1]) / high - 1
    ey = 100.0 / float(fpe)
    med = median_pe(t, info)
    rel = (float(fpe) / med) if med and med > 0 else None
    return {"ticker": sym, "name": info.get("shortName") or sym,
            "sector": info.get("sector") or "?",
            "pe": float(fpe), "ey": ey, "dd": dd * 100,
            "med": med, "rel": rel, "roe": roe, "cagr": cagr}


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="only scan the first N names")
    ap.add_argument("--no-write", action="store_true",
                    help="test run: print results, write nothing")
    args = ap.parse_args()

    rf = risk_free()
    pe_max = 100.0 / (rf + PREMIUM)
    tickers = [l.strip() for l in UNIVERSE.read_text(encoding="utf-8").splitlines()
               if l.strip() and not l.startswith("#")]
    if args.limit:
        tickers = tickers[:args.limit]
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
            r["buy"] = (r["pe"] <= pe_max and r["dd"] <= -DRAWDOWN_MIN
                        and r["pe"] >= 4)
            r["near"] = (r["pe"] <= pe_max * 1.10
                         and r["dd"] <= -(DRAWDOWN_MIN - 5))
            rows.append(r)
        time.sleep(0.15)                 # stay under Yahoo's rate limit
        print(f"{i}/{len(tickers)}", end="\r", flush=True)

    rows.sort(key=lambda r: (0 if r["buy"] else 1,
                             r["rel"] if r["rel"] is not None else 99))
    keep: list[dict] = []
    per_sector: dict[str, int] = {}
    for r in rows:
        if per_sector.get(r["sector"], 0) >= 2:
            continue
        keep.append(r)
        per_sector[r["sector"]] = per_sector.get(r["sector"], 0) + 1
        if len(keep) >= MAX_NAMES:
            break

    rules = {"risk_free": round(rf, 2), "pe_max": round(pe_max, 1),
             "drawdown_min": DRAWDOWN_MIN, "asof": str(date.today())}
    if args.no_write:
        print(f"\nTEST RUN: {len(rows)} passed quality, "
              f"{sum(r['buy'] for r in rows)} at BUY")
        for r in keep:
            print(f"  {r['ticker']:10s} {r['name'][:26]:26s} {r['pe']:5.1f}x  "
                  f"dd {r['dd']:+5.0f}%  rel {r.get('rel')}")
        return 0

    OUT.write_text(json.dumps({"rules": rules, "names": keep}, indent=1),
                   encoding="utf-8")

    lines = [f"{date.today()} refresh: {len(rows)} passed quality, "
             f"{sum(r['buy'] for r in rows)} at BUY, kept {len(keep)}"]
    for r in keep:
        flag = "BUY" if r["buy"] else ("near" if r["near"] else "")
        rel = f"{r['rel']:.2f}x own median" if r.get("rel") else "-"
        lines.append(f"  {r['ticker']:10s} {r['name'][:26]:26s} "
                     f"{r['pe']:5.1f}x  dd {r['dd']:+5.0f}%  {rel:16s} {flag}")
    LOG.write_text(("\n".join(lines) + "\n"), encoding="utf-8")

    print("\n" + "\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
