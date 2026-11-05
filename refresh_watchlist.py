"""Monthly refresh of the watchlist.

Reads universe.txt, applies the six quality filters, then ranks the passers by
the buy rule:

    forward earnings yield >= risk-free + 1%      (cheap on earnings)
    AND price at least 15% below its 52-week high (already punished)

Writes watchlist.json (max 12 names) and appends to watchlist_log.txt.
"""
from __future__ import annotations

import json
import math
import statistics
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


def rsi(closes: list[float], n: int = 14) -> float:
    if len(closes) < n + 2:
        return 50.0
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    ag = sum(gains[:n]) / n
    al = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


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

    # bottoming score: deep, near the low, turning, basing
    closes = [float(x) for x in h.values]
    low52 = min(closes)
    from_low = closes[-1] / low52 - 1
    rs = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    r30 = closes[-1] / closes[-31] - 1 if len(closes) > 31 else 0.0

    def vol(xs):
        if len(xs) < 5:
            return float("nan")
        return statistics.pstdev(xs) * math.sqrt(252)

    v30, v1y = vol(rs[-30:]), vol(rs)
    r14 = rsi(closes)
    from_30d_low = closes[-1] / min(closes[-30:]) - 1

    # overbought or already well off the base = a bounce, not a bottom
    if r14 >= 70 or from_30d_low > 0.12:
        return None

    checks = [dd <= -0.30, from_low <= 0.25, r30 > 0,
              v30 == v30 and v1y == v1y and v30 < 0.85 * v1y]
    score = sum(checks)

    # "in the box": deep, near the low, quiet, flat, and not yet bounced.
    # This is the state PGHN sat in from 13 to 26 October - the signal has to
    # fire here, not when the turning check finally passes.
    ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else closes[-1]
    ma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else closes[-1]
    zone = bool(dd <= -0.30 and from_low <= 0.20 and 28 <= r14 <= 58
                and v30 == v30 and v1y == v1y and v30 < 0.85 * v1y
                and abs(closes[-1] / ma20 - 1) <= 0.06
                and closes[-1] < ma50)

    return {"ticker": sym, "name": info.get("shortName") or sym,
            "sector": info.get("sector") or "?",
            "pe": float(fpe), "ey": ey, "dd": dd * 100,
            "from_low": from_low * 100, "r30": r30 * 100,
            "rsi": r14, "from_30d_low": from_30d_low * 100,
            "score": score, "checks": checks, "zone": zone,
            "med": med, "rel": rel, "roe": roe, "cagr": cagr}


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="only scan the first N names")
    ap.add_argument("--names-only", action="store_true",
                    help="re-check the names already on the list (~1 minute)")
    ap.add_argument("--no-write", action="store_true",
                    help="test run: print results, write nothing")
    args = ap.parse_args()

    rf = risk_free()
    pe_max = 100.0 / (rf + PREMIUM)

    # ---- light pass: refresh the dozen names already on the list and work out
    # which ones have just entered the basing zone. This is what the wallpaper
    # loop runs daily; the full 1,644-name scan stays a manual monthly job.
    if args.names_only:
        data = json.loads(OUT.read_text(encoding="utf-8"))
        prev_zone = set(data.get("zone") or [])
        names = []
        for n in data.get("names", []):
            sym = n["ticker"]
            try:
                r = check(sym)
            except Exception:                              # noqa: BLE001
                r = None
            if r:
                r["buy"] = (r["pe"] <= pe_max and r["dd"] <= -DRAWDOWN_MIN
                            and r["pe"] >= 4)
                r["near"] = (r["pe"] <= pe_max * 1.10
                             and r["dd"] <= -(DRAWDOWN_MIN - 5))
                r["new_zone"] = bool(r.get("zone") and sym not in prev_zone)
            else:
                # filtered out (RSI >= 70 or already 12% off the low): no longer
                # basing, but keep it on the list with its last known numbers
                n["zone"] = False
                n["new_zone"] = False
                r = n
            names.append(r)
            time.sleep(0.15)
        data["names"] = names
        data["zone"] = [r["ticker"] for r in names if r.get("zone")]
        data.setdefault("rules", {})["asof"] = str(date.today())
        OUT.write_text(json.dumps(data, indent=1), encoding="utf-8")
        new = [r["ticker"] for r in names if r.get("new_zone")]
        print(f"light pass: {len(names)} names, {len(data['zone'])} in zone"
              + (f", NEW: {', '.join(new)}" if new else ""))
        return 0

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

    rows.sort(key=lambda r: (-r["score"],
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

    # zone entries since the previous scan, so a name that has just started
    # basing can be flagged instead of only appearing once it has turned
    prev_zone: set[str] = set()
    try:
        prev = json.loads(OUT.read_text(encoding="utf-8"))
        prev_zone = set(prev.get("zone") or [])
    except Exception:                                  # noqa: BLE001
        prev_zone = set()
    now_zone = [r["ticker"] for r in keep if r.get("zone")]
    for r in keep:
        r["new_zone"] = bool(r.get("zone") and r["ticker"] not in prev_zone)

    rules = {"risk_free": round(rf, 2), "pe_max": round(pe_max, 1),
             "drawdown_min": DRAWDOWN_MIN, "asof": str(date.today())}
    if args.no_write:
        print(f"\nTEST RUN: {len(rows)} passed quality")
        for r in keep:
            print(f"  {r['ticker']:10s} {r['name'][:24]:24s} {r['pe']:5.1f}x  "
                  f"dd {r['dd']:+5.0f}%  low {r['from_low']:+4.0f}%  "
                  f"30d {r['r30']:+5.0f}%  {r['score']}/4  "
                  f"{'ZONE' if r.get('zone') else '    '}  "
                  f"rel {r.get('rel')}")
        return 0

    OUT.write_text(json.dumps({"rules": rules, "zone": now_zone,
                               "names": keep}, indent=1),
                   encoding="utf-8")

    lines = [f"{date.today()} refresh: {len(rows)} passed quality, "
             f"{sum(r['buy'] for r in rows)} at BUY, kept {len(keep)}, "
             f"{len(now_zone)} in zone"]
    for r in keep:
        rel = f"{r['rel']:.2f}x own median" if r.get("rel") else "-"
        flags = ("ZONE " if r.get("zone") else "     ") + \
                ("NEW " if r.get("new_zone") else "    ")
        lines.append(f"  {r['ticker']:10s} {r['name'][:26]:26s} "
                     f"{r['pe']:5.1f}x  dd {r['dd']:+5.0f}%  "
                     f"low {r['from_low']:+4.0f}%  30d {r['r30']:+5.0f}%  "
                     f"{r['score']}/4  {flags}{rel}")
    LOG.write_text(("\n".join(lines) + "\n"), encoding="utf-8")

    print("\n" + "\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
