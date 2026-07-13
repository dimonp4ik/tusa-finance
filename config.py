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
# Dividend fundamentals don't change intraday — daily is plenty, and .info is
# a slow one-request-per-symbol call (see DIV_SCAN_BATCH_PER_DAY) so there's
# no upside to running it more often.
SCAN_HOUR_UTC = int(os.getenv("SCAN_HOUR_UTC", "7"))          # once/day
DIGEST_HOUR_UTC = int(os.getenv("DIGEST_HOUR_UTC", "18"))     # evening recap
# Momentum + unusual-volume DO benefit from freshness (unusual-volume exists
# specifically to catch a move the same day it starts) — these rescan every
# N hours instead of once/day. Bulk OHLCV pull only (no .info), so the extra
# runs are cheap-ish; still free/unofficial yfinance, so not sub-hourly.
MOMENTUM_SCAN_INTERVAL_HOURS = int(os.getenv("MOMENTUM_SCAN_INTERVAL_HOURS", "4"))

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

# ── Unusual volume screener (stocks) ────────────────────────────────────────
# Catches a move on DAY 1 (volume spike + starting to react), unlike the
# momentum screener above which requires the +15%/4wk move to have already
# happened. Free source: same bulk OHLCV pull momentum already does — no
# extra API calls, no finviz/paid screener needed.
UNUSUAL_VOLUME_MULT = float(os.getenv("UNUSUAL_VOLUME_MULT", "3.0"))            # today's $vol vs 20d avg
UNUSUAL_VOLUME_MIN_PRICE_CHANGE_PCT = float(os.getenv("UNUSUAL_VOLUME_MIN_PRICE_CHANGE_PCT", "3.0"))
UNUSUAL_VOLUME_TOP_N = int(os.getenv("UNUSUAL_VOLUME_TOP_N", "10"))

# ── Insider buying enrichment (stocks only — SEC EDGAR, free, no key) ───────
# Applied to already-shortlisted candidates (dividend/momentum/unusual-volume
# top-N), not the full universe — SEC per-filing lookups are one HTTP call
# each, and there's no point scanning 12000 tickers we're not going to alert.
INSIDER_LOOKBACK_DAYS = int(os.getenv("INSIDER_LOOKBACK_DAYS", "30"))
INSIDER_MIN_VALUE_USD = float(os.getenv("INSIDER_MIN_VALUE_USD", "25000"))
# SEC requires a descriptive User-Agent identifying the requester (no key needed).
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "TusaFinance/1.0 (contact: dimapetrov.2006.12@gmail.com)")

# ── Crypto momentum universe ─────────────────────────────────────────────────
# Backtest (backtest_momentum.py) showed momentum-chasing (+15%/4wk, RSI 55-80)
# has a positive edge on liquid majors (BTC/ETH/XRP/DOGE-type) but a NEGATIVE
# edge on illiquid alts on average (pump-and-dump, catches tops not
# continuations). User wants the full OKX EU universe scanned anyway — a real
# few-day 2-5x small-cap pump is exactly the outlier a "top liquid pairs
# only" filter would hide; this is a small-size lottery-ticket bet, not an
# expectancy play. Low-liquidity hits get tagged in Telegram so sizing stays
# small on the risky ones (see telegram_notifier.format_momentum_candidate).
CRYPTO_LOW_LIQUIDITY_USD = float(os.getenv("CRYPTO_LOW_LIQUIDITY_USD", "5000000"))  # $5M/day

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

# ── Admin ────────────────────────────────────────────────────────────────────
# Super-admin hardcoded here, same pattern as the other tusa bots — not env-var
# driven (others get added via the bot itself once that exists).
ADMIN_USER_IDS = {671071896} | {
    int(x) for x in os.getenv("ADMIN_USER_IDS", "").split(",") if x.strip().isdigit()
}
