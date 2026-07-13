"""
Dividend screener — pure fundamentals, no LLM.

Scans a rotating daily slice of the full US-listed universe (see
config.DIV_SCAN_BATCH_PER_DAY) since fundamentals lookups are one
request per symbol and the free tickers this whole project runs on
would get IP-banned scanning ~8000 tickers every day for a bot whose
horizon is weeks, not minutes.
"""
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    DIV_MIN_YIELD_PCT, DIV_MAX_PAYOUT_RATIO, DIV_MIN_MARKET_CAP_USD,
    DIV_MIN_YEARS_HISTORY, DIV_TOP_N, DIV_SCAN_BATCH_PER_DAY,
)
from src import market_data, db

_log = logging.getLogger(__name__)
_STATE_KEY = "dividend_scan_offset"


def _next_slice(universe: list[str]) -> list[str]:
    """Rotating window over the sorted universe, persisted across restarts."""
    if not universe:
        return []
    offset = int(db.get_state(_STATE_KEY, "0"))
    if offset >= len(universe):
        offset = 0
    end = offset + DIV_SCAN_BATCH_PER_DAY
    batch = universe[offset:end]
    db.set_state(_STATE_KEY, str(end if end < len(universe) else 0))
    return batch


def screen(universe: list[str]) -> list[dict]:
    batch = _next_slice(universe)
    _log.info("dividend screener: scanning %d/%d symbols", len(batch), len(universe))

    candidates = []
    for symbol in batch:
        info = market_data.get_fundamentals(symbol)
        if not info:
            continue

        # dividendRate ($/share) / price is unambiguous, unlike yfinance's
        # dividendYield field whose units have flipped between fraction and
        # percent across versions. Fall back to dividendYield taken as-is
        # (percent) only when dividendRate/price aren't available.
        price = info.get("regularMarketPrice") or info.get("currentPrice") or 0
        dividend_rate = info.get("dividendRate")
        if dividend_rate and price:
            yield_pct = dividend_rate / price * 100
        else:
            yield_pct = info.get("dividendYield") or 0
        payout_ratio = info.get("payoutRatio")
        market_cap = info.get("marketCap") or 0

        if yield_pct < DIV_MIN_YIELD_PCT:
            continue
        if payout_ratio is None or payout_ratio <= 0 or payout_ratio > DIV_MAX_PAYOUT_RATIO:
            continue
        if market_cap < DIV_MIN_MARKET_CAP_USD:
            continue

        years = market_data.get_dividend_years(symbol)
        if years < DIV_MIN_YEARS_HISTORY:
            continue

        # Transparent 0-100 scoring, each component capped so long-history
        # blue chips (many payers clear 25+ years) don't all saturate at 100:
        # yield up to 40pt, history up to 30pt, payout-ratio safety up to 30pt.
        safety = 1 - (payout_ratio / DIV_MAX_PAYOUT_RATIO)
        score = min(40, yield_pct * 8) + min(30, years) + safety * 30

        candidates.append({
            "symbol": symbol,
            "name": info.get("shortName") or info.get("longName") or "",
            "yield_pct": round(yield_pct, 2),
            "payout_ratio": round(payout_ratio, 3),
            "market_cap": market_cap,
            "years_history": years,
            "score": round(score, 1),
        })

    candidates.sort(key=lambda r: r["score"], reverse=True)
    return candidates[:DIV_TOP_N]
