"""BalanceViewer for Streamlit.

The data layer is `server.py` (unchanged); this file is only the UI. Streamlit
supplies secrets through `st.secrets` while `server.py` reads `os.environ`, so
the first thing we do is bridge the two.

    streamlit run streamlit_app.py
"""
from __future__ import annotations

import hmac
import os

import pandas as pd
import streamlit as st

st.set_page_config(page_title="BalanceViewer", page_icon="💼", layout="wide")


def _bridge_secrets() -> None:
    """Copy flat st.secrets entries into os.environ; real env vars still win."""
    try:
        for key, value in st.secrets.items():
            if isinstance(value, (str, int, float)) and str(value).strip():
                os.environ.setdefault(str(key), str(value))
    except Exception:
        pass  # no secrets.toml locally: .env is used instead


_bridge_secrets()

import server  # noqa: E402  (must come after the environment is populated)

server.bootstrap_env()  # local runs: fills any gaps from .env / ../repo/.env

# ------------------------------------------------------------------ auth ----

_password = os.environ.get("APP_PASSWORD", "").strip()
if _password and not st.session_state.get("_authed"):
    st.title("BalanceViewer")
    st.caption("This dashboard shows private balances.")
    entered = st.text_input("Password", type="password", autocomplete="current-password")
    if entered:
        if hmac.compare_digest(entered, _password):
            st.session_state["_authed"] = True
            st.rerun()
        else:
            st.error("Wrong password")
    st.stop()

# ------------------------------------------------------------------- ui -----

COLUMNS = ["symbol", "amount", "price_usd", "value_usd"]
COLUMN_CONFIG = {
    "symbol": st.column_config.TextColumn("Asset", width="small"),
    "amount": st.column_config.NumberColumn("Amount", format="%.6g"),
    "price_usd": st.column_config.NumberColumn("Price", format="$%.4f"),
    "value_usd": st.column_config.NumberColumn("Value", format="$%.2f"),
}


def money(value) -> str:
    return "—" if value is None else f"${value:,.2f}"


def holdings_table(holdings: list[dict]) -> None:
    df = pd.DataFrame(holdings)
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = None
    st.dataframe(df[COLUMNS], hide_index=True, width="stretch",
                 column_config=COLUMN_CONFIG)


def source_panel(src: dict) -> None:
    st.subheader(src.get("label", "—"))
    if src.get("subtitle"):
        st.caption(src["subtitle"])
    st.metric("Value", money(src.get("total_usd")))

    if src.get("error"):
        st.warning(src["error"], icon="⚠️")

    holdings = src.get("holdings") or []
    if holdings:
        holdings_table(holdings)
    elif not src.get("error"):
        st.caption("No balances")

    for sub in src.get("subaccounts") or []:
        with st.expander(f"{sub['name']} — {money(sub['total_usd'])}"):
            if sub.get("uid"):
                st.caption(sub["uid"])
            holdings_table(sub.get("holdings") or [])

    for note in src.get("notes") or []:
        st.caption(f"⚠ {note}")


@st.cache_data(ttl=45, show_spinner="Fetching balances…")
def load(force: int = 0) -> dict:
    return server.build_portfolio(force=bool(force))


@st.fragment(run_every="60s")
def dashboard() -> None:
    head, action = st.columns([4, 1], vertical_alignment="center")
    with head:
        st.caption("KuCoin spot + Ethereum wallet, valued in USD")
    with action:
        if st.button("Refresh", type="primary", width="stretch"):
            load.clear()

    data = load(0)

    st.metric("Total", money(data["total_usd"]),
              help="KuCoin (incl. sub-accounts) + Ethereum wallet")
    st.caption(f"updated {data['updated']} · {data['elapsed_ms']} ms · "
               "auto-refreshes every 60s")

    for col, src in zip(st.columns(len(data["sources"]), gap="large"), data["sources"]):
        with col:
            source_panel(src)


dashboard()

with st.sidebar:
    st.header("Sources")
    st.caption(f"KuCoin keys: {'set' if os.environ.get('KUCOIN_API_KEY') else 'missing'}")
    st.caption(f"Wallet: {os.environ.get('ETH_ADDRESS') or 'missing'}")
    st.caption(f"Password gate: {'on' if _password else 'off'}")
    st.divider()
    st.caption("Prices: KuCoin tickers → DefiLlama → CoinGecko. "
               "Cached 45s to respect rate limits.")
