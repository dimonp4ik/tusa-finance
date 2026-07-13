"""Full OKX EU spot crypto universe — public endpoint, no API key needed."""
import logging
import sys
import os

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import OKX_BASE_URL

_log = logging.getLogger(__name__)

# Stablecoins / commodity-backed tokens as the BASE asset — momentum on
# these is meaningless (they're designed to not move) and they showed up
# polluting the raw top-by-volume ranking (USDC-USDT, XAUT-USDT, ...).
_NON_MOMENTUM_BASES = {
    "USDT", "USDC", "USDG", "DAI", "TUSD", "FDUSD", "PYUSD", "USDE", "USDP",
    "XAUT", "PAXG",
}


def get_crypto_universe(quote_currencies=("USDT", "USDC")) -> list[str]:
    """Live OKX EU SPOT instrument IDs, e.g. 'BTC-USDT'. Filtered to major
    stablecoin quote pairs so screener math (price/volume in USD) is simple."""
    try:
        resp = requests.get(
            f"{OKX_BASE_URL}/api/v5/public/instruments",
            params={"instType": "SPOT"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except Exception as e:
        _log.error("OKX instruments fetch failed: %s", e)
        return []

    out = []
    for x in data:
        if x.get("state") != "live":
            continue
        inst_id = x.get("instId", "")
        quote = x.get("quoteCcy", "")
        if quote in quote_currencies:
            out.append(inst_id)
    return sorted(set(out))


def get_top_crypto_by_volume(n: int, quote_currencies=("USDT", "USDC")) -> list[str]:
    """Top-N SPOT pairs by 24h quote-currency volume — an objective, live
    "majors" proxy. Backtesting showed momentum-chasing works on majors
    (BTC/ETH/XRP/DOGE-type liquid names) but loses money on illiquid alts
    that pump-and-dump; ranking by live volume avoids hardcoding a symbol
    list that would go stale as coins rise/fall in liquidity."""
    try:
        resp = requests.get(
            f"{OKX_BASE_URL}/api/v5/market/tickers",
            params={"instType": "SPOT"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except Exception as e:
        _log.error("OKX tickers fetch failed: %s", e)
        return []

    ranked = []
    for x in data:
        inst_id = x.get("instId", "")
        base, quote = (inst_id.split("-") + [""])[:2]
        if quote not in quote_currencies or base in _NON_MOMENTUM_BASES:
            continue
        try:
            vol_quote_24h = float(x.get("volCcy24h") or 0)
        except (TypeError, ValueError):
            continue
        ranked.append((inst_id, vol_quote_24h))

    ranked.sort(key=lambda r: r[1], reverse=True)
    # de-dupe base assets that have both -USDT and -USDC pairs, keep the more liquid one
    seen_base, out = set(), []
    for inst_id, _ in ranked:
        base = inst_id.split("-")[0]
        if base in seen_base:
            continue
        seen_base.add(base)
        out.append(inst_id)
        if len(out) >= n:
            break
    return out
