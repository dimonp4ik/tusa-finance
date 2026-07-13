"""OKX EU public market data (no auth) — candles for the momentum screener."""
import logging
import sys
import os

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import OKX_BASE_URL

_log = logging.getLogger(__name__)


def get_candles(inst_id: str, bar: str = "1D", limit: int = 60) -> pd.DataFrame | None:
    """Returns DataFrame(ts,Open,High,Low,Close,Volume) newest-last, or None on failure.
    Volume is in the QUOTE currency (volCcyQuote) → already ~USD for USDT/USDC pairs."""
    try:
        resp = requests.get(
            f"{OKX_BASE_URL}/api/v5/market/candles",
            params={"instId": inst_id, "bar": bar, "limit": str(limit)},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if not data:
            return None
        # OKX returns [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm], newest first
        rows = [{
            "ts": int(r[0]),
            "Open": float(r[1]), "High": float(r[2]), "Low": float(r[3]), "Close": float(r[4]),
            "Volume": float(r[7]) if len(r) > 7 else float(r[5]),
        } for r in reversed(data)]
        return pd.DataFrame(rows)
    except Exception as e:
        _log.debug("OKX candles fetch failed for %s: %s", inst_id, e)
        return None
