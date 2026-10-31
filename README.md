# BalanceViewer — KuCoin spot + MetaMask

One page, two panels: your KuCoin spot balances (including sub-accounts) and your
Ethereum wallet (the address MetaMask holds), each valued in USD, plus a combined
total.

```
python server.py            # http://127.0.0.1:8787
```

No `pip install`, no Node, no build step — standard library only. Python 3.9+.

## Setup

1. **KuCoin** — API Management → Create API. Give it **General permission only**:
   no Trade, no Transfer. Add your IP to the whitelist. Copy the key, secret and
   passphrase into `.env`.
2. **Wallet** — put your public `0x...` address in `ETH_ADDRESS`. This is the
   address MetaMask shows in the account selector. Never paste a seed phrase or
   private key anywhere; the app only needs the public address.
3. `copy .env.example .env` and fill it in, then run `python server.py`.

The app also auto-picks up `KUCOIN_*` from `../repo/.env` if those are already
filled in, so you don't have to duplicate them.

## What it does

| Panel | Source | How |
|---|---|---|
| KuCoin | `GET /api/v1/accounts?type=trade` (signed, HMAC-SHA256 v2) | every non-zero spot asset |
| KuCoin sub-accounts | `GET /api/ua/v2/sub-account/balance`, falling back to `GET /api/v2/sub-accounts` | one row per sub-account, rolled into the KuCoin total |
| MetaMask | Ethereum JSON-RPC `eth_getBalance` + `balanceOf` batches | ETH + 23 common ERC-20s |

Prices: KuCoin `allTickers` (native exchange price) → DefiLlama (contract-accurate,
no key) → CoinGecko, so assets with no `-USDT` pair (stETH, wstETH, …) still get
a real price. A 45 s cache keeps you from hammering any of them; `?refresh=1`
(the Refresh button) bypasses it.

Values without a price are listed but excluded from the total and flagged in the
card footer — no silent zeros.

## Common tweaks

- **Sub-accounts are missing.** The key has to belong to the **master** account —
  a sub-account key only sees itself, never its siblings. With a master key the
  page auto-detects unified account (UTA) mode, then falls back to classic.
  UTA mode only counts `SPOT` and `FUNDING` wallets (`KUCOIN_SUB_ACCOUNT_TYPES`);
  margin and futures balances are deliberately left out. Set
  `KUCOIN_INCLUDE_SUBACCOUNTS=0` to hide them.
- **Funding account money missing?** KuCoin parks deposits in the Funding
  (`main`) wallet, not Spot. The KuCoin card warns you when balances sit there;
  set `KUCOIN_INCLUDE_FUNDING=1` to include them.
- **A token isn't showing?** Add it: `EXTRA_TOKENS=PEPE:0x6982...1933:18`. The
  page only scans a fixed list because there is no free "all tokens for an
  address" on mainnet without an indexer API key.
- **RPC keeps failing?** Reorder `ETH_RPC_URLS`, or drop in your own Alchemy /
  Infura endpoint.
- **Reach it from your phone?** `python server.py --host 0.0.0.0`, then use your
  LAN IP. Only do this on a network you trust — there is no login.

## Security notes

- Binds to `127.0.0.1` by default.
- The API secret is used server-side only; the browser receives valuations, never
  credentials. Errors are sanitised before they reach the page.
- `.env` is git-ignored. A read-only KuCoin key limits the blast radius even if
  this file leaks.
- The wallet side is read-only by nature: a public address cannot move funds.
