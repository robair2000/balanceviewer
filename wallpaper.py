"""Render the portfolio total as a Windows desktop wallpaper.

    python wallpaper.py --once        # render + apply (only if the numbers changed)
    python wallpaper.py --loop 600    # check every 10 minutes
    python wallpaper.py --no-set      # just write wallpaper.png, don't apply
    python wallpaper.py --restore     # forget state so the next run applies again

Notes:
- Text sits bottom-left, above the taskbar, so it doesn't fight your icons.
- Nothing flickers: the wallpaper is only re-applied when the rendered text differs.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageStat

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402  (needs ROOT on sys.path)

server.bootstrap_env()  # load .env when running outside server.py's main()

OUT = ROOT / "wallpaper.png"
STATE = ROOT / ".wallpaper_state.json"
# saxoBot writes its monthly plan here (equitybot/widget.py --text does too)
PLAN_CACHE = Path(os.getenv("SAXO_PLAN_CACHE",
                            str(ROOT.parent.parent / "equitybot" / "signal_cache.json")))
# manual stock positions (the ones outside the monthly rotation)
POSITIONS = Path(os.getenv("SAXO_POSITIONS_FILE", str(ROOT / "positions.json")))
# thesis checklist for the main holding
METRICS = Path(os.getenv("SAXO_METRICS_FILE", str(ROOT / "basler_metrics.json")))
# quality watchlist: buy trigger = N% below the 52-week high
WATCHLIST = Path(os.getenv("SAXO_WATCHLIST_FILE", str(ROOT / "watchlist.json")))
# crypto bottoming screen output
CRYPTO = Path(os.getenv("SAXO_CRYPTO_FILE", str(ROOT / "crypto_watch.json")))
# ETF diversifier screen output
ETFS = Path(os.getenv("SAXO_ETF_FILE", str(ROOT / "etf_watch.json")))

SCREEN_W, SCREEN_H = 1920, 1080
FONT_BOLD = "C:/Windows/Fonts/segoeuib.ttf"
FONT_SEMI = "C:/Windows/Fonts/seguisb.ttf"
FONT_REG = "C:/Windows/Fonts/segoeui.ttf"

BG_TOP, BG_BOTTOM = (16, 22, 32), (8, 10, 16)
PANEL = (18, 23, 34, 235)
BORDER = (34, 43, 61, 255)
TEXT = (232, 237, 247)
MUTED = (139, 151, 173)
DIM = (95, 107, 130)
GREEN = (46, 204, 155)
PURPLE = (155, 140, 255)

SPI_SETDESKWALLPAPER = 20
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDCHANGE = 0x02


def money(value) -> str:
    return "—" if value is None else f"${value:,.2f}"


def spaced(text: str, px: int = 3) -> str:
    return (" " * 0).join(list(text))


def draw_tracked(draw, xy, text, font, fill, tracking=3):
    """Letter-spaced text, drawn character by character."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking
    return x


def find_background(explicit: str | None = None) -> Path | None:
    """Explicit --bg, else assets/background.*, else background.* next to this file."""
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None
    for folder in (ROOT / "assets", ROOT):
        for pattern in ("background.jpg", "background.jpeg", "background.png", "background.webp"):
            candidate = folder / pattern
            if candidate.is_file():
                return candidate
    return None


def cover(path: Path, w: int, h: int) -> Image.Image:
    """Scale to fill w x h, centre-cropped, never stretched."""
    img = Image.open(path).convert("RGB")
    scale = max(w / img.width, h / img.height)
    if scale != 1:
        img = img.resize((max(w, round(img.width * scale)), max(h, round(img.height * scale))),
                         Image.LANCZOS)
    left, top = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((left, top, left + w, top + h))


def _scrim(corner: str) -> Image.Image:
    """Lighter of a vertical and a horizontal fade, darkening the panel's corner."""
    vert = Image.new("L", (SCREEN_W, SCREEN_H))
    vd = ImageDraw.Draw(vert)
    for y in range(SCREEN_H):
        t = (1 - y / (SCREEN_H - 1)) if corner.startswith("t") else (y / (SCREEN_H - 1))
        vd.line([(0, y), (SCREEN_W, y)], fill=int(170 * (max(t, 0) ** 2.0)))

    horz = Image.new("L", (SCREEN_W, SCREEN_H))
    hd = ImageDraw.Draw(horz)
    for x in range(SCREEN_W):
        t = (1 - x / (SCREEN_W - 1)) if corner.endswith("l") else (x / (SCREEN_W - 1))
        hd.line([(x, 0), (x, SCREEN_H)], fill=int(125 * (max(t, 0) ** 2.0)))

    return ImageChops.lighter(vert, horz).filter(ImageFilter.GaussianBlur(70))


def background(bg_path: Path | None, corner: str = "tl") -> Image.Image:
    if bg_path:
        img = cover(bg_path, SCREEN_W, SCREEN_H)

        # dim adaptively: dark photos keep their punch, bright ones get tamed
        mean = ImageStat.Stat(img.convert("L")).mean[0]
        dim = 0.10 + 0.30 * min(mean / 120.0, 1.0)
        img = Image.blend(img, Image.new("RGB", img.size, (6, 9, 14)), dim)

        # fade into the panel's corner so the text sits on calm pixels
        img = Image.composite(Image.new("RGB", img.size, (6, 9, 14)), img, _scrim(corner))
        return img.convert("RGBA")

    img = Image.new("RGB", (SCREEN_W, SCREEN_H), BG_BOTTOM)
    d = ImageDraw.Draw(img)
    for y in range(SCREEN_H):
        t = y / (SCREEN_H - 1)
        d.line([(0, y), (SCREEN_W, y)],
               fill=tuple(int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)))

    # soft glow so pure black doesn't look flat
    glow = Image.new("RGB", (SCREEN_W, SCREEN_H), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([-380, SCREEN_H - 780, 980, SCREEN_H + 220], fill=(22, 52, 70))
    gd.ellipse([SCREEN_W - 700, -300, SCREEN_W + 260, 420], fill=(38, 28, 64))
    glow = glow.filter(ImageFilter.GaussianBlur(170))
    return Image.blend(img, glow, 0.34).convert("RGBA")


def frosted_panel(canvas: Image.Image, box: tuple[int, int, int, int], radius: int) -> None:
    """Blurred glass with a dark tint, dropped onto the canvas in place."""
    x0, y0, x1, y1 = box
    region = canvas.crop(box).convert("RGB").filter(ImageFilter.GaussianBlur(26))
    glass = Image.alpha_composite(region.convert("RGBA"),
                                  Image.new("RGBA", region.size, (10, 14, 22, 132)))
    mask = Image.new("L", region.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, region.width - 1, region.height - 1],
                                           radius=radius, fill=255)
    canvas.paste(glass, (x0, y0), mask)

    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([x0, y0 + 14, x1, y1 + 14], radius=radius,
                                             fill=(0, 0, 0, 120))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(26)))
    canvas.paste(glass, (x0, y0), mask)


def panel_box(corner: str, pad_w: int, pad_h: int) -> tuple[int, int, int, int]:
    margin_x, margin_top, margin_bottom = 56, 52, 72
    x0 = margin_x if corner.endswith("l") else SCREEN_W - margin_x - pad_w
    y0 = margin_top if corner.startswith("t") else SCREEN_H - margin_bottom - pad_h
    return x0, y0, x0 + pad_w, y0 + pad_h


MAX_SUB_ROWS = 4


def build_rows(data: dict, hidden: bool) -> list[dict]:
    """One row per source, then its sub-accounts indented beneath it."""
    rows: list[dict] = []
    for src in data.get("sources", []):
        failed = bool(src.get("error"))
        rows.append({
            "label": src.get("label", "—"),
            "value": "••••" if hidden else ("—" if failed else money(src.get("total_usd"))),
            "indent": 0,
            "color": GREEN if src.get("id") == "kucoin" else PURPLE,
            "label_fill": (186, 196, 214),
            "value_fill": (255, 255, 255),
        })

        subs = src.get("subaccounts") or []
        for sub in subs[:MAX_SUB_ROWS]:
            rows.append({
                "label": sub.get("name") or "sub-account",
                "value": "••••" if hidden else money(sub.get("total_usd")),
                "indent": 13,
                "color": None,
                "label_fill": (148, 159, 180),
                "value_fill": (203, 211, 225),
            })
        if len(subs) > MAX_SUB_ROWS:
            rows.append({"label": f"+{len(subs) - MAX_SUB_ROWS} more", "value": "",
                         "indent": 13, "color": None,
                         "label_fill": (116, 126, 146), "value_fill": (150, 160, 180)})
    return rows


def fit_text(d, text: str, font, max_w: float) -> str:
    if d.textlength(text, font=font) <= max_w:
        return text
    while text and d.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


# ------------------------------------------------------------------ saxo plan

def read_plan() -> dict | None:
    """This month's saxoBot signal, written by equitybot."""
    try:
        cache = json.loads(PLAN_CACHE.read_text(encoding="utf-8"))
    except Exception:                                  # noqa: BLE001
        return None
    cur = cache.get("current")
    if not cur:
        return None
    return {"current": cur, "previous": cache.get("previous")}


def plan_actions(cur: dict, prev: dict | None) -> list[tuple[str, str]]:
    """[(side, text)] - what changed versus the previous month's signal."""
    rows = cur.get("rows") or []
    if not prev:
        return [("buy", f"BUY {r['ticker']} {r['shares']} sh") for r in rows]
    held = {r["ticker"]: r for r in (prev.get("rows") or [])}
    want = {r["ticker"]: r for r in rows}
    out: list[tuple[str, str]] = []
    for t, r in held.items():
        if t not in want:
            out.append(("sell", f"SELL {t} all ({r['shares']} sh)"))
    for t, r in want.items():
        delta = int(r["shares"]) - int(held.get(t, {}).get("shares", 0))
        if delta > 0:
            out.append(("buy", f"BUY  {t} +{delta} sh"))
        elif delta < 0:
            out.append(("sell", f"SELL {t} {-delta} sh"))
    if not out:
        out.append(("hold", "no change - hold"))
    return out


def render_plan(img: Image.Image, d: ImageDraw.ImageDraw, plan: dict,
                left_x: int, top_y: int, pad_w: int = 336) -> int:
    """Draw the saxoBot panel at an explicit position."""
    cur = plan["current"]
    rows = cur.get("rows") or []
    reason = str(cur.get("reason") or "")
    stale = str(cur.get("month", "")) != datetime.now().strftime("%Y-%m")

    pad_h = 74 + 20 * max(len(rows), 1) + (12 if reason else 0) + 10

    x0, x1 = left_x, left_x + pad_w
    y0 = top_y
    y1 = y0 + pad_h

    frosted_panel(img, (x0, y0, x1, y1), 16)
    d.rounded_rectangle([x0, y0, x1, y1], radius=16,
                        outline=(255, 255, 255, 28), width=1)

    f_label = ImageFont.truetype(FONT_SEMI, 9)
    f_month = ImageFont.truetype(FONT_BOLD, 18)
    f_row = ImageFont.truetype(FONT_SEMI, 11)
    f_val = ImageFont.truetype(FONT_BOLD, 11)
    f_act = ImageFont.truetype(FONT_SEMI, 10)
    f_stamp = ImageFont.truetype(FONT_REG, 9)

    px = x0 + 20
    py = y0 + 17

    d.ellipse([px, py + 2, px + 5, py + 7],
              fill=(245, 158, 11) if stale else PURPLE)
    draw_tracked(d, (px + 11, py - 1), "SAXO PLAN", f_label, (190, 200, 220),
                 tracking=2.0)
    tag = "STALE" if stale else str(cur.get("asof", ""))[-5:]
    d.text((x1 - 20 - d.textlength(tag, font=f_stamp), py), tag,
           font=f_stamp, fill=(245, 158, 11) if stale else (150, 162, 184))

    py += 15
    label = str(cur.get("label") or cur.get("month") or "")
    d.text((px - 1, py), label, font=f_month, fill=(255, 255, 255))

    py += 24
    if reason:
        d.text((px, py), fit_text(d, reason, f_stamp, pad_w - 40),
               font=f_stamp, fill=(128, 141, 161))
        py += 13
    py += 4
    d.line([(px, py), (x1 - 20, py)], fill=(255, 255, 255, 24), width=1)
    py += 9

    if rows:
        for r in rows:
            name = f"{r['ticker']} {r.get('name', '')}"
            right = f"{int(r['shares'])} sh · {float(r['price']):.2f}"
            right_w = d.textlength(right, font=f_val)
            d.text((px, py - 1), fit_text(d, name, f_row, pad_w - 40 - right_w - 10),
                   font=f_row, fill=(186, 196, 214))
            d.text((x1 - 20 - right_w, py - 1), right, font=f_val,
                   fill=(230, 236, 246))
            py += 20
    else:
        d.text((px, py - 1), "HOLD CASH", font=f_val, fill=(255, 255, 255))
        py += 20


def read_positions() -> list[dict]:
    """Manual positions from positions.json (outside the monthly rotation)."""
    try:
        data = json.loads(POSITIONS.read_text(encoding="utf-8"))
    except Exception:                                  # noqa: BLE001
        return []
    out: list[dict] = []
    for p in data if isinstance(data, list) else []:
        try:
            out.append({"ticker": str(p["ticker"]),
                        "name": str(p.get("name", p["ticker"])),
                        "shares": float(p["shares"]),
                        "entry": float(p["entry"]),
                        "cost": float(p["cost"]) if p.get("cost") else None,
                        "ccy": str(p.get("ccy", "EUR")).upper(),
                        "note": str(p.get("note", ""))})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def last_prices(tickers: list[str]) -> dict[str, float]:
    """Latest close for each ticker; {} if the data source is unavailable."""
    if not tickers:
        return {}
    try:
        import pandas as pd
        import yfinance as yf

        d = yf.download(tickers, period="5d", progress=False,
                        auto_adjust=True)["Close"]
        if isinstance(d, pd.Series):
            d = d.to_frame(tickers[0])
        d = d.ffill()
        out = {}
        for t in tickers:
            if t in d.columns and d[t].notna().any():
                out[t] = float(d[t].dropna().iloc[-1])
        return out
    except Exception:                                  # noqa: BLE001
        return {}


def usd_per_eur() -> float:
    """USD per EUR, for positions quoted in dollars. 1.16 if unavailable."""
    try:
        import yfinance as yf

        h = yf.download("EURUSD=X", period="5d", progress=False,
                        auto_adjust=True)["Close"].dropna()
        return float(h.iloc[-1].iloc[0] if hasattr(h.iloc[-1], "iloc")
                     else h.iloc[-1])
    except Exception:                                  # noqa: BLE001
        return 1.16


def render_positions(img: Image.Image, d: ImageDraw.ImageDraw,
                     positions: list[dict], prices: dict[str, float],
                     right_x: int, top_y: int, pad_w: int = 336,
                     fx: float = 1.16) -> int:
    """Holdings with live P&L, stacked under the balance card."""
    row_h = 36
    notes = [p["note"] for p in positions if p["note"]]
    pad_h = 58 + row_h * len(positions) + (13 if notes else 0)
    x1, y0 = right_x, top_y
    x0 = x1 - pad_w
    y1 = y0 + pad_h

    frosted_panel(img, (x0, y0, x1, y1), 16)
    d.rounded_rectangle([x0, y0, x1, y1], radius=16,
                        outline=(255, 255, 255, 28), width=1)

    f_label = ImageFont.truetype(FONT_SEMI, 9)
    f_row = ImageFont.truetype(FONT_SEMI, 12)
    f_val = ImageFont.truetype(FONT_BOLD, 12)
    f_small = ImageFont.truetype(FONT_REG, 9)
    f_pct = ImageFont.truetype(FONT_BOLD, 10)

    px, py = x0 + 20, y0 + 16
    d.ellipse([px, py + 2, px + 5, py + 7], fill=(245, 158, 11))
    draw_tracked(d, (px + 11, py - 1), "POSITIONS", f_label, (190, 200, 220),
                 tracking=2.0)
    stamp = datetime.now().strftime("%d %b %H:%M")
    d.text((x1 - 20 - d.textlength(stamp, font=f_small), py), stamp,
           font=f_small, fill=(150, 162, 184))

    py += 19
    for p in positions:
        price = prices.get(p["ticker"])
        usd = p.get("ccy") == "USD"
        value = (price * p["shares"] / fx if usd else price * p["shares"]) \
            if price else None
        basis = p.get("cost") or (p["entry"] * p["shares"] if p["entry"] else None)
        pct = (value / basis - 1) if value and basis else None
        fill = GREEN if (pct or 0) >= 0 else (239, 107, 107)

        name = f"{p['ticker'].split('.')[0]} {p['name']}"
        d.text((px, py - 1), fit_text(d, name, f_row, pad_w - 40 - 70),
               font=f_row, fill=(230, 236, 246))
        if value is not None:
            txt = f"€{value:,.0f}"
            d.text((x1 - 20 - d.textlength(txt, font=f_val), py - 2), txt,
                   font=f_val, fill=(255, 255, 255))

        py += 16
        sign = "$" if usd else ""
        left = (f"{p['shares']:.0f} sh · {sign}{price:.2f}" if price
                else f"{p['shares']:.0f} sh · no quote")
        d.text((px, py), left, font=f_small, fill=(140, 152, 174))
        if pct is not None:
            txt = f"{pct:+.1%}"
            d.text((x1 - 20 - d.textlength(txt, font=f_pct), py - 1), txt,
                   font=f_pct, fill=fill)
        py += row_h - 16

    if notes:
        d.text((px, py), fit_text(d, " · ".join(notes), f_small, pad_w - 40),
               font=f_small, fill=(128, 141, 161))
    return y1


def read_metrics() -> dict:
    try:
        data = json.loads(METRICS.read_text(encoding="utf-8"))
    except Exception:                                  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def render_thesis(img: Image.Image, d: ImageDraw.ImageDraw, m: dict,
                  right_x: int, top_y: int, pad_w: int = 336) -> int:
    """The three numbers that keep or kill the Basler thesis."""
    if not m:
        return top_y
    lim = m.get("limits") or {}
    order_min = float(lim.get("order_growth_min", 20.0))
    margin_min = float(lim.get("gross_margin_min", 46.0))

    rows = []
    if m.get("order_growth") is not None:
        v = float(m["order_growth"])
        rows.append((f"Order intake {v:+.0f}%", v >= order_min))
    if m.get("gross_margin") is not None:
        v = float(m["gross_margin"])
        rows.append((f"Gross margin  {v:.0f}%", v >= margin_min))
    if m.get("ebit_low") is not None and m.get("ebit_high") is not None:
        rows.append((f"EBIT guide {float(m['ebit_low']):.1f}-"
                     f"{float(m['ebit_high']):.1f}%",
                     m.get("guidance") != "cut"))
    if not rows:
        return top_y

    pad_h = 56 + 19 * len(rows) + 13 + (12 if m.get("next_report") else 0)
    x1, y0 = right_x, top_y
    x0 = x1 - pad_w
    y1 = y0 + pad_h

    frosted_panel(img, (x0, y0, x1, y1), 16)
    d.rounded_rectangle([x0, y0, x1, y1], radius=16,
                        outline=(255, 255, 255, 28), width=1)

    f_label = ImageFont.truetype(FONT_SEMI, 9)
    f_row = ImageFont.truetype(FONT_SEMI, 11)
    f_small = ImageFont.truetype(FONT_REG, 9)

    px, py = x0 + 20, y0 + 15
    d.ellipse([px, py + 2, px + 5, py + 7], fill=(155, 140, 255))
    draw_tracked(d, (px + 11, py - 1), "BSL THESIS", f_label, (190, 200, 220),
                 tracking=2.0)
    asof = str(m.get("asof") or "")[:10]
    try:
        asof = datetime.strptime(asof, "%Y-%m-%d").strftime("%d %b")
    except ValueError:
        pass
    if asof:
        d.text((x1 - 20 - d.textlength(asof, font=f_small), py), asof,
               font=f_small, fill=(150, 162, 184))

    py += 18
    for text, ok in rows:
        d.ellipse([px + 1, py + 3, px + 7, py + 9],
                  fill=GREEN if ok else (239, 107, 107))
        d.text((px + 13, py - 1), text, font=f_row, fill=(200, 209, 224))
        py += 19

    d.text((px, py), "exit: orders <+20% · margin <46% · cut", font=f_small,
           fill=(110, 122, 142))

    nxt = str(m.get("next_report") or "")[:10]
    if nxt:
        due = date.today().isoformat() > nxt and str(m.get("asof") or "") < nxt
        try:
            label = datetime.strptime(nxt, "%Y-%m-%d").strftime("%d %b")
        except ValueError:
            label = nxt
        py += 13
        d.text((px, py),
               ("REPORT OUT - update numbers" if due else f"next report {label}"),
               font=f_small, fill=(245, 158, 11) if due else (110, 122, 142))
    return y1


def read_watchlist() -> tuple[dict, list[dict]]:
    try:
        data = json.loads(WATCHLIST.read_text(encoding="utf-8"))
    except Exception:                                  # noqa: BLE001
        return {}, []
    rules = data.get("rules") or {}
    if not rules:                          # older schema
        rules = {"drawdown_min": float(data.get("drawdown_trigger", 20.0))}
    out = []
    for n in data.get("names", []):
        try:
            out.append({"ticker": str(n["ticker"]),
                        "name": str(n.get("name", n["ticker"])),
                        "pe": n.get("pe")})
        except (KeyError, TypeError):
            continue
    return rules, out


def watchlist_stats(names: list[dict]) -> list[dict]:
    """price, 52-week high and the drawdown trigger for each name."""
    if not names:
        return []
    tickers = [n["ticker"] for n in names]
    try:
        import pandas as pd
        import yfinance as yf

        d = yf.download(tickers, period="1y", progress=False,
                        auto_adjust=True)["Close"]
        if isinstance(d, pd.Series):
            d = d.to_frame(tickers[0])
        d = d.ffill()
    except Exception:                                  # noqa: BLE001
        return []

    out = []
    for n in names:
        t = n["ticker"]
        if t not in d.columns or not d[t].notna().any():
            continue
        s = d[t].dropna()
        out.append({**n, "price": float(s.iloc[-1]),
                    "high": float(s.max()),
                    "low": float(s.min())})
    return out


def render_watchlist(img: Image.Image, d: ImageDraw.ImageDraw,
                     rows: list[dict], rules: dict, right_x: int,
                     top_y: int, pad_w: int = 336) -> int:
    """Quality names, their forward P/E, and how far they are from a buy."""
    if not rows:
        return top_y
    dd_min = float(rules.get("drawdown_min", 20.0))
    pe_max = rules.get("pe_max")

    pad_h = 56 + 18 * len(rows)
    x1, y0 = right_x, top_y
    x0 = x1 - pad_w
    y1 = y0 + pad_h

    frosted_panel(img, (x0, y0, x1, y1), 16)
    d.rounded_rectangle([x0, y0, x1, y1], radius=16,
                        outline=(255, 255, 255, 28), width=1)

    f_label = ImageFont.truetype(FONT_SEMI, 9)
    f_row = ImageFont.truetype(FONT_SEMI, 11)
    f_val = ImageFont.truetype(FONT_BOLD, 10)
    f_small = ImageFont.truetype(FONT_REG, 9)

    px, py = x0 + 20, y0 + 15
    d.ellipse([px, py + 2, px + 5, py + 7], fill=(56, 189, 248))
    draw_tracked(d, (px + 11, py - 1), "WATCHLIST", f_label, (190, 200, 220),
                 tracking=2.0)
    tag = (f"P/E<{pe_max:.0f} · bottoming" if pe_max else "bottoming")
    d.text((x1 - 20 - d.textlength(tag, font=f_small), py), tag,
           font=f_small, fill=(150, 162, 184))

    py += 18
    for r in rows:
        dd = (r["price"] / r["high"] - 1) * 100
        pe = r.get("pe")
        score = int(r.get("score", 0))

        name = f"{r['ticker'].split('.')[0]} {r['name']}"
        d.text((px, py), fit_text(d, name, f_row, pad_w - 150),
               font=f_row, fill=(200, 209, 224))

        d.text((x1 - 128, py), f"{pe:.0f}x" if pe else "-", font=f_val,
               fill=(180, 190, 210))
        d.text((x1 - 95, py), f"{dd:+.0f}%", font=f_val,
               fill=GREEN if dd > -10 else (245, 158, 11) if dd > -30
               else (239, 107, 107))

        dots = "".join("\u25cf" if i < score else "\u25cb" for i in range(4))
        colour = GREEN if score == 4 else ((245, 158, 11) if score == 3
                                           else (120, 132, 152))
        d.text((x1 - 20 - d.textlength(dots, font=f_val), py), dots,
               font=f_val, fill=colour)
        py += 18

    return y1


QUESTIONS = [
    "How does it make money?",
    "What does it earn in a normal year?",
    "Is growth real, or a base effect?",
    "Are margins rising or falling?",
    "How much debt, and when is it due?",
    "Is the share count growing?",
    "Multiple vs its own history?",
    "Cheap for a temporary or permanent reason?",
    "What would prove me wrong?",
    "At what price do I sell?",
]


def render_checklist(img: Image.Image, d: ImageDraw.ImageDraw,
                     right_x: int, top_y: int) -> int:
    """The ten questions, beside the monthly plan."""
    pad_w, row_h = 430, 17
    pad_h = 40 + row_h * len(QUESTIONS) + 18
    x1 = right_x
    x0 = x1 - pad_w
    y0 = top_y
    y1 = y0 + pad_h

    frosted_panel(img, (x0, y0, x1, y1), 16)
    d.rounded_rectangle([x0, y0, x1, y1], radius=16,
                        outline=(255, 255, 255, 28), width=1)

    f_label = ImageFont.truetype(FONT_SEMI, 9)
    f_row = ImageFont.truetype(FONT_SEMI, 11)
    f_num = ImageFont.truetype(FONT_BOLD, 9)
    f_small = ImageFont.truetype(FONT_REG, 9)

    px, py = x0 + 20, y0 + 15
    d.ellipse([px, py + 2, px + 5, py + 7], fill=(155, 140, 255))
    draw_tracked(d, (px + 11, py - 1), "TEN QUESTIONS", f_label,
                 (190, 200, 220), tracking=2.0)

    py += 18
    for i, q in enumerate(QUESTIONS, 1):
        d.text((px, py), f"{i:>2d}", font=f_num, fill=(110, 122, 142))
        d.text((px + 20, py - 1), q, font=f_row, fill=(200, 209, 224))
        py += row_h

    d.text((px, py + 1), "any \u2018don't know\u2019 \u2192 too-hard pile",
           font=f_small, fill=(110, 122, 142))
    return y1


def read_crypto() -> list[dict]:
    try:
        data = json.loads(CRYPTO.read_text(encoding="utf-8"))
    except Exception:                                  # noqa: BLE001
        return []
    return [c for c in data.get("coins", []) if c.get("score", 0) >= 2][:10]


def render_crypto(img: Image.Image, d: ImageDraw.ImageDraw, coins: list[dict],
                  left_x: int, top_y: int, pad_w: int = 430) -> int:
    """Coins closest to a bottom: deep, near the low, turning, basing."""
    if not coins:
        return top_y
    pad_h = 56 + 18 * len(coins)
    x0, y0 = left_x, top_y
    x1 = x0 + pad_w
    y1 = y0 + pad_h

    frosted_panel(img, (x0, y0, x1, y1), 16)
    d.rounded_rectangle([x0, y0, x1, y1], radius=16,
                        outline=(255, 255, 255, 28), width=1)

    f_label = ImageFont.truetype(FONT_SEMI, 9)
    f_row = ImageFont.truetype(FONT_SEMI, 11)
    f_val = ImageFont.truetype(FONT_BOLD, 10)
    f_small = ImageFont.truetype(FONT_REG, 9)

    px, py = x0 + 20, y0 + 15
    d.ellipse([px, py + 2, px + 5, py + 7], fill=(245, 158, 11))
    draw_tracked(d, (px + 11, py - 1), "CRYPTO BOTTOMING", f_label,
                 (190, 200, 220), tracking=2.0)
    tag = "deep · near low · turning · basing"
    d.text((x1 - 20 - d.textlength(tag, font=f_small), py), tag,
           font=f_small, fill=(150, 162, 184))

    py += 18
    for c in coins:
        score = int(c.get("score", 0))
        name = str(c.get("sym", ""))[:6]
        d.text((px, py), name, font=f_row, fill=(210, 219, 234))
        d.text((px + 62, py), f"{c.get('from_low', 0):+.0f}% low",
               font=f_small, fill=(140, 152, 174))
        r30 = c.get("r30", 0)
        d.text((px + 150, py), f"{r30:+.0f}% 30d", font=f_small,
               fill=GREEN if r30 > 0 else (239, 107, 107))

        dots = ""
        for i in range(4):
            dots += "\u25cf" if i < score else "\u25cb"
        colour = GREEN if score == 4 else ((245, 158, 11) if score == 3
                                           else (110, 122, 142))
        d.text((x1 - 20 - d.textlength(dots, font=f_val), py - 1), dots,
               font=f_val, fill=colour)
        py += 18

    return y1


def read_etfs() -> list[dict]:
    try:
        data = json.loads(ETFS.read_text(encoding="utf-8"))
    except Exception:                                  # noqa: BLE001
        return []
    return data.get("etfs", [])[:7]


def render_etfs(img: Image.Image, d: ImageDraw.ImageDraw, rows: list[dict],
                left_x: int, top_y: int, pad_w: int = 430) -> int:
    """Diversifier candidates: low correlation first."""
    if not rows:
        return top_y
    pad_h = 56 + 18 * len(rows)
    x0, y0 = left_x, top_y
    x1 = x0 + pad_w
    y1 = y0 + pad_h

    frosted_panel(img, (x0, y0, x1, y1), 16)
    d.rounded_rectangle([x0, y0, x1, y1], radius=16,
                        outline=(255, 255, 255, 28), width=1)

    f_label = ImageFont.truetype(FONT_SEMI, 9)
    f_row = ImageFont.truetype(FONT_SEMI, 11)
    f_val = ImageFont.truetype(FONT_BOLD, 10)
    f_small = ImageFont.truetype(FONT_REG, 9)

    px, py = x0 + 20, y0 + 15
    d.ellipse([px, py + 2, px + 5, py + 7], fill=(56, 189, 248))
    draw_tracked(d, (px + 11, py - 1), "ETF DIVERSIFIERS", f_label,
                 (190, 200, 220), tracking=2.0)
    tag = "price · 3y dd · corr · TER"
    d.text((x1 - 20 - d.textlength(tag, font=f_small), py), tag,
           font=f_small, fill=(150, 162, 184))

    py += 18
    for r in rows:
        d.text((px, py), fit_text(d, str(r["name"]), f_row, pad_w - 200),
               font=f_row, fill=(200, 209, 224))
        d.text((x1 - 175, py), f"{float(r['price']):.2f}", font=f_val,
               fill=(180, 190, 210))
        d.text((x1 - 128, py), f"{float(r['dd3y']):+.0f}%", font=f_val,
               fill=(150, 162, 184))
        corr = float(r["corr"])
        d.text((x1 - 84, py), f"{corr:.2f}", font=f_val,
               fill=GREEN if corr < 0.2 else (245, 158, 11) if corr < 0.4
               else (239, 107, 107))
        ter = r.get("ter")
        d.text((x1 - 20 - d.textlength(f"{ter:.2f}%" if ter else "-",
                                       font=f_val), py),
               f"{ter:.2f}%" if ter else "-", font=f_val,
               fill=(150, 162, 184))
        py += 18

    return y1


def render(data: dict, bg_path: Path | None = None, corner: str = "tr",
           hidden: bool = False, plan: dict | None = None,
           positions: list[dict] | None = None,
           prices: dict[str, float] | None = None,
           metrics: dict | None = None,
           fx: float = 1.16,
           watch: list[dict] | None = None,
           watch_rules: dict | None = None,
           crypto: list[dict] | None = None,
           etfs: list[dict] | None = None) -> Image.Image:
    rows = build_rows(data, hidden)
    pad_w = 336
    pad_h = 88 + 20 * len(rows)

    img = background(bg_path, corner)
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    x0, y0, x1, y1 = panel_box(corner, pad_w, pad_h)

    frosted_panel(img, (x0, y0, x1, y1), 16)
    d.rounded_rectangle([x0, y0, x1, y1], radius=16,
                        outline=(255, 255, 255, 28), width=1)

    f_label = ImageFont.truetype(FONT_SEMI, 9)
    f_total = ImageFont.truetype(FONT_BOLD, 26)
    f_row = ImageFont.truetype(FONT_SEMI, 12)
    f_row_val = ImageFont.truetype(FONT_BOLD, 12)
    f_stamp = ImageFont.truetype(FONT_REG, 9)

    px = x0 + 20
    py = y0 + 17

    d.ellipse([px, py + 2, px + 5, py + 7], fill=GREEN)
    draw_tracked(d, (px + 11, py - 1), "BALANCE", f_label, (190, 200, 220), tracking=2.0)

    stamp = datetime.fromtimestamp(data.get("ts", time.time())).strftime("%d %b %H:%M")
    d.text((x1 - 20 - d.textlength(stamp, font=f_stamp), py), stamp, font=f_stamp,
           fill=(150, 162, 184))

    py += 15
    total = "••••••" if hidden else money(data.get("total_usd"))
    d.text((px - 1, py), total, font=f_total, fill=(255, 255, 255))

    py += 38
    d.line([(px, py), (x1 - 20, py)], fill=(255, 255, 255, 24), width=1)
    py += 9

    for row in rows:
        if row["indent"]:
            d.ellipse([px + 7, py + 5, px + 10, py + 8], fill=row["label_fill"])
        else:
            d.ellipse([px, py + 4, px + 4, py + 8], fill=row["color"])

        value = row["value"]
        val_w = d.textlength(value, font=f_row_val) if value else 0
        label = fit_text(d, row["label"], f_row, pad_w - 40 - row["indent"] - val_w - 12)
        d.text((px + 11 + row["indent"], py - 1), label, font=f_row, fill=row["label_fill"])

        if value:
            d.text((x1 - 20 - val_w, py - 1), value, font=f_row_val, fill=row["value_fill"])
        py += 20

    if positions:
        bottom = render_positions(img, d, positions, prices or {}, x1, y1 + 16,
                                  fx=fx)
    else:
        bottom = y1 + 16
    thesis_bottom = render_thesis(img, d, metrics or {}, x1, bottom + 16)
    watch_bottom = thesis_bottom
    if watch:
        watch_bottom = render_watchlist(img, d, watch, watch_rules or {}, x1,
                                        thesis_bottom + 16)
    if plan:
        render_plan(img, d, plan, x1 - 336, watch_bottom + 16, pad_w=336)

    check_x1 = x0 - 40
    check_bottom = render_checklist(img, d, check_x1, y0)
    crypto_bottom = check_bottom + 16
    if crypto:
        crypto_bottom = render_crypto(img, d, crypto, check_x1 - 430,
                                      crypto_bottom)
    if etfs:
        render_etfs(img, d, etfs, check_x1 - 430, crypto_bottom + 16)

    return Image.alpha_composite(img, overlay).convert("RGB")


def set_wallpaper(path: Path) -> None:
    ok = ctypes.windll.user32.SystemParametersInfoW(
        SPI_SETDESKWALLPAPER, 0, str(path), SPIF_UPDATEINIFILE | SPIF_SENDCHANGE)
    if not ok:
        raise ctypes.WinError()


def read_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_state(digest: str, total: float, hidden: bool) -> None:
    STATE.write_text(json.dumps({"digest": digest, "total": total, "hidden": hidden,
                                 "at": time.time()}), encoding="utf-8")


def update(apply: bool, force: bool, bg_path: Path | None = None, corner: str = "tr",
           threshold: float = 0.25, max_age: int = 3600,
           hidden: bool | None = None, toggle: bool = False) -> str:
    state = read_state()
    if toggle:
        hidden = not bool(state.get("hidden"))
        force = True
    elif hidden is None:
        hidden = bool(state.get("hidden"))
    hidden = bool(hidden)

    data = server.build_portfolio()
    plan = read_plan()
    positions = read_positions()
    prices = last_prices([p["ticker"] for p in positions]) if positions else {}
    metrics = read_metrics()
    fx = usd_per_eur() if any(p.get("ccy") == "USD" for p in positions) else 1.16
    watch_rules, watch_names = read_watchlist()
    watch = watchlist_stats(watch_names)
    crypto = read_crypto()
    etfs = read_etfs()
    total = float(data.get("total_usd") or 0)
    summary = f"{money(total)} | " + " ".join(
        f"{s.get('label')}={money(s.get('total_usd'))}" for s in data.get("sources", []))

    if apply and not force:
        failed = [s.get("label") for s in data.get("sources", []) if s.get("error")]
        if total <= 0:
            return summary + "  [skipped: no data, kept previous wallpaper]"
        if failed and state.get("digest"):
            return summary + f"  [skipped: {', '.join(failed)} unavailable, kept previous]"

    img = render(data, bg_path, corner, hidden, plan, positions, prices, metrics,
                 fx, watch, watch_rules, crypto, etfs)
    img.save(OUT, "PNG")

    bg_sig = f"{bg_path}:{bg_path.stat().st_mtime_ns}" if bg_path and bg_path.exists() else "none"
    plan_sig = ""
    if plan:
        c = plan.get("current") or {}
        plan_sig = f"|{c.get('month')}|{c.get('asof')}|{c.get('reason')}|" + ",".join(
            f"{r['ticker']}:{r['shares']}" for r in (c.get("rows") or []))
    pos_sig = "|".join(
        f"{p['ticker']}:{p['shares']:.0f}@{prices.get(p['ticker'], 0):.2f}"
        for p in positions)
    met_sig = "|".join(f"{k}={v}" for k, v in sorted((metrics or {}).items())
                       if not isinstance(v, dict))
    watch_sig = "|".join(f"{r['ticker']}@{r['price']:.2f}" for r in (watch or []))
    crypto_sig = "|".join(f"{c['sym']}:{c.get('score')}" for c in (crypto or [])[:4])
    digest = hashlib.sha256(
        f"{summary}|{corner}|{bg_sig}|{hidden}{plan_sig}{pos_sig}{met_sig}"
        f"{watch_sig}{crypto_sig}".encode()).hexdigest()

    previous_digest = state.get("digest")
    previous_total = state.get("total")
    age = time.time() - float(state.get("at") or 0)
    mode = "  [hidden]" if hidden else ""

    if not apply:
        return summary + "  [written, not applied]" + mode

    if force:
        reason = "forced"
    elif age > max_age:
        reason = f"refresh, {int(age // 60)}m old"
    elif previous_total and abs(total - float(previous_total)) < abs(float(previous_total)) * threshold / 100:
        return summary + f"  [skipped: moved <{threshold}%]" + mode
    elif digest != previous_digest:
        reason = "changed"
    else:
        return summary + "  [unchanged, skipped]" + mode

    set_wallpaper(OUT)
    write_state(digest, total, hidden)
    return summary + f"  [{reason}, applied]" + mode


def main() -> int:
    ap = argparse.ArgumentParser(description="Balance wallpaper")
    ap.add_argument("--once", action="store_true", help="render and apply once")
    ap.add_argument("--loop", type=int, metavar="SECONDS", help="repeat forever")
    ap.add_argument("--interval", type=int, default=0, help=argparse.SUPPRESS)
    ap.add_argument("--no-set", action="store_true", help="render only")
    ap.add_argument("--force", action="store_true", help="apply even if unchanged")
    ap.add_argument("--bg", metavar="IMAGE", help="background photo (default: assets/background.* or background.*)")
    ap.add_argument("--corner", choices=["tl", "tr", "bl", "br"], default="tr",
                    help="where the card sits (default tr = upper right)")
    ap.add_argument("--threshold", type=float, default=0.25,
                    help="only redraw when the total moves at least this %% (default 0.25)")
    ap.add_argument("--max-age", type=int, default=60, metavar="MINUTES",
                    help="force a redraw after this many minutes so the clock stays fresh (default 60)")
    ap.add_argument("--restore", action="store_true", help="clear saved state")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--hide", action="store_true", help="hide the numbers")
    mode.add_argument("--show", action="store_true", help="show the numbers")
    mode.add_argument("--toggle", action="store_true", help="flip hidden/shown and apply now")
    args = ap.parse_args()

    if args.restore:
        STATE.unlink(missing_ok=True)
        print("state cleared")
        return 0

    bg_path = find_background(args.bg)
    apply = not args.no_set
    hidden = True if args.hide else (False if args.show else None)
    print(f"background: {bg_path if bg_path else 'generated gradient'} | corner: {args.corner}")

    if args.loop:
        print(f"updating every {args.loop}s, Ctrl+C to stop")
        try:
            started = os.path.getmtime(__file__)
            while True:
                if os.path.getmtime(__file__) != started:
                    print("wallpaper.py changed - restarting with the new code")
                    os.execv(sys.executable, [sys.executable] + sys.argv)
                try:
                    print(time.strftime("%H:%M:%S"),
                          update(apply, False, bg_path, args.corner, args.threshold,
                                 args.max_age * 60, hidden, args.toggle))
                except Exception as exc:
                    print(time.strftime("%H:%M:%S"), "error:", exc)
                time.sleep(max(30, args.loop))
        except KeyboardInterrupt:
            print("\nstopped")
        return 0

    print(update(apply, args.force, bg_path, args.corner, args.threshold,
                 args.max_age * 60, hidden, args.toggle))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
