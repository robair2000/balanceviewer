"""Fill in the company names (and P/E if available) for the names on the panel."""
import json
import time
from pathlib import Path

import yfinance as yf

ROOT = Path(r"C:\Users\robai\Documents\Standaardproject\balanceviewer")
WL = ROOT / "watchlist.json"

data = json.loads(WL.read_text(encoding="utf-8"))
pe_max = float((data.get("rules") or {}).get("pe_max") or 17.0)

for n in data.get("names", []):
    sym = n["ticker"]
    try:
        i = yf.Ticker(sym).info
    except Exception as exc:                                # noqa: BLE001
        print(f"  {sym:9s} failed: {str(exc)[:40]}")
        continue
    name = i.get("longName") or i.get("shortName") or ""
    # trim the corporate suffix so it fits next to the ticker
    for suffix in (", Inc.", " Inc.", " Corporation", " plc", " PLC", " AG",
                   " SE", " S.A.", " N.V.", " Group", " Company"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    fpe = i.get("forwardPE")
    n["name"] = name
    n["sector"] = i.get("sector") or n.get("sector") or "?"
    if fpe:
        n["pe"] = round(float(fpe), 1)
        n["buy"] = bool(n["pe"] <= pe_max and n["dd"] <= -15 and n["pe"] >= 4)
    print(f"  {sym:9s} {name[:38]:38s} pe {n.get('pe')}")
    time.sleep(0.2)

WL.write_text(json.dumps(data, indent=1), encoding="utf-8")
print(f"updated {len(data.get('names', []))} names")
