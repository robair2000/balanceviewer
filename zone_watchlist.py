"""Daily: every name currently basing in the zone, enriched with fundamentals.

zone_scan.py finds them using prices only (one batched download, ~35s).
This then fetches P/E and sector for those handful of names - a dozen lookups
instead of eight thousand - and rewrites watchlist.json, so the WATCHLIST panel
always shows names that are in the box *today*, not a list curated weeks ago.

    python zone_watchlist.py
"""
from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

import zone_scan
from refresh_watchlist import DRAWDOWN_MIN, MAX_NAMES, PREMIUM, check, risk_free

ROOT = Path(__file__).resolve().parent
ZONE = ROOT / "zone_watch.json"
OUT = ROOT / "watchlist.json"


def main() -> int:
    universe = zone_scan.load_universe()
    rows = zone_scan.scan_universe()

    # a rate-limited download silently returns a fraction of the universe; do
    # not overwrite a good list with a partial one
    coverage = getattr(zone_scan, "LAST_COVERAGE", 1.0)
    if coverage < 0.8:
        print(f"only {coverage:.0%} of the universe downloaded - keeping the "
              f"previous list, will retry later", flush=True)
        return 1

    prev: set[str] = set()
    try:
        prev = set(json.loads(ZONE.read_text(encoding="utf-8")).get("zone") or [])
    except Exception:                                     # noqa: BLE001
        prev = set()
    for r in rows:
        r["new"] = r["ticker"] not in prev
    ZONE.write_text(json.dumps({"generated": str(date.today()),
                                "count": len(rows), "zone": rows}, indent=1),
                    encoding="utf-8")

    rf = risk_free()
    pe_max = 100.0 / (rf + PREMIUM)
    print(f"risk-free {rf:.2f}% -> P/E ceiling {pe_max:.1f}x; "
          f"enriching {len(rows)} zone names", flush=True)

    names = []
    for z in rows:
        sym = z["ticker"]
        try:
            r = check(sym)
        except Exception as exc:                          # noqa: BLE001
            print(f"  skip {sym}: {str(exc)[:40]}", flush=True)
            r = None
        time.sleep(0.15)
        if not r:
            # no forward P/E available (small caps often lack estimates), or the
            # fundamentals call failed - still show it, priced off the zone data
            checks = [z["dd"] <= -30.0, z["from_low"] <= 25.0, z["r30"] > 0, True]
            r = {"ticker": sym, "name": sym, "sector": "?",
                 "pe": None, "ey": None, "dd": z["dd"],
                 "from_low": z["from_low"], "r30": z.get("r30", 0.0),
                 "rsi": z["rsi"], "from_30d_low": z.get("from_30d_low", 0.0),
                 "score": sum(checks), "checks": checks,
                 "med": None, "rel": None, "roe": None, "cagr": None}
        r["buy"] = (bool(r.get("pe")) and r["pe"] <= pe_max
                    and r["dd"] <= -DRAWDOWN_MIN and r["pe"] >= 4)
        r["near"] = (bool(r.get("pe")) and r["pe"] <= pe_max * 1.10
                     and r["dd"] <= -(DRAWDOWN_MIN - 5))
        r["zone"] = True
        r["new_zone"] = bool(z.get("new"))
        names.append(r)

    # one name per sector first, so a corner of the market cannot fill the panel
    names.sort(key=lambda r: (-r["score"],
                              r["rel"] if r["rel"] is not None else 99))
    keep, per_sector = [], {}
    for r in names:
        sector = r.get("sector") or "?"
        if sector != "?" and per_sector.get(sector, 0) >= 2:
            continue
        keep.append(r)
        if sector != "?":
            per_sector[sector] = per_sector.get(sector, 0) + 1
        if len(keep) >= MAX_NAMES:
            break

    OUT.write_text(json.dumps({
        "rules": {"risk_free": round(rf, 2), "pe_max": round(pe_max, 1),
                  "drawdown_min": DRAWDOWN_MIN, "asof": str(date.today())},
        "source": "zone",
        "zone": [r["ticker"] for r in keep],
        "names": keep}, indent=1), encoding="utf-8")

    print(f"{len(keep)} on the panel, {sum(1 for r in keep if r['new_zone'])} new")
    for r in keep:
        pe = f"{r['pe']:5.1f}x" if r.get("pe") else "   -  "
        print(f"  {'NEW ' if r['new_zone'] else '    '}{r['ticker']:10s} "
              f"{pe}  dd {r['dd']:+5.0f}%  rsi {r['rsi']:4.1f}  "
              f"{r['score']}/4  {(r.get('sector') or '?')[:18]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
