"""Build universe.txt from S&P 500 + STOXX Europe 600 constituents.

Maps each constituent to its Yahoo symbol, validates it returns prices,
and writes the survivors. Run monthly (or when the indices change).
"""
from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

OUT = Path(__file__).resolve().parent / "universe.txt"
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) research"}

SUFFIX = {
    "Frankfurt Stock Exchange": ".DE",
    "London Stock Exchange": ".L",
    "SIX Swiss Exchange": ".SW",
    "Nasdaq Stockholm": ".ST",
    "Borsa Italiana": ".MI",
    "Bolsa de Madrid": ".MC",
    "Nasdaq Copenhagen": ".CO",
    "Warsaw Stock Exchange": ".WA",
    "Nasdaq Helsinki": ".HE",
    "Euronext Athens": ".AT",
    "Wiener Börse": ".VI",
}
EURONEXT = {"France": ".PA", "Netherlands": ".AS", "Belgium": ".BR",
            "Portugal": ".LS", "Ireland": ".IR"}


def fetch(url: str) -> list[pd.DataFrame]:
    r = requests.get(url, headers=HDR, timeout=60)
    r.raise_for_status()
    return pd.read_html(io.StringIO(r.text))


def yahoo_symbol(ticker: str, listing: str, country: str) -> str | None:
    t = str(ticker).strip().replace(" ", "")
    if not t:
        return None
    if listing == "Euronext":
        suf = EURONEXT.get(country)
    else:
        suf = SUFFIX.get(listing)
    return f"{t}{suf}" if suf else None


def main() -> int:
    syms: dict[str, str] = {}

    sp = fetch("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]
    for _, row in sp.iterrows():
        s = str(row["Symbol"]).replace(".", "-").strip()
        syms[s] = str(row["Security"])

    st = None
    for d in fetch("https://en.wikipedia.org/wiki/STOXX_Europe_600"):
        if "Ticker" in d.columns and "Main listing" in d.columns:
            st = d
            break
    if st is not None:
        for _, row in st.iterrows():
            sym = yahoo_symbol(row["Ticker"], row["Main listing"], row["Country"])
            if sym:
                syms[sym] = str(row["Company"])

    print(f"candidates: {len(syms)}")

    good: list[str] = []
    keys = list(syms)
    for i in range(0, len(keys), 40):
        batch = keys[i:i + 40]
        try:
            d = yf.download(batch, period="5d", progress=False,
                            auto_adjust=True)["Close"]
            if isinstance(d, pd.Series):
                d = d.to_frame(batch[0])
            good += [t for t in batch if t in d.columns and d[t].notna().any()]
        except Exception as exc:                            # noqa: BLE001
            print(f"  batch {i}: {str(exc)[:50]}")
        print(f"{i + len(batch)}/{len(keys)}", end="\r", flush=True)

    good = sorted(set(good))
    OUT.write_text("# S&P 500 + STOXX 600 constituents, validated\n"
                   + "\n".join(good) + "\n", encoding="utf-8")
    print(f"\nwrote {len(good)} symbols to {OUT.name}")
    missing = len(syms) - len(good)
    print(f"dropped {missing} that returned no prices")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
