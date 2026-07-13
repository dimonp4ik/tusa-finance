"""Full OKX EU spot crypto universe — public endpoint, no API key needed."""
import logging
import sys
import os

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import OKX_BASE_URL

_log = logging.getLogger(__name__)


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
