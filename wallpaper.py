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
from datetime import datetime
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
                balance_box: tuple[int, int, int, int],
                on_left: bool = True) -> None:
    """Draw the saxoBot panel beside the balance panel."""
    cur = plan["current"]
    rows = cur.get("rows") or []
    stale = str(cur.get("month", "")) != datetime.now().strftime("%Y-%m")

    pad_w = 300
    pad_h = 74 + 20 * max(len(rows), 1) + 10

    bx0, by0, _bx1, _by1 = balance_box
    gap = 18
    if on_left:
        x1 = bx0 - gap
        x0 = x1 - pad_w
    else:
        x0 = _bx1 + gap
        x1 = x0 + pad_w
    y0 = by0
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

    py += 30
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


def render(data: dict, bg_path: Path | None = None, corner: str = "tr",
           hidden: bool = False, plan: dict | None = None) -> Image.Image:
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

    if plan:
        render_plan(img, d, plan, (x0, y0, x1, y1), on_left=corner.endswith("r"))

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
    total = float(data.get("total_usd") or 0)
    summary = f"{money(total)} | " + " ".join(
        f"{s.get('label')}={money(s.get('total_usd'))}" for s in data.get("sources", []))

    if apply and not force:
        failed = [s.get("label") for s in data.get("sources", []) if s.get("error")]
        if total <= 0:
            return summary + "  [skipped: no data, kept previous wallpaper]"
        if failed and state.get("digest"):
            return summary + f"  [skipped: {', '.join(failed)} unavailable, kept previous]"

    img = render(data, bg_path, corner, hidden, plan)
    img.save(OUT, "PNG")

    bg_sig = f"{bg_path}:{bg_path.stat().st_mtime_ns}" if bg_path and bg_path.exists() else "none"
    plan_sig = ""
    if plan:
        c = plan.get("current") or {}
        plan_sig = f"|{c.get('month')}|{c.get('asof')}|" + ",".join(
            f"{r['ticker']}:{r['shares']}" for r in (c.get("rows") or []))
    digest = hashlib.sha256(
        f"{summary}|{corner}|{bg_sig}|{hidden}{plan_sig}".encode()).hexdigest()

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
            while True:
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
