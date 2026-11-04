"""Update the Basler thesis panel on the wallpaper.

    python update_basler.py --orders 55 --margin 48 --guidance intact

Numbers come from Basler's quarterly report / ad-hoc release (Auftragseingang,
Rohertrag/Umsatz, Ausblick). The wallpaper picks the file up within one tick.
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILE = HERE / "basler_metrics.json"


def main() -> int:
    ap = argparse.ArgumentParser(description="Basler thesis metrics")
    ap.add_argument("--orders", type=float,
                    help="incoming orders, year-on-year, in percent (e.g. 62)")
    ap.add_argument("--margin", type=float,
                    help="gross margin, in percent (e.g. 49)")
    ap.add_argument("--ebit-low", type=float,
                    help="EBIT margin guide, low end, in percent")
    ap.add_argument("--ebit-high", type=float,
                    help="EBIT margin guide, high end, in percent")
    ap.add_argument("--guidance", choices=["raised", "intact", "cut"])
    ap.add_argument("--next", help="next report date, YYYY-MM-DD")
    ap.add_argument("--asof", default=str(date.today()))
    ap.add_argument("--source", default="")
    args = ap.parse_args()

    data = json.loads(FILE.read_text(encoding="utf-8")) if FILE.exists() else {}
    data.setdefault("limits", {"order_growth_min": 20.0, "gross_margin_min": 46.0})

    for key, value in (("order_growth", args.orders),
                       ("gross_margin", args.margin),
                       ("ebit_low", args.ebit_low),
                       ("ebit_high", args.ebit_high),
                       ("guidance", args.guidance)):
        if value is not None:
            data[key] = value
    if args.next:
        data["next_report"] = args.next
    if args.asof:
        data["asof"] = args.asof
    if args.source:
        data["source"] = args.source

    FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(f"updated {FILE.name}: orders {data.get('order_growth')}% | "
          f"margin {data.get('gross_margin')}% | guidance {data.get('guidance')} "
          f"| as of {data.get('asof')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
