"""Catalyst scan: published events on the names we own or watch.

US names  -> SEC EDGAR 8-K item codes (2.02 results, 1.01 material agreements)
All names -> Google News RSS headlines, keyword-classified and date-filtered

It detects catalysts that have been *published*. It cannot front-run them: by
the time a story is indexed, the move is usually in the price.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "alerts.json"
HDR = {"User-Agent": "catalyst-scan research contact: robai@example.com"}
NEWS_HDR = {"User-Agent": "Mozilla/5.0 research"}

DAYS_NEWS = 7
DAYS_FILING = 14

KINDS = [
    ("EARNINGS", re.compile(r"beat|miss|raise[sd]? (guidance|outlook|forecast)|"
                            r"record (revenue|orders|sales)|q[1-4] (results|earnings)|"
                            r"earnings|profit (jump|surge|fall)", re.I)),
    ("CONTRACT", re.compile(r"\b(contract|order|agreement|customer|deal|wins?|"
                            r"award|partnership)\b", re.I)),
    ("UPGRADE", re.compile(r"price target|upgrade|downgrade|buy rating|overweight|"
                           r"initiates coverage", re.I)),
]

ITEM_KIND = {"2.02": "EARNINGS", "1.01": "CONTRACT", "7.01": "UPDATE",
             "8.01": "EVENT", "5.02": "MANAGEMENT"}

NOISE = re.compile(
    r"stock price|price, news|quote & history|tradingview|yahoo! finance|"
    r"marketbeat|acquires new stake|new stake|holdings|insider|shares outstanding|"
    r"analyst estimates|consensus|transcript|notification of|form 4|form 13",
    re.I)
KEEP = {"EARNINGS", "CONTRACT", "UPGRADE"}


def classify(text: str) -> str:
    for kind, rx in KINDS:
        if rx.search(text):
            return kind
    return "NEWS"


def load_tickers() -> dict[str, str]:
    """ticker -> company name, from the watchlist plus the live positions."""
    out = {"BSL.DE": "Basler AG", "TER": "Teradyne", "TEM": "Tempus AI"}
    wl = ROOT / "watchlist.json"
    if wl.exists():
        try:
            for n in json.loads(wl.read_text(encoding="utf-8")).get("names", []):
                out.setdefault(n["ticker"], n.get("name") or n["ticker"])
        except Exception:                                  # noqa: BLE001
            pass
    pos = ROOT / "positions.json"
    if pos.exists():
        try:
            for p in json.loads(pos.read_text(encoding="utf-8")):
                out.setdefault(p["ticker"], p.get("name") or p["ticker"])
        except Exception:                                  # noqa: BLE001
            pass
    return out


def sec_map() -> dict[str, str]:
    try:
        r = requests.get("https://www.sec.gov/files/company_tickers.json",
                         headers=HDR, timeout=30)
        return {v["ticker"]: str(v["cik_str"]).zfill(10)
                for v in r.json().values()}
    except Exception:                                      # noqa: BLE001
        return {}


def filings(ticker: str, cik: str) -> list[dict]:
    try:
        r = requests.get(f"https://data.sec.gov/submissions/CIK{cik}.json",
                         headers=HDR, timeout=30)
        rec = r.json().get("filings", {}).get("recent", {})
    except Exception:                                      # noqa: BLE001
        return []
    out = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS_FILING)
    for i, form in enumerate(rec.get("form", [])):
        try:
            d = datetime.strptime(rec["filingDate"][i], "%Y-%m-%d").replace(
                tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        if d < cutoff:
            continue
        items = (rec.get("items") or [""] * len(rec["form"]))[i] or ""
        if form not in ("8-K", "8-K/A"):
            continue
        kind = next((ITEM_KIND[x.strip()] for x in items.split(",")
                     if x.strip() in ITEM_KIND), "EVENT")
        out.append({"ticker": ticker, "date": d.date().isoformat(),
                    "kind": kind, "source": "SEC 8-K",
                    "text": f"8-K {items}".strip()})
    return out


def news(ticker: str, name: str) -> list[dict]:
    q = requests.utils.quote(f'"{name}"')
    url = (f"https://news.google.com/rss/search?q={q}"
           "&hl=en-US&gl=US&ceid=US:en")
    try:
        r = requests.get(url, headers=NEWS_HDR, timeout=20)
        root = ET.fromstring(r.content)
    except Exception:                                      # noqa: BLE001
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS_NEWS)
    out = []
    for it in root.findall(".//item")[:20]:
        title = (it.findtext("title") or "").strip()
        if NOISE.search(title):
            continue
        pub = it.findtext("pubDate") or ""
        try:
            d = parsedate_to_datetime(pub)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if d < cutoff:
            continue
        out.append({"ticker": ticker, "date": d.date().isoformat(),
                    "kind": classify(title), "source": "news",
                    "text": title[:120]})
    return out


def main() -> int:
    tickers = load_tickers()
    ciks = sec_map()
    alerts = []
    for t, name in tickers.items():
        if t in ciks:
            alerts += filings(t, ciks[t])
        alerts += news(t, name.split(" ")[0] if len(name) > 18 else name)

    seen, uniq = set(), []
    for a in sorted(alerts, key=lambda x: x["date"], reverse=True):
        if a["kind"] not in KEEP:
            continue
        key = (a["ticker"], a["text"][:70])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(a)

    OUT.write_text(json.dumps({"generated": datetime.now(timezone.utc).isoformat(
        timespec="minutes"), "alerts": uniq[:40]}, indent=1), encoding="utf-8")

    print(f"{len(uniq)} alerts from {len(tickers)} names\n")
    for a in uniq[:12]:
        print(f"  {a['date']}  {a['ticker']:8s} {a['kind']:10s} "
              f"{a['text'][:70]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
