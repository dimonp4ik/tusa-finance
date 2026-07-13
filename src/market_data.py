"""
Shared bulk price-data fetch layer — used by both screeners so the
expensive step (pulling price history for the whole universe) happens once.

yfinance is free but unofficial and IP-rate-limited, so we batch requests
and sleep between batches (see config.YFINANCE_BATCH_SIZE/SLEEP_SEC).
"""
import logging
import sys
import os
import time

import pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import YFINANCE_BATCH_SIZE, YFINANCE_SLEEP_SEC

_log = logging.getLogger(__name__)


def get_stock_price_history(symbols: list[str], period: str = "2mo", interval: str = "1d") -> dict:
    """symbol -> DataFrame(Open,High,Low,Close,Volume) for symbols with data.
    Batched + throttled to stay polite to Yahoo's free endpoint."""
    out = {}
    for i in range(0, len(symbols), YFINANCE_BATCH_SIZE):
        batch = symbols[i:i + YFINANCE_BATCH_SIZE]
        try:
            data = yf.download(
                tickers=" ".join(batch),
                period=period,
                interval=interval,
                group_by="ticker",
                threads=True,
                progress=False,
                auto_adjust=True,
            )
        except Exception as e:
            _log.warning("batch download failed (%d symbols): %s", len(batch), e)
            time.sleep(YFINANCE_SLEEP_SEC)
            continue

        if isinstance(data.columns, pd.MultiIndex):
            for sym in batch:
                try:
                    df = data[sym].dropna()
                    if not df.empty:
                        out[sym] = df
                except KeyError:
                    continue
        elif not data.empty:
            # yfinance drops the MultiIndex when only one ticker actually
            # returned data, even if the batch requested more than one.
            out[batch[0]] = data.dropna()
        time.sleep(YFINANCE_SLEEP_SEC)
    return out


def get_fundamentals(symbol: str) -> dict | None:
    """.info fundamentals for ONE symbol — only call on an already-filtered,
    liquid subset (this is the slow, one-request-per-symbol call)."""
    try:
        t = yf.Ticker(symbol)
        info = t.info
        if not info or info.get("regularMarketPrice") is None:
            return None
        return info
    except Exception as e:
        _log.debug("fundamentals fetch failed for %s: %s", symbol, e)
        return None


def get_dividend_years(symbol: str) -> int:
    """Span (in years) of the dividend payment history yfinance has on file."""
    try:
        t = yf.Ticker(symbol)
        divs = t.dividends
        if divs is None or divs.empty:
            return 0
        first, last = divs.index.min(), divs.index.max()
        return max(0, (last - first).days // 365)
    except Exception:
        return 0
