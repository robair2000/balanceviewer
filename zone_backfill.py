"""One-off: add the zone flags to the existing watchlist without a full rescan."""
import json
from pathlib import Path

from refresh_watchlist import check

OUT = Path(r"C:\Users\robai\Documents\Standaardproject\balanceviewer"
           r"\watchlist.json")
data = json.loads(OUT.read_text(encoding="utf-8"))
prev_zone = set(data.get("zone") or [])

now_zone = []
for n in data["names"]:
    sym = n["ticker"]
    try:
        r = check(sym)
    except Exception as exc:                              # noqa: BLE001
        print(f"  {sym}: {str(exc)[:50]}")
        r = None
    z = bool(r and r.get("zone"))
    n["zone"] = z
    n["new_zone"] = bool(z and sym not in prev_zone)
    if z:
        now_zone.append(sym)
    print(f"  {sym:9s} zone={z}")

data["zone"] = now_zone
OUT.write_text(json.dumps(data, indent=1), encoding="utf-8")
print("\nin zone:", now_zone)
