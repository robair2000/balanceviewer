#!/usr/bin/env python3
"""BalanceViewer: KuCoin spot balances + Ethereum (MetaMask) wallet.

Standard library only - no pip install, no build step.

    python server.py            -> http://127.0.0.1:8787

Secrets stay on this machine: the browser only ever receives already-valued
JSON, never the API key/secret. Bind address defaults to 127.0.0.1.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
USER_AGENT = "balanceviewer/1.0 (+local)"

KUCOIN_BASE = "https://api.kucoin.com"
DEFAULT_RPCS = [
    "https://eth.drpc.org",
    "https://eth-mainnet.public.blastapi.io",
    "https://1rpc.io/eth",
    "https://rpc.flashbots.net",
    "https://ethereum-rpc.publicnode.com",
]

# ---------------------------------------------------------------- config ----


def load_env_file(path: Path, only: set[str] | None = None) -> None:
    """Tiny .env reader. Real environment variables always win."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        if not key or (only is not None and key not in only):
            continue
        if key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def bootstrap_env() -> None:
    load_env_file(APP_DIR / ".env")
    # Convenience: reuse the KuCoin keys already sitting in ../repo/.env
    shared = {"KUCOIN_API_KEY", "KUCOIN_API_SECRET", "KUCOIN_PASSPHRASE"}
    load_env_file(APP_DIR.parent / "repo" / ".env", only=shared)


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


class ConfigError(RuntimeError):
    """Missing or invalid user configuration - shown as a card message."""


# ------------------------------------------------------------- transport ----


def http_json(url: str, method: str = "GET", body: bytes | str | None = None,
              headers: dict | None = None, timeout: float = 20.0):
    if isinstance(body, str):
        body = body.encode()
    req = Request(url, data=body, method=method)
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def describe(exc: BaseException) -> str:
    """Short, non-sensitive error text for the UI."""
    if isinstance(exc, ConfigError):
        return str(exc)
    if isinstance(exc, HTTPError):
        try:
            snippet = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            snippet = ""
        return f"HTTP {exc.code} from remote API{': ' + snippet if snippet else ''}"
    if isinstance(exc, URLError):
        return f"Network error: {exc.reason}"
    return f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------- prices ----


class PriceBook:
    """USD prices, KuCoin tickers first, CoinGecko as a fallback."""

    STABLES = ("USDC", "DAI", "TUSD", "FDUSD", "PYUSD", "USDE")
    CG_IDS = {
        "ETH": "ethereum", "WBTC": "wrapped-bitcoin", "LINK": "chainlink",
        "UNI": "uniswap", "AAVE": "aave", "SHIB": "shiba-inu", "PEPE": "pepe",
        "MKR": "maker", "CRV": "curve-dao-token", "GRT": "the-graph",
        "ENS": "ethereum-name-service", "LDO": "lido-dao", "ONDO": "ondo-finance",
        "WLD": "worldcoin-wld", "ARB": "arbitrum", "FET": "fetch-ai",
        "INJ": "injective-protocol", "RENDER": "render-token",
        "WSTETH": "wrapped-steth", "POL": "polygon-ecosystem-token",
        "MATIC": "matic-network", "USDC": "usd-coin", "USDT": "tether",
        "DAI": "dai", "KCS": "kucoin-shares", "BTC": "bitcoin",
    }
    # Assets that trade within a fraction of a percent of another asset but
    # aren't listed on CoinGecko's simple/price endpoint.
    ALIASES = {"STETH": "ETH"}

    def __init__(self) -> None:
        self._tickers: dict[str, float] = {}
        self._tickers_ts = 0.0
        self._cg: dict[str, float] = {}
        self._cg_ts = 0.0
        self._llama: dict[str, float] = {}
        self._llama_ts = 0.0
        self._lock = threading.Lock()

    def tickers(self) -> dict[str, float]:
        with self._lock:
            if self._tickers and time.time() - self._tickers_ts < 60:
                return self._tickers
            try:
                out = http_json(f"{KUCOIN_BASE}/api/v1/market/allTickers")
                if out.get("code") != "200000":
                    raise RuntimeError(f"KuCoin ticker error: {out.get('msg') or out.get('code')}")
                book: dict[str, float] = {}
                for t in out["data"]["ticker"]:
                    try:
                        px = float(t.get("last") or 0) or float(t.get("buy") or 0)
                    except (TypeError, ValueError):
                        px = 0.0
                    if px > 0:
                        book[t["symbol"]] = px
                if book:
                    self._tickers, self._tickers_ts = book, time.time()
            except Exception:
                if not self._tickers:
                    raise
            return self._tickers

    def _coingecko(self, symbols: set[str]) -> dict[str, float]:
        with self._lock:
            fresh = self._cg and time.time() - self._cg_ts < 300
            if fresh and symbols <= set(self._cg):
                return {s: self._cg[s] for s in symbols}
            ids = {s: self.CG_IDS[s] for s in symbols if s in self.CG_IDS}
            missing = {i for s, i in ids.items() if s not in self._cg}
            if not missing:
                return {s: self._cg[s] for s in symbols if s in self._cg}
            try:
                url = ("https://api.coingecko.com/api/v3/simple/price?ids="
                       + ",".join(sorted(missing)) + "&vs_currencies=usd")
                out = http_json(url, timeout=15)
                for sym, cid in ids.items():
                    px = (out.get(cid) or {}).get("usd")
                    if px:
                        self._cg[sym] = float(px)
                self._cg_ts = time.time()
            except Exception:
                pass
            return {s: self._cg[s] for s in symbols if s in self._cg}

    def _llama_prices(self, symbols: set[str]) -> dict[str, float]:
        """DefiLlama: contract-accurate prices, no API key, good rate limits."""
        with self._lock:
            if self._llama and time.time() - self._llama_ts < 300 and symbols <= set(self._llama):
                return {s: self._llama[s] for s in symbols}
            ids: dict[str, str] = {}
            for sym in symbols:
                if sym == "ETH":
                    ids[sym] = "coingecko:ethereum"
                elif sym in ETH_TOKENS:
                    ids[sym] = "ethereum:" + ETH_TOKENS[sym][0]
            if not ids:
                return {}
            try:
                out = http_json("https://coins.llama.fi/prices/current/" + ",".join(ids.values()),
                                timeout=15)
                coins = out.get("coins") or {}
                for sym, key in ids.items():
                    px = (coins.get(key) or {}).get("price")
                    if px:
                        self._llama[sym] = float(px)
                self._llama_ts = time.time()
            except Exception:
                pass
            return {s: self._llama[s] for s in symbols if s in self._llama}

    def usd(self, symbol: str) -> float | None:
        sym = symbol.upper()
        tk = self.tickers()
        if sym == "USDT":
            return 1.0
        if sym in self.STABLES:
            return tk.get(f"{sym}-USDT", 1.0)
        direct = tk.get(f"{sym}-USDT")
        if direct:
            return direct
        usdc = tk.get(f"{sym}-USDC")
        if usdc:
            return usdc * tk.get("USDC-USDT", 1.0)
        for quote in ("BTC", "ETH", "KCS"):
            pair = tk.get(f"{sym}-{quote}")
            if pair:
                base = tk.get(f"{quote}-USDT")
                if base:
                    return pair * base
        px = self._llama_prices({sym}).get(sym)
        if px:
            return px
        px = self._coingecko({sym}).get(sym)
        if px:
            return px
        if sym in self.ALIASES:
            return self.usd(self.ALIASES[sym])
        return None

    def value(self, symbol: str, amount: float) -> tuple[float | None, float | None]:
        px = self.usd(symbol)
        if px is None:
            return None, None
        return px, px * amount

    def warm(self, symbols) -> None:
        """Batch the CoinGecko fallback into one request for the symbols KuCoin can't price."""
        try:
            tk = self.tickers()
        except Exception:
            tk = {}
        missing = set()
        for symbol in symbols:
            sym = symbol.upper()
            if sym == "USDT" or sym in self.STABLES:
                continue
            if f"{sym}-USDT" in tk or f"{sym}-USDC" in tk:
                continue
            if any(f"{sym}-{q}" in tk and tk.get(f"{q}-USDT") for q in ("BTC", "ETH", "KCS")):
                continue
            missing.add(sym)
        if missing:
            still = missing - set(self._llama_prices(missing))
            if still:
                self._coingecko(still)


PRICES = PriceBook()

# --------------------------------------------------------------- KuCoin -----


def kucoin_headers(key: str, secret: str, passphrase: str, method: str, endpoint: str,
                   body: str = "") -> dict:
    ts = str(int(time.time() * 1000))
    sig = base64.b64encode(
        hmac.new(secret.encode(), (ts + method + endpoint + body).encode(), hashlib.sha256).digest()
    ).decode()
    pp = base64.b64encode(
        hmac.new(secret.encode(), passphrase.encode(), hashlib.sha256).digest()
    ).decode()
    return {
        "KC-API-KEY": key,
        "KC-API-SIGN": sig,
        "KC-API-TIMESTAMP": ts,
        "KC-API-PASSPHRASE": pp,
        "KC-API-KEY-VERSION": "2",
        "Content-Type": "application/json",
    }


def kucoin_get(endpoint: str):
    key = env("KUCOIN_API_KEY")
    secret = env("KUCOIN_API_SECRET")
    passphrase = env("KUCOIN_PASSPHRASE")
    if not (key and secret and passphrase):
        raise ConfigError("KuCoin keys missing - add KUCOIN_API_KEY / SECRET / PASSPHRASE to .env")
    base = env("KUCOIN_API_BASE", KUCOIN_BASE) or KUCOIN_BASE
    out = http_json(base + endpoint, headers=kucoin_headers(key, secret, passphrase, "GET", endpoint))
    if out.get("code") != "200000":
        msg = out.get("msg") or out.get("code")
        raise RuntimeError(f"KuCoin: {msg}")
    return out.get("data")


def kucoin_accounts(acct_type: str) -> list[dict]:
    rows: list[dict] = []
    for page in range(1, 6):
        data = kucoin_get(f"/api/v1/accounts?type={acct_type}&pageSize=100&currentPage={page}") or []
        rows.extend(data)
        if len(data) < 100:
            break
    return rows


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _uta_subaccount_names() -> dict[str, str]:
    """uid -> subName. Best effort; names are cosmetic."""
    names: dict[str, str] = {}
    try:
        for page in range(1, 6):
            data = kucoin_get(f"/api/ua/v2/user/sub-account-list?pageSize=50&currentPage={page}") or {}
            items = data.get("items") or []
            for item in items:
                uid = item.get("uid")
                if uid is not None:
                    names[str(uid)] = item.get("subName") or f"sub {str(uid)[-4:]}"
            if len(items) < 50:
                break
    except Exception:
        pass
    return names


def _uta_subaccounts(types: set[str]) -> list[dict]:
    """Unified-account mode: /api/ua/v2/sub-account/balance returns every sub-account."""
    names = _uta_subaccount_names()
    subs: list[dict] = []
    last_id, seen = 0, set()
    for _ in range(10):
        query = "?pageSize=50" + (f"&lastId={last_id}" if last_id else "")
        data = kucoin_get("/api/ua/v2/sub-account/balance" + query) or {}
        users = data.get("userList") or []
        for user in users:
            uid = str(user.get("uid") or "")
            amounts: dict[str, float] = {}
            for acct in user.get("accounts") or []:
                if (acct.get("accountType") or "").upper() not in types:
                    continue
                for cur in acct.get("currencies") or []:
                    code = cur.get("currency") or ""
                    if code:
                        amounts[code] = amounts.get(code, 0.0) + _num(cur.get("balance"))
            subs.append({"name": names.get(uid) or f"sub {uid[-4:]}", "uid": uid,
                         "amounts": amounts})
        nxt = data.get("lastId")
        if not users or nxt in (None, 0, last_id) or nxt in seen:
            break
        seen.add(last_id)
        last_id = nxt
    return subs


def _classic_subaccounts(include_funding: bool) -> list[dict]:
    """Classic mode: /api/v2/sub-accounts lists spot (and funding) balances per sub-account."""
    subs: list[dict] = []
    for page in range(1, 6):
        data = kucoin_get(f"/api/v2/sub-accounts?currentPage={page}&pageSize=50") or {}
        items = data.get("items") or []
        for item in items:
            groups = list(item.get("tradeAccounts") or [])
            if include_funding:
                groups += list(item.get("mainAccounts") or [])
            amounts: dict[str, float] = {}
            for acct in groups:
                code = acct.get("currency") or ""
                if code:
                    amounts[code] = amounts.get(code, 0.0) + _num(acct.get("balance"))
            uid = str(item.get("subUserId") or "")
            subs.append({"name": item.get("subName") or uid, "uid": uid, "amounts": amounts})
        if len(items) < 50:
            break
    return subs


def kucoin_subaccounts(include_funding: bool) -> tuple[list[dict], str]:
    """Try unified-account mode first, then classic. Returns (subaccounts, mode label)."""
    types = {t.strip().upper() for t in env("KUCOIN_SUB_ACCOUNT_TYPES", "SPOT,FUNDING").split(",")
             if t.strip()}
    if not include_funding:
        types.discard("FUNDING")

    errors: list[str] = []
    try:
        subs = _uta_subaccounts(types) if types else []
    except Exception as exc:
        subs = []
        errors.append(f"UTA: {describe(exc)}")
    if subs:
        return subs, "unified account"
    try:
        subs = _classic_subaccounts(include_funding)
    except Exception as exc:
        errors.append(f"classic: {describe(exc)}")
        subs = []
    if subs:
        return subs, "classic"
    if len(errors) == 2:
        raise RuntimeError("Sub-accounts not readable (" + "; ".join(errors) + ")")
    return [], ""


def kucoin_source() -> dict:
    spot = kucoin_accounts("trade")
    include_funding = env("KUCOIN_INCLUDE_FUNDING", "0") in ("1", "true", "yes")
    funding = kucoin_accounts("main") if include_funding else []

    amounts: dict[str, float] = {}
    for acct in spot:
        amounts[acct["currency"]] = amounts.get(acct["currency"], 0.0) + float(acct["balance"])
    for acct in funding:
        amounts[acct["currency"]] = amounts.get(acct["currency"], 0.0) + float(acct["balance"])

    holdings, notes = [], []
    PRICES.warm(amounts.keys())
    for cur, amt in amounts.items():
        if amt <= 0:
            continue
        px, val = PRICES.value(cur, amt)
        holdings.append({"symbol": cur, "amount": amt, "price_usd": px, "value_usd": val})
    holdings.sort(key=lambda h: (h["value_usd"] or 0, h["amount"]), reverse=True)
    master_total = sum(h["value_usd"] or 0 for h in holdings)

    if not include_funding:
        pending: dict[str, float] = {}
        for acct in kucoin_accounts("main"):
            bal = float(acct["balance"])
            if bal > 0:
                pending[acct["currency"]] = pending.get(acct["currency"], 0.0) + bal
        if pending:
            PRICES.warm(pending.keys())
            unseen = sum((PRICES.value(c, a)[1] or 0) for c, a in pending.items())
            notes.append(
                f"Funding wallet not included: ${unseen:,.2f} across {len(pending)} "
                "asset(s). Set KUCOIN_INCLUDE_FUNDING=1 to add it."
            )

    subaccounts: list[dict] = []
    sub_mode = ""
    if env("KUCOIN_INCLUDE_SUBACCOUNTS", "1") not in ("0", "false", "no"):
        try:
            raw_subs, sub_mode = kucoin_subaccounts(include_funding)
        except Exception as exc:
            raw_subs, sub_mode = [], ""
            notes.append("Sub-accounts: " + describe(exc))
        if raw_subs:
            PRICES.warm({cur for sub in raw_subs for cur in sub["amounts"]})
            for sub in raw_subs:
                sub_holdings = []
                for cur, amt in sub["amounts"].items():
                    if amt <= 0:
                        continue
                    px, val = PRICES.value(cur, amt)
                    sub_holdings.append({"symbol": cur, "amount": amt,
                                         "price_usd": px, "value_usd": val})
                sub_holdings.sort(key=lambda h: (h["value_usd"] or 0, h["amount"]), reverse=True)
                if not sub_holdings:
                    continue
                subaccounts.append({
                    "name": sub["name"],
                    "uid": sub["uid"],
                    "total_usd": sum(h["value_usd"] or 0 for h in sub_holdings),
                    "holdings": sub_holdings,
                })
            subaccounts.sort(key=lambda s: s["total_usd"], reverse=True)
            if subaccounts and not include_funding:
                notes.append("Sub-account Funding wallets excluded "
                             "(set KUCOIN_INCLUDE_FUNDING=1 to add).")

    total = master_total + sum(s["total_usd"] for s in subaccounts)
    unpriced = [h["symbol"] for h in holdings if h["value_usd"] is None]
    if unpriced:
        notes.append("No USD price found for: " + ", ".join(unpriced[:8]) + " (excluded from the total)")

    subtitle = "Spot account"
    if subaccounts:
        subtitle += f" + {len(subaccounts)} sub-account{'s' if len(subaccounts) > 1 else ''}"

    return {
        "id": "kucoin",
        "label": "KuCoin",
        "subtitle": subtitle,
        "total_usd": total,
        "holdings_total": master_total,
        "holdings": holdings,
        "subaccounts": subaccounts,
        "subaccounts_mode": sub_mode,
        "notes": notes,
    }


# ------------------------------------------------------------- Ethereum -----

# symbol: (contract, decimals)
ETH_TOKENS: dict[str, tuple[str, int]] = {
    "USDC": ("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", 6),
    "USDT": ("0xdAC17F958D2ee523a2206206994597C13D831ec7", 6),
    "DAI": ("0x6B175474E89094C44Da98b954EedeAC495271d0F", 18),
    "LINK": ("0x514910771AF9Ca656af840dff83E8264EcF986CA", 18),
    "UNI": ("0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984", 18),
    "AAVE": ("0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DdAe9", 18),
    "SHIB": ("0x95aD61b0a150d79219dCF64E1A633f87B4C82F77", 18),
    "PEPE": ("0x6982508145454Ce325dDbE47a25d4ec3d2311933", 18),
    "WBTC": ("0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", 8),
    "MKR": ("0x9f8F72aA9304c8B593d555F12eF6589cC3A579A2", 18),
    "CRV": ("0xD533a949740bb3306d119CC777fa900bA034cd52", 18),
    "GRT": ("0xc944E90C64B2c07662A292be6244BDf05Cda44a7", 18),
    "ENS": ("0xC18360217D8F7Ab5e7c516566761Ea12Ce7F9D72", 18),
    "LDO": ("0x5A98FcBEA516Cf06857215779Fd812CA3beF1B32", 18),
    "ONDO": ("0xfAbA6f8e4a5E8Ab82F62fe7C39859FA577269BE3", 18),
    "WLD": ("0x163f8C2467924be0ae7B5347228CABF260318753", 18),
    "ARB": ("0xB50721BCf8d664c30412Cfbc6cf7a15145234ad1", 18),
    "FET": ("0xaea46A60368A7bD060eec7DF8CBa43b7EF41Ad85", 18),
    "INJ": ("0xe28b3B32B6c345A34Ff64674606124Dd5Aceca30", 18),
    "RENDER": ("0x6De037ef9aD2725EB40118Bb1702EBb27e4Aeb24", 18),
    "STETH": ("0xae7ab96520DE3A18E5e111B5EaAb095312D7fE84", 18),
    "WSTETH": ("0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0", 18),
    "POL": ("0x455e53CBB86018Ac2B8092FdCd39d8444aFFC3F6", 18),
}

ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def parse_extra_tokens(raw: str) -> dict[str, tuple[str, int]]:
    """EXTRA_TOKENS='SYM:0xaddr:decimals;SYM2:0xaddr:decimals'"""
    out: dict[str, tuple[str, int]] = {}
    for chunk in raw.replace(",", ";").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(":")
        if len(parts) != 3 or not ADDRESS_RE.match(parts[1].strip()):
            continue
        try:
            out[parts[0].strip().upper()] = (parts[1].strip(), int(parts[2]))
        except ValueError:
            continue
    return out


def rpc_batch(calls: list[tuple[str, list]]) -> list:
    payload = [
        {"jsonrpc": "2.0", "id": i, "method": method, "params": params}
        for i, (method, params) in enumerate(calls)
    ]
    body = json.dumps(payload)
    urls = [u.strip() for u in env("ETH_RPC_URLS", ",".join(DEFAULT_RPCS)).split(",") if u.strip()]
    last_exc: BaseException | None = None
    for url in urls:
        try:
            out = http_json(url, method="POST", body=body,
                            headers={"Content-Type": "application/json"}, timeout=20)
            if isinstance(out, dict):
                out = [out]
            by_id = {item.get("id"): item for item in out if isinstance(item, dict)}
            if len(by_id) < len(calls):
                raise RuntimeError("RPC returned an incomplete batch")
            return [by_id.get(i, {}) for i in range(len(calls))]
        except Exception as exc:  # try the next endpoint
            last_exc = exc
    raise RuntimeError(f"All Ethereum RPCs failed. Last: {describe(last_exc)}")


def eth_source() -> dict:
    address = env("ETH_ADDRESS").strip()
    if not address:
        raise ConfigError("No wallet address - set ETH_ADDRESS in .env")
    if not ADDRESS_RE.match(address):
        raise ConfigError("ETH_ADDRESS is not a valid 0x... address")

    tokens = dict(ETH_TOKENS)
    tokens.update(parse_extra_tokens(env("EXTRA_TOKENS")))

    calls: list[tuple[str, list]] = [("eth_getBalance", [address, "latest"])]
    padded = address[2:].lower().rjust(64, "0")
    for symbol, (contract, _decimals) in tokens.items():
        calls.append(("eth_call", [{"to": contract, "data": "0x70a08231" + padded}, "latest"]))
    results = rpc_batch(calls)

    def as_int(item) -> int:
        if not isinstance(item, dict) or item.get("error") or not item.get("result"):
            return 0
        try:
            return int(item["result"], 16)
        except (TypeError, ValueError):
            return 0

    raw: list[tuple[str, float]] = [("ETH", as_int(results[0]) / 1e18)]
    for i, (symbol, (_contract, decimals)) in enumerate(tokens.items(), start=1):
        raw.append((symbol, as_int(results[i]) / (10 ** decimals)))

    holdings = []
    PRICES.warm(symbol for symbol, _amount in raw)
    for symbol, amount in raw:
        if amount <= 0:
            continue
        px, val = PRICES.value(symbol, amount)
        holdings.append({"symbol": symbol, "amount": amount, "price_usd": px, "value_usd": val})
    holdings.sort(key=lambda h: (h["value_usd"] or 0, h["amount"]), reverse=True)

    notes = []
    unpriced = [h["symbol"] for h in holdings if h["value_usd"] is None]
    if unpriced:
        notes.append("No USD price found for: " + ", ".join(unpriced[:8]) + " (excluded from the total)")
    if not holdings:
        notes.append("Wallet is empty on Ethereum mainnet (or only holds tokens not in the watched list).")

    return {
        "id": "ethereum",
        "label": env("ETH_LABEL", "MetaMask"),
        "subtitle": f"{address[:6]}...{address[-4:]} · Ethereum",
        "total_usd": sum(h["value_usd"] or 0 for h in holdings),
        "holdings_total": sum(h["value_usd"] or 0 for h in holdings),
        "holdings": holdings,
        "subaccounts": [],
        "subaccounts_mode": "",
        "notes": notes,
    }


# ---------------------------------------------------------------- cache ------

_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()
_SOURCES = {"kucoin": kucoin_source, "ethereum": eth_source}


def cached_source(name: str, force: bool = False) -> dict:
    ttl = float(env("CACHE_SECONDS", "45") or 45)
    with _cache_lock:
        ts, prev = _cache.get(name, (0.0, None))
        if not force and prev is not None and time.time() - ts < ttl:
            return {**prev, "stale": False, "age_s": int(time.time() - ts), "error": None}
    try:
        fresh = _SOURCES[name]()
        with _cache_lock:
            _cache[name] = (time.time(), fresh)
        return {**fresh, "stale": False, "age_s": 0, "error": None}
    except Exception as exc:
        err = describe(exc)
        with _cache_lock:
            ts, prev = _cache.get(name, (0.0, None))
        if prev is not None:
            return {**prev, "stale": True, "age_s": int(time.time() - ts), "error": err}
        label = "KuCoin" if name == "kucoin" else "MetaMask"
        return {"id": name, "label": label, "subtitle": "", "total_usd": 0.0,
                "holdings_total": 0.0, "holdings": [], "subaccounts": [],
                "subaccounts_mode": "", "notes": [], "stale": False, "age_s": 0,
                "error": err}


def build_portfolio(force: bool = False) -> dict:
    started = time.time()
    sources = [cached_source("kucoin", force), cached_source("ethereum", force)]
    total = sum(s.get("total_usd") or 0 for s in sources)
    return {
        "ts": int(time.time()),
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "elapsed_ms": int((time.time() - started) * 1000),
        "total_usd": total,
        "sources": sources,
    }


# --------------------------------------------------------------- server ------


class Handler(BaseHTTPRequestHandler):
    server_version = "balanceviewer"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # keep the console quiet
        if self.path.startswith("/api"):
            print(f"[{time.strftime('%H:%M:%S')}] {self.path} {args[1] if len(args) > 1 else ''}")

    def _send(self, code: int, body: bytes, content_type: str, cache: str = "no-store") -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload: dict, code: int = 200) -> None:
        self._send(code, json.dumps(payload).encode(), "application/json; charset=utf-8")

    def _file(self, path: Path) -> None:
        try:
            data = path.read_bytes()
        except OSError:
            return self._json({"error": "not found"}, 404)
        types = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml",
                 ".png": "image/png", ".ico": "image/x-icon"}
        self._send(200, data, types.get(path.suffix, "application/octet-stream"),
                   cache="no-cache")

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path
        try:
            if route in ("/", "/index.html"):
                return self._file(STATIC_DIR / "index.html")
            if route == "/api/portfolio":
                force = "refresh=1" in (parsed.query or "")
                return self._json(build_portfolio(force))
            if route == "/api/health":
                return self._json({"ok": True})
            if route == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            if route.startswith("/static/"):
                target = (STATIC_DIR / route[len("/static/"):]).resolve()
                if STATIC_DIR.resolve() in target.parents and target.is_file():
                    return self._file(target)
            return self._json({"error": "not found"}, 404)
        except BrokenPipeError:
            pass
        except Exception as exc:
            self._json({"error": describe(exc)}, 500)


def main() -> int:
    bootstrap_env()
    parser = argparse.ArgumentParser(description="KuCoin + Ethereum balance dashboard")
    parser.add_argument("--host", default=env("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(env("PORT", "8787") or 8787))
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}/"
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"BalanceViewer on {url}")
    print("Watching: " + ", ".join(
        ["KuCoin spot" if env("KUCOIN_API_KEY") else "KuCoin (not configured)",
         f"wallet {env('ETH_ADDRESS') or '(not configured)'}"]))
    print("Ctrl+C to stop.")
    if not args.no_open and env("AUTO_OPEN", "1") not in ("0", "false", "no"):
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
