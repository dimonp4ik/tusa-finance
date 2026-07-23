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
# Dividend fundamentals move on a quarterly cadence at best — weekly is
# already generous, daily was noise (user: too frequent for data that barely
# changes). Once a week, Sunday evening — before the US week opens, time to
# read it and decide.
DIV_SCAN_DAY_OF_WEEK = os.getenv("DIV_SCAN_DAY_OF_WEEK", "sun")  # apscheduler day name
SCAN_HOUR_UTC = int(os.getenv("SCAN_HOUR_UTC", "19"))            # evening
DIGEST_HOUR_UTC = int(os.getenv("DIGEST_HOUR_UTC", "18"))        # evening recap
# Momentum + unusual-volume DO benefit from freshness (unusual-volume exists
# specifically to catch a move the same day it starts) — these rescan every
# N hours instead of once/day. Bulk OHLCV pull only (no .info), so the extra
# runs are cheap-ish; still free/unofficial yfinance, so not sub-hourly.
MOMENTUM_SCAN_INTERVAL_HOURS = int(os.getenv("MOMENTUM_SCAN_INTERVAL_HOURS", "4"))
# News-catalyst scan — see src/news_scanner.py. This is the one screener that
# genuinely benefits from running often: a catalyst headline can be the
# FIRST signal, before price/volume have caught up (unlike momentum/unusual-
# volume, which only see a move after it's already started). Free RSS + free
# Groq tier, so frequent polling is cheap — just don't go sub-minute.
NEWS_SCAN_INTERVAL_MINUTES = int(os.getenv("NEWS_SCAN_INTERVAL_MINUTES", "5"))
# Overlap the lookback past the scan interval so a slow RSS feed / a scan
# that ran a bit late never drops a headline in the gap; headline-hash dedup
# (db.was_headline_seen) prevents re-processing the same one twice.
NEWS_LOOKBACK_MINUTES = int(os.getenv("NEWS_LOOKBACK_MINUTES", "20"))
# Don't re-alert the SAME symbol again within this window even if a second
# headline about it shows up — avoids a burst of 5 alerts for one story
# getting re-reported by 5 outlets. Short on purpose (news kind ≠ momentum
# kind's 7-day cooldown): a genuinely new catalyst a few hours later should
# still get through.
NEWS_SYMBOL_COOLDOWN_HOURS = float(os.getenv("NEWS_SYMBOL_COOLDOWN_HOURS", "3"))

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
DIV_TOP_N = int(os.getenv("DIV_TOP_N", "5"))          # candidates sent per digest
# Fundamentals (.info) is a slow, one-request-per-symbol call — the full
# US universe is ~12000 tickers. Instead of hammering yfinance in one giant
# burst, scan a rotating slice each weekly run (bot_state remembers the
# offset) so the whole universe cycles over ~3 months without risking an IP
# ban from firing thousands of .info calls in one sitting.
DIV_SCAN_BATCH_PER_DAY = int(os.getenv("DIV_SCAN_BATCH_PER_DAY", "1000"))

# ── Momentum ("x") screener thresholds ──────────────────────────────────────
MOM_LOOKBACK_WEEKS = int(os.getenv("MOM_LOOKBACK_WEEKS", "4"))
MOM_MIN_PRICE_CHANGE_PCT = float(os.getenv("MOM_MIN_PRICE_CHANGE_PCT", "15.0"))
MOM_MIN_VOLUME_USD = float(os.getenv("MOM_MIN_VOLUME_USD", "1000000"))   # $1M/day avg
MOM_RSI_MIN = float(os.getenv("MOM_RSI_MIN", "55"))
MOM_RSI_MAX = float(os.getenv("MOM_RSI_MAX", "80"))     # avoid buying blown-off tops
# Per-class shortlist size (stocks and crypto screened separately)…
MOM_TOP_N = int(os.getenv("MOM_TOP_N", "10"))
# …then merged into ONE digest of this size, guaranteed ≥1 stock and ≥1
# crypto when both classes produced hits (user: too many signals per scan).
MOM_DIGEST_TOP_N = int(os.getenv("MOM_DIGEST_TOP_N", "5"))

# ── Unusual volume screener (stocks) ────────────────────────────────────────
# Catches a move on DAY 1 (volume spike + starting to react), unlike the
# momentum screener above which requires the +15%/4wk move to have already
# happened. Free source: same bulk OHLCV pull momentum already does — no
# extra API calls, no finviz/paid screener needed.
UNUSUAL_VOLUME_MULT = float(os.getenv("UNUSUAL_VOLUME_MULT", "3.0"))            # today's $vol vs 20d avg
UNUSUAL_VOLUME_MIN_PRICE_CHANGE_PCT = float(os.getenv("UNUSUAL_VOLUME_MIN_PRICE_CHANGE_PCT", "3.0"))
UNUSUAL_VOLUME_TOP_N = int(os.getenv("UNUSUAL_VOLUME_TOP_N", "5"))

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

# ── Market context (all free, no keys) ──────────────────────────────────────
# Regime filter: momentum strategies historically only earn their edge when
# the broad market is in an uptrend (SPY above its 200-day MA); in downtrends
# momentum crashes. We tag (not block) signals so the user sees the regime.
REGIME_MA_DAYS = int(os.getenv("REGIME_MA_DAYS", "200"))
# Crypto Fear & Greed (alternative.me, free JSON, no key): buying pumps
# during "Extreme Greed" is where the lottery tickets burn fastest.
FNG_EXTREME_GREED = int(os.getenv("FNG_EXTREME_GREED", "75"))
FNG_EXTREME_FEAR = int(os.getenv("FNG_EXTREME_FEAR", "25"))
# OKX perp funding rate: above this per-8h rate longs are crowded — the
# leverage suggestion gets downgraded to spot-only on that candidate.
FUNDING_RATE_WARN = float(os.getenv("FUNDING_RATE_WARN", "0.0005"))  # 0.05%/8h

# ── AI verifier (optional second opinion on digest candidates) ──────────────
# Preferred: Claude (user already has a key from the other tusa bots —
# CLAUDE_API_KEY, same env name as the crypto bot). Haiku 4.5 by default:
# the judge task is simple (5 candidates → BUY/SKIP + short reason) and
# Haiku costs ~$0.5/mo on this load; set JUDGE_MODEL=claude-sonnet-5 for a
# smarter judge at ~$1.5/mo. DeepSeek stays as the fallback provider.
# No key set at all → step skipped entirely, bot works exactly as before.
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "")
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "claude-haiku-4-5")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
# Only BUY-verdict candidates get sent — a SKIP verdict is the judge saying
# "don't act on this", showing it anyway is noise (user: don't want to see
# rejected setups). SKIP'd candidates stay in the DB (screener history) and
# are NOT marked sent, so they can resurface later if conditions change.
HIDE_JUDGE_SKIPPED = os.getenv("HIDE_JUDGE_SKIPPED", "1") != "0"

# ── News-catalyst scanner (free RSS + free Groq tier) ───────────────────────
# Groq (console.groq.com, free tier ~14400 req/day) scores whether a matched
# headline is an actual tradeable catalyst, not just an incidental mention —
# same role Groq already plays in the crypto/stocks bots' news_agent.py.
# No key → matched headlines are still logged but never sent (no sentiment
# gate = too noisy to trust blindly).
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

# ── Rate limiting (keep yfinance/free sources from getting us IP-banned) ───
YFINANCE_BATCH_SIZE = int(os.getenv("YFINANCE_BATCH_SIZE", "50"))
YFINANCE_SLEEP_SEC = float(os.getenv("YFINANCE_SLEEP_SEC", "0.5"))

# ── Admin ────────────────────────────────────────────────────────────────────
# Super-admin hardcoded here, same pattern as the other tusa bots — not env-var
# driven (others get added via the bot itself once that exists).
ADMIN_USER_IDS = {671071896} | {
    int(x) for x in os.getenv("ADMIN_USER_IDS", "").split(",") if x.strip().isdigit()
}
