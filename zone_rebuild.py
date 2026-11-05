"""Rebuild the panel from zone_watch.json without any network calls."""
import json
from pathlib import Path

ROOT = Path(r"C:\Users\robai\Documents\Standaardproject\balanceviewer")
zone = json.loads((ROOT / "zone_watch.json").read_text(encoding="utf-8"))
prev = set()

names = []
for z in zone.get("zone", []):
    checks = [z["dd"] <= -30, z["from_low"] <= 25, z.get("r30", 0) > 0,
              z["dd"] <= -30]          # basing held by construction
    names.append({"ticker": z["ticker"], "name": "", "sector": "?",
                  "pe": None, "ey": None, "dd": z["dd"],
                  "from_low": z["from_low"], "r30": z.get("r30", 0.0),
                  "rsi": z["rsi"], "from_30d_low": z.get("from_30d_low", 0.0),
                  "score": sum(checks), "checks": checks, "med": None,
                  "rel": None, "roe": None, "cagr": None, "buy": False,
                  "near": False, "zone": True,
                  "new_zone": z["ticker"] not in prev})

(ROOT / "watchlist.json").write_text(json.dumps({
    "rules": {"risk_free": 4.89, "pe_max": 17.0, "drawdown_min": 15.0,
              "asof": "2026-11-05"},
    "source": "zone",
    "zone": [n["ticker"] for n in names],
    "names": names}, indent=1), encoding="utf-8")
print(f"rebuilt panel with {len(names)} zone names")
