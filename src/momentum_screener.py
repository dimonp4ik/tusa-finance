"""
Momentum ("x") screener — stocks + crypto, pure price/volume/RSI, no LLM.

Stocks: bulk OHLCV pull (cheap, no per-symbol fundamentals call) over the
full universe. Crypto: OKX EU public candles over all live SPOT pairs.

Crypto candidates additionally get a suggest_leverage flag — the momentum
screener decides per-candidate whether spot or leveraged (2-5x X-Perp) fits
better; leverage is never forced.
"""
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    MOM_LOOKBACK_WEEKS, MOM_MIN_PRICE_CHANGE_PCT, MOM_MIN_VOLUME_USD,
    MOM_RSI_MIN, MOM_RSI_MAX, MOM_TOP_N, CRYPTO_LEVERAGE_SCORE_THRESHOLD,
)
from src import market_data, okx_public, indicators

_log = logging.getLogger(__name__)

_LOOKBACK_DAYS = MOM_LOOKBACK_WEEKS * 5   # trading days (stocks)
_LOOKBACK_DAYS_CRYPTO = MOM_LOOKBACK_WEEKS * 7  # calendar days (crypto trades 24/7)


def _score(price_change_pct: float, volume_usd: float) -> float:
    vol_component = min(100, (volume_usd / MOM_MIN_VOLUME_USD) * 10) if MOM_MIN_VOLUME_USD > 0 else 100
    return round(min(100, price_change_pct * 0.8 + vol_component * 0.2), 1)


def screen_stocks(universe: list[str]) -> list[dict]:
    _log.info("momentum screener: pulling price history for %d stock symbols", len(universe))
    history = market_data.get_stock_price_history(universe, period="3mo", interval="1d")

    candidates = []
    for symbol, df in history.items():
        if len(df) < _LOOKBACK_DAYS + 5:
            continue
        close = df["Close"]
        change_pct = indicators.pct_change_over(close, _LOOKBACK_DAYS)
        if change_pct is None or change_pct < MOM_MIN_PRICE_CHANGE_PCT:
            continue

        avg_dollar_vol = float((df["Close"] * df["Volume"]).tail(10).mean())
        if avg_dollar_vol < MOM_MIN_VOLUME_USD:
            continue

        rsi_val = float(indicators.rsi(close).iloc[-1])
        if not (MOM_RSI_MIN <= rsi_val <= MOM_RSI_MAX):
            continue

        candidates.append({
            "symbol": symbol,
            "asset_type": "stock",
            "price_change_pct": round(change_pct, 1),
            "volume_usd": round(avg_dollar_vol, 0),
            "rsi": round(rsi_val, 1),
            "score": _score(change_pct, avg_dollar_vol),
            "suggest_leverage": 0,   # stocks: never — no-leverage policy
            "lookback_weeks": MOM_LOOKBACK_WEEKS,
        })

    candidates.sort(key=lambda r: r["score"], reverse=True)
    return candidates[:MOM_TOP_N]


def screen_crypto(inst_ids: list[str]) -> list[dict]:
    _log.info("momentum screener: pulling OKX candles for %d crypto pairs", len(inst_ids))
    candidates = []
    for inst_id in inst_ids:
        df = okx_public.get_candles(inst_id, bar="1D", limit=_LOOKBACK_DAYS_CRYPTO + 10)
        if df is None or len(df) < _LOOKBACK_DAYS_CRYPTO + 5:
            continue
        close = df["Close"]
        change_pct = indicators.pct_change_over(close, _LOOKBACK_DAYS_CRYPTO)
        if change_pct is None or change_pct < MOM_MIN_PRICE_CHANGE_PCT:
            continue

        avg_dollar_vol = float(df["Volume"].tail(10).mean())
        if avg_dollar_vol < MOM_MIN_VOLUME_USD:
            continue

        rsi_val = float(indicators.rsi(close).iloc[-1])
        if not (MOM_RSI_MIN <= rsi_val <= MOM_RSI_MAX):
            continue

        score = _score(change_pct, avg_dollar_vol)
        candidates.append({
            "symbol": inst_id,
            "asset_type": "crypto",
            "price_change_pct": round(change_pct, 1),
            "volume_usd": round(avg_dollar_vol, 0),
            "rsi": round(rsi_val, 1),
            "score": score,
            "suggest_leverage": int(score >= CRYPTO_LEVERAGE_SCORE_THRESHOLD),
            "lookback_weeks": MOM_LOOKBACK_WEEKS,
        })

    candidates.sort(key=lambda r: r["score"], reverse=True)
    return candidates[:MOM_TOP_N]
