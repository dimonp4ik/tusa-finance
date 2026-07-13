"""
Tusa Finance — configuration.

Investing bot (not scalping): weeks-long horizon.
  - Stocks: no leverage, buy & hold only. Screened from the full US-listed
    universe (NASDAQ + NYSE/AMEX), execution later via Trading212 (manual for now).
  - Crypto: OKX EU. Spot buy OR leveraged X-Perp (2-5x) — the momentum
    screener decides per-candidate, leverage is never forced.

Everything below is env-var overridable; sane free-tier defaults otherwise.
"""
import os

from dotenv import load_dotenv
load_dotenv()

# ── Telegram ─────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Storage ──────────────────────────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", "tusa_finance.db")

# ── Scan cadence ─────────────────────────────────────────────────────────────
# Weeks-long holding period → daily scan is plenty. Keep it cheap: yfinance
# and the exchange-listing files are free but rate/IP limited, so we don't
# hammer them more than once a day.
SCAN_HOUR_UTC = int(os.getenv("SCAN_HOUR_UTC", "7"))          # once/day
DIGEST_HOUR_UTC = int(os.getenv("DIGEST_HOUR_UTC", "18"))     # evening recap

# ── Universe sources (all free, no API key) ─────────────────────────────────
# NASDAQ Trader symbol directory — official, free, no auth. Covers the full
# US-listed universe (close enough to what Trading212 offers).
NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

# OKX EU public instruments endpoint (no auth needed for market data)
OKX_BASE_URL = os.getenv("OKX_BASE_URL", "https://my.okx.com")

# ── Dividend screener thresholds ────────────────────────────────────────────
DIV_MIN_YIELD_PCT = float(os.getenv("DIV_MIN_YIELD_PCT", "3.0"))
DIV_MAX_PAYOUT_RATIO = float(os.getenv("DIV_MAX_PAYOUT_RATIO", "0.80"))   # 80%
DIV_MIN_MARKET_CAP_USD = float(os.getenv("DIV_MIN_MARKET_CAP_USD", "500000000"))  # $500M
DIV_MIN_YEARS_HISTORY = int(os.getenv("DIV_MIN_YEARS_HISTORY", "5"))
DIV_TOP_N = int(os.getenv("DIV_TOP_N", "10"))          # candidates sent per digest
# Fundamentals (.info) is a slow, one-request-per-symbol call — the full
# US universe is ~8000 tickers. Instead of hammering yfinance daily, scan a
# rotating slice per day (bot_state remembers the offset) so the whole
# universe gets covered over ~2-3 weeks without risking an IP ban.
DIV_SCAN_BATCH_PER_DAY = int(os.getenv("DIV_SCAN_BATCH_PER_DAY", "400"))

# ── Momentum ("x") screener thresholds ──────────────────────────────────────
MOM_LOOKBACK_WEEKS = int(os.getenv("MOM_LOOKBACK_WEEKS", "4"))
MOM_MIN_PRICE_CHANGE_PCT = float(os.getenv("MOM_MIN_PRICE_CHANGE_PCT", "15.0"))
MOM_MIN_VOLUME_USD = float(os.getenv("MOM_MIN_VOLUME_USD", "1000000"))   # $1M/day avg
MOM_RSI_MIN = float(os.getenv("MOM_RSI_MIN", "55"))
MOM_RSI_MAX = float(os.getenv("MOM_RSI_MAX", "80"))     # avoid buying blown-off tops
MOM_TOP_N = int(os.getenv("MOM_TOP_N", "10"))

# ── Crypto momentum universe ─────────────────────────────────────────────────
# Backtest (backtest_momentum.py) showed momentum-chasing (+15%/4wk, RSI 55-80)
# has a positive edge on liquid majors (BTC/ETH/XRP/DOGE-type) but a NEGATIVE
# edge on illiquid alts (pump-and-dump, catches tops not continuations).
# Restrict the crypto momentum screener to the top-N most liquid pairs by live
# 24h volume — an objective, self-updating "majors" filter (see
# src/universe_crypto.get_top_crypto_by_volume).
CRYPTO_MOMENTUM_UNIVERSE_SIZE = int(os.getenv("CRYPTO_MOMENTUM_UNIVERSE_SIZE", "20"))

# ── Crypto leverage (X-Perp, decided per-candidate, never forced) ───────────
CRYPTO_LEVERAGE_MIN = int(os.getenv("CRYPTO_LEVERAGE_MIN", "2"))
CRYPTO_LEVERAGE_MAX = int(os.getenv("CRYPTO_LEVERAGE_MAX", "5"))
# Above this momentum/volatility score the screener suggests leverage instead
# of spot — kept simple and tunable rather than hardcoded logic scattered
# around the codebase.
CRYPTO_LEVERAGE_SCORE_THRESHOLD = float(os.getenv("CRYPTO_LEVERAGE_SCORE_THRESHOLD", "70"))

# ── Rate limiting (keep yfinance/free sources from getting us IP-banned) ───
YFINANCE_BATCH_SIZE = int(os.getenv("YFINANCE_BATCH_SIZE", "50"))
YFINANCE_SLEEP_SEC = float(os.getenv("YFINANCE_SLEEP_SEC", "0.5"))

# ── Admin (for future Telegram commands) ────────────────────────────────────
ADMIN_USER_IDS = {int(x) for x in os.getenv("ADMIN_USER_IDS", "").split(",") if x.strip().isdigit()}
