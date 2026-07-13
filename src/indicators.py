"""Minimal, timeframe-agnostic TA primitives (pandas Series in, Series/float out)."""
import pandas as pd


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def pct_change_over(close: pd.Series, periods: int) -> float | None:
    if len(close) <= periods:
        return None
    start, end = close.iloc[-periods - 1], close.iloc[-1]
    if start <= 0:
        return None
    return (end - start) / start * 100
