"""Catalyst scan: published events on the names we own or watch.

US names  -> SEC EDGAR 8-K item codes (2.02 results, 1.01 material agreements)
EU names  -> finanznachrichten.de (EQS/DGAP ad-hocs, read straight off the wire)
All names -> Google News RSS headlines, keyword-classified and date-filtered

German ad-hocs index on Google News with a lag of hours, which is why Basler's
06:51 nine-month report never reached the card on the morning it was published.
The EU source exists to close that gap.

It detects catalysts that have been *published*. It cannot front-run them: by
the time a story is indexed, the move is usually in the price.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "alerts.json"
HDR = {"User-Agent": "catalyst-scan research contact: robai@example.com"}
NEWS_HDR = {"User-Agent": "Mozilla/5.0 research"}
BROWSER_HDR = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/124.0 Safari/537.36"),
               "Accept-Language": "de-DE,de;q=0.9,en;q=0.8"}

# ticker -> finanznachrichten page slug; add a name here to cover it
EU_SLUGS = {
    "BSL.DE": "basler-ag",
    "GBF.DE": "bilfinger-se",
    "R3NK.DE": "renk-group-ag",
    "PGHN.SW": "partners-group-holding-ag",
    "MONC.MI": "moncler-spa",
    "FRES.L": "fresnillo-plc",
    "HOC.L": "hochschild-mining-plc",
}
EU_ROW = re.compile(r'<td class="zentriert">([^<]{1,12})</td><td class="zl">'
                    r'<a href="([^"]+)"[^>]*?title="([^"]+)"')
# voting-rights notices and directors' dealings are filings, not catalysts
EU_SKIP = re.compile(r"^(EQS-PVR|EQS-DD|EQS-Stimmrechte|DGAP-Stimmrechte)|"
                     r"Release according to Article 40|Managers' transactions",
                     re.I)
# "Basler Aktiengesellschaft: 9-Monatsergebnis ..." -> "9-Monatsergebnis ..."
EU_PREFIX = re.compile(r"^[A-ZÄÖÜ][\w&.\- ]{2,40}?(?:Aktiengesellschaft|AG|SE|"
                       r"Inc\.?|PLC|S\.p\.A\.|Holding|Group|Corporation|N\.V\.)"
                       r"\s*:\s*")

DAYS_NEWS = 7
DAYS_FILING = 14

KINDS = [
    ("EARNINGS", re.compile(
        r"beat|miss|rais(e|es|ed) (its )?(guidance|outlook|forecast)|"
        r"increas(e|es|ed) (of )?(guidance|forecast|outlook)|"
        r"hike[sd]? (guidance|forecast)|record (revenue|orders|sales)|"
        r"q[1-4] (results|earnings)|quarterly (results|earnings)|"
        r"preliminary (\d+|nine|six|three|twelve)-month|"
        r"(annual|full-year|half-year|nine-month) (results?|forecast)|"
        r"earnings|profit (jump|surge|fall)|"
        # German wire wording
        r"monats(ergebnis|zahlen)|halbjahres|quartals(ergebnis|zahlen)|"
        r"geschäftszahlen|hebt (die |seine )?prognose|"
        r"prognose (angehoben|erhöht|gesenkt)|ergebnis deutlich", re.I)),
    ("CONTRACT", re.compile(r"\b(contract|order|agreement|customer|deal|wins?|"
                            r"award|partnership)\b|"
                            r"auftrag|rahmenvertrag|kooperation|"
                            r"vertrag (über|mit)|großauftrag", re.I)),
    ("UPGRADE", re.compile(r"price target|upgrade|downgrade|buy rating|overweight|"
                           r"initiates coverage|kursziel|hochgestuft|"
                           r"herabgestuft|stuft .{0,25} (auf|hoch|runter)", re.I)),
]

ITEM_KIND = {"2.02": "EARNINGS", "1.01": "CONTRACT", "7.01": "UPDATE",
             "8.01": "EVENT", "5.02": "MANAGEMENT"}

NOISE = re.compile(
    r"stock price|price, news|quote & history|tradingview|yahoo! finance|"
    r"marketbeat|acquires new stake|new stake|holdings|insider|shares outstanding|"
    r"analyst estimates|consensus|transcript|notification of|form 4|form 13|"
    r"nebenwerte-tag|marktbericht",          # daily roundups, not one-name news
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


def de_date(cell: str) -> str | None:
    """'08:18' is today's row; '30.10.' / '30.10.2026' are older ones."""
    cell = cell.strip().rstrip(".")
    today = datetime.now(timezone.utc).date()
    if re.fullmatch(r"\d{1,2}:\d{2}", cell):
        return today.isoformat()
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})?", cell)
    if not m:
        return None
    day, month = int(m.group(1)), int(m.group(2))
    year = int(m.group(3)) if m.group(3) else today.year
    try:
        d = date(year, month, day)
    except ValueError:
        return None
    if not m.group(3) and d > today:            # "30.12." seen in January
        try:
            d = date(year - 1, month, day)
        except ValueError:
            return None
    return d.isoformat()


def de_news(ticker: str, slug: str) -> list[dict]:
    """Ad-hocs from the German wire, in German, same day they are published."""
    try:
        r = requests.get(
            f"https://www.finanznachrichten.de/nachrichten-aktien/{slug}.htm",
            headers=BROWSER_HDR, timeout=20)
        r.raise_for_status()
    except Exception:                                      # noqa: BLE001
        return []

    cutoff = (datetime.now(timezone.utc) - timedelta(days=DAYS_NEWS)).date()
    out = []
    for cell, _link, title in EU_ROW.findall(r.text):
        d = de_date(cell)
        if d is None or date.fromisoformat(d) < cutoff:
            continue
        headline = re.sub(r"\s*\([^()]*\)\s*$", "", title).strip()
        headline = re.sub(r"^(EQS-News|EQS-Adhoc|DGAP-News|DGAP-Adhoc):\s*",
                          "", headline)
        headline = EU_PREFIX.sub("", headline).strip()
        if not headline or EU_SKIP.search(headline) or NOISE.search(headline):
            continue
        out.append({"ticker": ticker, "date": d, "kind": classify(headline),
                    "source": "EQS", "text": headline[:120]})
    return out


def main() -> int:
    tickers = load_tickers()
    ciks = sec_map()
    alerts = []
    for t, name in tickers.items():
        if t in ciks:
            alerts += filings(t, ciks[t])
        if t in EU_SLUGS:
            alerts += de_news(t, EU_SLUGS[t])
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
