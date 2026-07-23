"""
Full US-listed stock universe — free, no API key.

Source: NASDAQ Trader's official symbol directory (nasdaqlisted.txt +
otherlisted.txt), the same files brokers use. Close enough to what
Trading212 offers (major US exchanges); Trading212 itself has no public
"list all instruments" endpoint without an authenticated account.
"""
import logging
import sys
import os
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import NASDAQ_LISTED_URL, OTHER_LISTED_URL

_log = logging.getLogger(__name__)

_cache = {"at": 0.0, "symbols": []}
_CACHE_TTL = 3600  # NASDAQ listing barely changes intraday; the news scanner
                    # hits this every few minutes, don't refetch every time.


def _parse_pipe_file(text: str, symbol_col: str, test_col: str = "Test Issue") -> list[str]:
    lines = text.strip().splitlines()
    if not lines:
        return []
    header = lines[0].split("|")
    try:
        sym_idx = header.index(symbol_col)
        test_idx = header.index(test_col) if test_col in header else None
    except ValueError:
        return []
    out = []
    for line in lines[1:]:
        if line.startswith("File Creation Time"):
            continue
        cols = line.split("|")
        if len(cols) <= sym_idx:
            continue
        if test_idx is not None and len(cols) > test_idx and cols[test_idx].strip().upper() == "Y":
            continue
        sym = cols[sym_idx].strip()
        if sym and "$" not in sym and "." not in sym:  # skip warrants/units/preferred-share suffix tickers
            out.append(sym)
    return out


def get_stock_universe() -> list[str]:
    """All active, non-test US-listed common tickers. In-process cached
    (1h TTL) — called by both the 4h momentum scan and the 5min news scan."""
    now = time.time()
    if now - _cache["at"] < _CACHE_TTL and _cache["symbols"]:
        return _cache["symbols"]

    symbols = set()
    try:
        resp = requests.get(NASDAQ_LISTED_URL, timeout=20)
        resp.raise_for_status()
        symbols.update(_parse_pipe_file(resp.text, "Symbol"))
    except Exception as e:
        _log.error("nasdaqlisted.txt fetch failed: %s", e)
    try:
        resp = requests.get(OTHER_LISTED_URL, timeout=20)
        resp.raise_for_status()
        symbols.update(_parse_pipe_file(resp.text, "ACT Symbol"))
    except Exception as e:
        _log.error("otherlisted.txt fetch failed: %s", e)

    if symbols:
        _cache["symbols"] = sorted(symbols)
        _cache["at"] = now
    return _cache["symbols"]
