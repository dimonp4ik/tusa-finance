"""
Free market-context signals that historically move momentum win rates.

1. Regime filter — SPY vs its 200-day MA. Momentum's edge concentrates in
   market uptrends and reverses hard in downtrends (Cooper/Gutierrez/Hameed
   2004 and every replication since). We TAG the digest rather than block
   signals: the user decides, but sees the regime up front.
2. Crypto Fear & Greed — alternative.me free JSON (no key). Chasing pumps
   during Extreme Greed is where lottery tickets burn fastest.
3. OKX perp funding rate — public endpoint, no key. Very positive funding =
   crowded longs; the screener's leverage suggestion is downgraded to
   spot-only for that candidate.

Everything degrades gracefully: any fetch failure returns None and the
digest simply omits that context line.
"""
import logging
import sys
import os

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import OKX_BASE_URL, REGIME_MA_DAYS, FNG_EXTREME_GREED, FNG_EXTREME_FEAR

_log = logging.getLogger(__name__)


def get_stock_regime() -> dict | None:
    """{'bullish': bool, 'spy': float, 'ma': float} or None on failure."""
    try:
        import yfinance as yf
        df = yf.download("SPY", period="1y", interval="1d", progress=False, auto_adjust=True)
        if hasattr(df.columns, "get_level_values") and df.columns.nlevels > 1:
            df.columns = df.columns.get_level_values(0)
        close = df["Close"].dropna()
        if len(close) < REGIME_MA_DAYS:
            return None
        ma = float(close.tail(REGIME_MA_DAYS).mean())
        spy = float(close.iloc[-1])
        return {"bullish": spy > ma, "spy": spy, "ma": ma}
    except Exception as e:
        _log.warning("stock regime fetch failed: %s", e)
        return None


def get_crypto_fear_greed() -> dict | None:
    """{'value': int, 'label': str} from alternative.me, or None."""
    try:
        resp = requests.get("https://api.alternative.me/fng/", timeout=15)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if not data:
            return None
        return {"value": int(data[0]["value"]), "label": data[0].get("value_classification", "")}
    except Exception as e:
        _log.warning("crypto fear&greed fetch failed: %s", e)
        return None


def get_funding_rate(inst_id: str) -> float | None:
    """Current funding rate for the coin's USDT perp on OKX (public, no key).
    inst_id is the SPOT id ('SOL-USDT') — we look up 'SOL-USDT-SWAP'.
    None when the coin has no perp or the fetch fails."""
    swap_id = f"{inst_id}-SWAP"
    try:
        resp = requests.get(
            f"{OKX_BASE_URL}/api/v5/public/funding-rate",
            params={"instId": swap_id},
            timeout=10,
        )
        data = resp.json().get("data", [])
        if not data:
            return None
        return float(data[0]["fundingRate"])
    except Exception as e:
        _log.debug("funding rate fetch failed for %s: %s", swap_id, e)
        return None


def context_header_lines() -> list[str]:
    """Digest header lines describing the current market context."""
    lines = []
    regime = get_stock_regime()
    if regime:
        if regime["bullish"]:
            lines.append(f"🟢 Рынок США: бычий режим (SPY выше {REGIME_MA_DAYS}-дн. средней)")
        else:
            lines.append(
                f"🔴 Рынок США: медвежий режим (SPY ниже {REGIME_MA_DAYS}-дн. средней) — "
                "моментум исторически слабее, осторожнее с покупками"
            )
    fng = get_crypto_fear_greed()
    if fng:
        line = f"🪙 Крипто-настроение: {fng['value']}/100 ({fng['label']})"
        if fng["value"] >= FNG_EXTREME_GREED:
            line += " — жадность на максимуме, входы в пампы рискованнее обычного"
        elif fng["value"] <= FNG_EXTREME_FEAR:
            line += " — страх на максимуме, исторически неплохая зона для набора позиций"
        lines.append(line)
    return lines
