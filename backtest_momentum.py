"""
Fast walk-forward backtest for the momentum ("x") screener logic.

Not a live-bot module — a standalone research script (like tusa stocks'
backtest.py). Answers: "when this screener would have fired historically,
did price actually keep going up over the next N weeks, and is that better
than just holding the same asset for N weeks at a random time?"

Speed comes from a local pickle cache (backtest_cache/) — same idea as tusa
stocks' backtest.py, re-fetching nothing on repeat runs.

Usage: python backtest_momentum.py [--stocks-only] [--crypto-only] [--refresh-cache]
"""
import argparse
import pickle
import statistics
import sys
import time
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent))
from src import indicators
from config import MOM_RSI_MIN, MOM_RSI_MAX, OKX_BASE_URL

CACHE_DIR = Path(__file__).parent / "backtest_cache"
CACHE_DIR.mkdir(exist_ok=True)

LOOKBACK_DAYS = 20      # ~4 trading weeks
HOLD_DAYS = 20
MIN_CHANGE_PCT = 15.0

LOOKBACK_DAYS_CRYPTO = 28   # calendar days, crypto trades 24/7
HOLD_DAYS_CRYPTO = 28

# Curated liquid universe — a full historical scan of all 12k NASDAQ tickers
# would be slow and mostly noise (delisted/illiquid names); these are large/
# mid-cap names across sectors, a reasonable proxy for "what the live
# screener would realistically flag" among liquid, tradeable names.
STOCK_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "AMD", "NFLX",
    "CRM", "ORCL", "ADBE", "INTC", "QCOM", "TXN", "MU", "PANW", "SNOW", "PLTR",
    "JPM", "BAC", "GS", "V", "MA", "AXP",
    "XOM", "CVX", "COP",
    "UNH", "JNJ", "LLY", "PFE", "ABBV", "MRK",
    "WMT", "COST", "HD", "MCD", "NKE", "SBUX", "TGT",
    "KO", "PG", "PEP", "PM",
    "BA", "CAT", "GE", "HON", "UPS", "LMT",
    "DIS", "CMCSA", "T", "VZ",
    "SPY", "QQQ",
]

CRYPTO_UNIVERSE = [
    "BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "ADA-USDT", "DOGE-USDT",
    "AVAX-USDT", "LINK-USDT", "DOT-USDT", "LTC-USDT", "BCH-USDT", "NEAR-USDT",
    "ATOM-USDT", "UNI-USDT", "AAVE-USDT", "ARB-USDT", "OP-USDT", "SUI-USDT",
    "APT-USDT", "INJ-USDT", "FIL-USDT", "ICP-USDT", "ETC-USDT", "XLM-USDT",
    "TRX-USDT", "SHIB-USDT", "PEPE-USDT", "TON-USDT", "TIA-USDT", "SEI-USDT",
]


# ── Cached fetch ──────────────────────────────────────────────────────────────

def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key.replace('/', '_')}.pkl"


def fetch_stock_history(symbol: str, refresh: bool = False) -> pd.DataFrame | None:
    path = _cache_path(f"stock_{symbol}")
    if not refresh and path.exists():
        with open(path, "rb") as f:
            return pickle.load(f)
    try:
        df = yf.download(symbol, period="2y", interval="1d", progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna()
    except Exception as e:
        print(f"  ! {symbol} fetch failed: {e}")
        return None
    with open(path, "wb") as f:
        pickle.dump(df, f)
    return df


def fetch_crypto_history(inst_id: str, refresh: bool = False, target_bars: int = 730) -> pd.DataFrame | None:
    """Paginated OKX history-candles (100/page) → ~2 years of daily bars."""
    path = _cache_path(f"crypto_{inst_id}")
    if not refresh and path.exists():
        with open(path, "rb") as f:
            return pickle.load(f)

    rows, after = [], ""
    for _ in range(target_bars // 100 + 1):
        params = {"instId": inst_id, "bar": "1D", "limit": "100"}
        if after:
            params["after"] = after
        try:
            resp = requests.get(f"{OKX_BASE_URL}/api/v5/market/history-candles", params=params, timeout=15)
            data = resp.json().get("data", [])
        except Exception as e:
            print(f"  ! {inst_id} fetch failed: {e}")
            break
        if not data:
            break
        rows.extend(data)
        after = data[-1][0]   # oldest ts in this page → next page goes further back
        time.sleep(0.15)
        if len(data) < 100:
            break

    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["ts", "Open", "High", "Low", "Close", "Vol", "VolCcy", "VolCcyQuote", "Confirm"])
    df = df.astype({"ts": "int64", "Open": float, "High": float, "Low": float, "Close": float, "VolCcyQuote": float})
    df = df.sort_values("ts").rename(columns={"VolCcyQuote": "Volume"}).reset_index(drop=True)
    with open(path, "wb") as f:
        pickle.dump(df, f)
    return df


# ── Walk-forward simulation ─────────────────────────────────────────────────

def simulate(close: pd.Series, volume: pd.Series, lookback: int, hold: int,
             min_change_pct: float) -> tuple[list, list]:
    """Returns (signal_forward_returns, baseline_forward_returns).
    Baseline = forward return from EVERY day (what you'd get holding at a
    random time), so we can tell if the screener beats "just holding"."""
    rsi_series = indicators.rsi(close)
    dollar_vol = (close * volume).rolling(10).mean()
    vol_floor = dollar_vol.quantile(0.5)   # relative floor — keeps this liquidity-agnostic across wildly different symbols

    signal_returns, baseline_returns = [], []
    n = len(close)
    i = lookback
    while i + hold < n:
        change_pct = (close.iloc[i] - close.iloc[i - lookback]) / close.iloc[i - lookback] * 100
        fwd_return = (close.iloc[i + hold] - close.iloc[i]) / close.iloc[i] * 100
        baseline_returns.append(fwd_return)

        rsi_val = rsi_series.iloc[i]
        vol_val = dollar_vol.iloc[i]
        is_signal = (
            change_pct >= min_change_pct
            and pd.notna(rsi_val) and MOM_RSI_MIN <= rsi_val <= MOM_RSI_MAX
            and pd.notna(vol_val) and vol_val >= vol_floor
        )
        if is_signal:
            signal_returns.append(fwd_return)
            i += hold   # non-overlapping: skip past the holding period
        else:
            i += 1
    return signal_returns, baseline_returns


def _stats(returns: list) -> dict:
    if not returns:
        return {"n": 0}
    wins = [r for r in returns if r > 0]
    return {
        "n": len(returns),
        "win_rate": round(len(wins) / len(returns) * 100, 1),
        "avg": round(statistics.mean(returns), 2),
        "median": round(statistics.median(returns), 2),
    }


def run(universe: list[str], fetch_fn, lookback: int, hold: int, refresh: bool, label: str) -> None:
    print(f"\n=== {label} ({len(universe)} symbols) ===")
    all_signal, all_baseline = [], []
    for sym in universe:
        df = fetch_fn(sym, refresh)
        if df is None or len(df) < lookback + hold + 30:
            continue
        sig, base = simulate(df["Close"], df["Volume"], lookback, hold, MIN_CHANGE_PCT)
        all_signal.extend(sig)
        all_baseline.extend(base)

    s, b = _stats(all_signal), _stats(all_baseline)
    print(f"Signal trades : n={s.get('n',0):>4}  win_rate={s.get('win_rate','-')}%  "
          f"avg_fwd_return={s.get('avg','-')}%  median={s.get('median','-')}%")
    print(f"Baseline (any day, same hold period): n={b.get('n',0):>4}  "
          f"win_rate={b.get('win_rate','-')}%  avg_fwd_return={b.get('avg','-')}%  median={b.get('median','-')}%")
    if s.get("n"):
        edge = s["avg"] - b.get("avg", 0)
        print(f"Edge vs baseline: {edge:+.2f}pp avg forward return")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--stocks-only", action="store_true")
    p.add_argument("--crypto-only", action="store_true")
    p.add_argument("--refresh-cache", action="store_true")
    args = p.parse_args()

    if not args.crypto_only:
        run(STOCK_UNIVERSE, fetch_stock_history, LOOKBACK_DAYS, HOLD_DAYS, args.refresh_cache,
            "STOCK MOMENTUM (4wk lookback -> 4wk forward)")
    if not args.stocks_only:
        run(CRYPTO_UNIVERSE, fetch_crypto_history, LOOKBACK_DAYS_CRYPTO, HOLD_DAYS_CRYPTO, args.refresh_cache,
            "CRYPTO MOMENTUM (4wk lookback -> 4wk forward)")
