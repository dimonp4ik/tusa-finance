"""
Tusa Finance — signal-only investing bot (weeks horizon).

Three independent scan cycles:
  1. Dividend screener — once/week (DIV_SCAN_DAY_OF_WEEK, SCAN_HOUR_UTC).
     Fundamentals move on a quarterly cadence at best, and .info is a slow
     one-request-per-symbol call — daily was noise for data that barely
     changes (see DIV_SCAN_BATCH_PER_DAY for the rotating-slice coverage).
  2. Momentum + unusual-volume screener — every MOMENTUM_SCAN_INTERVAL_HOURS.
     Unusual-volume specifically exists to catch a move the same day it
     starts, so it benefits from freshness; momentum rides along since it
     shares the same bulk OHLCV pull.
  3. News-catalyst scanner — every NEWS_SCAN_INTERVAL_MINUTES (default 5).
     The only screener that can flag something BEFORE it shows up in price —
     momentum/unusual-volume both require the move to have already started.

AI judge (Claude/DeepSeek) SKIP verdicts are hidden from Telegram entirely,
not shown with a ⛔ tag — see HIDE_JUDGE_SKIPPED / _drop_hidden_skips.

No autotrading yet — Trading212 (stocks) and OKX (crypto, spot or 2-5x
X-Perp per-candidate) execution come in a later phase.
"""
import logging
import sys
import os
import time

from flask import Flask
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (
    SCAN_HOUR_UTC, DIV_SCAN_DAY_OF_WEEK, MOMENTUM_SCAN_INTERVAL_HOURS,
    NEWS_SCAN_INTERVAL_MINUTES, NEWS_LOOKBACK_MINUTES, NEWS_SYMBOL_COOLDOWN_HOURS,
    FUNDING_RATE_WARN, HIDE_JUDGE_SKIPPED,
)
from src import db
from src import universe_stocks, universe_crypto
from src import dividend_screener, momentum_screener, news_scanner
from src import insider_buying
from src import market_context, ai_judge
from src import telegram_notifier
from src import telegram_bot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
_log = logging.getLogger(__name__)

app = Flask(__name__)
_scheduler = None


@app.route("/")
def health():
    return {"status": "ok", "service": "tusa-finance"}


def _enrich_insider(rows: list[dict]) -> None:
    """Attaches row['insider_purchases'] in place. US stocks only (SEC EDGAR
    doesn't cover crypto) — only called on already-shortlisted NEW candidates,
    not the full universe."""
    for r in rows:
        if r.get("asset_type") == "crypto":
            continue
        try:
            r["insider_purchases"] = insider_buying.get_recent_insider_purchases(r["symbol"])
        except Exception as e:
            _log.debug("insider lookup failed for %s: %s", r["symbol"], e)
            r["insider_purchases"] = []


def _drop_hidden_skips(rows: list[dict]) -> list[dict]:
    """User doesn't want rejected setups cluttering the chat — a SKIP verdict
    from the AI judge means "don't act on this", so hide it instead of
    showing it with a ⛔ tag. Rows without a judge verdict (judge disabled,
    or news candidates which don't use this judge) pass through unchanged."""
    if not HIDE_JUDGE_SKIPPED:
        return rows
    return [r for r in rows if r.get("judge", {}).get("verdict") != "SKIP"]


def _enrich_judge(rows: list[dict], kind: str) -> None:
    """Optional AI second opinion (Claude if CLAUDE_API_KEY is set, DeepSeek
    as fallback) — attaches row['judge'] in place and logs verdicts for
    later hit-rate analysis."""
    if not ai_judge.is_enabled() or not rows:
        return
    verdicts = ai_judge.judge_candidates(rows)
    if not verdicts:
        return
    db.save_judge_verdicts(kind, verdicts)
    provider = ai_judge.provider_name()
    for r in rows:
        if r["symbol"] in verdicts:
            r["judge"] = {**verdicts[r["symbol"]], "provider": provider}


def run_dividend_scan():
    _log.info("=== dividend scan start ===")
    try:
        stock_universe = universe_stocks.get_stock_universe()
        dividend_hits = dividend_screener.screen(stock_universe)
        if dividend_hits:
            db.save_dividend_candidates(dividend_hits)

        new_dividend = [r for r in dividend_hits if not db.was_recently_sent(r["symbol"], "dividend")]
        _enrich_insider(new_dividend)
        _enrich_judge(new_dividend, "dividend")
        # SKIP'd candidates are neither shown nor marked-sent — they can
        # resurface on a later scan if the judge's read on them changes.
        to_send = _drop_hidden_skips(new_dividend)
        telegram_notifier.send_digest(to_send, [], [])
        for r in to_send:
            db.mark_sent(r["symbol"], "dividend")

        _log.info("dividend scan done: %d new alerts sent", len(new_dividend))
    except Exception as e:
        _log.exception("dividend scan failed: %s", e)
        telegram_notifier.send_message(f"⚠️ Дивидендный скан упал с ошибкой: {e}")
    _log.info("=== dividend scan end ===")


def run_momentum_scan():
    _log.info("=== momentum scan start ===")
    try:
        stock_universe = universe_stocks.get_stock_universe()
        # Full OKX EU universe, not just top-by-volume: backtest showed
        # alts have a negative EXPECTED edge on average, but the user wants
        # the wide net anyway — a real few-day 2-5x on a small-cap coin is
        # exactly the outlier a "top liquid pairs only" filter would hide.
        # This is a lottery-ticket bet, not an expectancy play — small size
        # per pick. Low-liquidity crypto hits are tagged in Telegram.
        crypto_momentum_universe = universe_crypto.get_crypto_universe()
        _log.info("universe: %d stocks, %d crypto pairs (full)",
                   len(stock_universe), len(crypto_momentum_universe))

        # Shared bulk OHLCV pull — screen_stocks and screen_unusual_volume
        # both read off it instead of each re-downloading the same data.
        stock_history = momentum_screener.fetch_stock_history(stock_universe)

        momentum_stock_hits = momentum_screener.screen_stocks(stock_universe, history=stock_history)
        momentum_crypto_hits = momentum_screener.screen_crypto(crypto_momentum_universe)
        unusual_volume_hits = momentum_screener.screen_unusual_volume(stock_universe, history=stock_history)

        # One short combined digest (user: too many signals per scan) —
        # top-5 by score with ≥1 stock and ≥1 crypto guaranteed when both exist.
        momentum_hits = momentum_screener.select_top_mixed(momentum_stock_hits, momentum_crypto_hits)

        # Crowded-longs check: very positive perp funding on a leveraged
        # suggestion → downgrade to spot-only (free OKX public endpoint).
        for r in momentum_hits:
            if r["asset_type"] == "crypto" and r.get("suggest_leverage"):
                rate = market_context.get_funding_rate(r["symbol"])
                if rate is not None and rate > FUNDING_RATE_WARN:
                    r["suggest_leverage"] = 0
                    r["funding_hot"] = True

        if momentum_hits:
            db.save_momentum_candidates(
                [{k: v for k, v in r.items() if k not in ("funding_hot",)} for r in momentum_hits])
        if unusual_volume_hits:
            db.save_unusual_volume_candidates(unusual_volume_hits)

        new_momentum = [r for r in momentum_hits if not db.was_recently_sent(r["symbol"], "momentum")]
        new_unusual = [r for r in unusual_volume_hits if not db.was_recently_sent(r["symbol"], "unusual_volume")]

        # SEC lookups only on what's actually about to be sent.
        _enrich_insider(new_momentum)
        _enrich_insider(new_unusual)
        _enrich_judge(new_momentum, "momentum")
        _enrich_judge(new_unusual, "unusual_volume")

        # Market-context header: SPY regime + crypto Fear & Greed (both free).
        context_lines = market_context.context_header_lines()

        momentum_to_send = _drop_hidden_skips(new_momentum)
        unusual_to_send = _drop_hidden_skips(new_unusual)
        telegram_notifier.send_digest([], momentum_to_send, unusual_to_send, context_lines=context_lines)

        for r in momentum_to_send:
            db.mark_sent(r["symbol"], "momentum")
        for r in unusual_to_send:
            db.mark_sent(r["symbol"], "unusual_volume")

        _log.info("momentum scan done: %d new momentum, %d new unusual-volume alerts sent",
                   len(momentum_to_send), len(unusual_to_send))
    except Exception as e:
        _log.exception("momentum scan failed: %s", e)
        telegram_notifier.send_message(f"⚠️ Моментум-скан упал с ошибкой: {e}")
    _log.info("=== momentum scan end ===")


def run_news_scan():
    """Every NEWS_SCAN_INTERVAL_MINUTES — no-ops cheaply (no network calls at
    all) when GROQ_API_KEY isn't set. No Telegram error messages on failure:
    this runs every few minutes, a transient RSS hiccup would spam the chat."""
    try:
        if not news_scanner.is_enabled():
            return
        stock_universe = universe_stocks.get_stock_universe()
        crypto_universe = universe_crypto.get_crypto_universe()
        hits = news_scanner.scan(stock_universe, crypto_universe, NEWS_LOOKBACK_MINUTES)
        if not hits:
            return

        db.save_news_candidates(hits)

        fresh = []
        for r in hits:
            if db.is_headline_seen(r["headline_hash"]):
                continue
            if db.was_recently_sent(r["symbol"], "news", within_days=NEWS_SYMBOL_COOLDOWN_HOURS / 24):
                db.mark_headline_seen(r["headline_hash"], r["symbol"])
                continue
            fresh.append(r)

        if fresh:
            _enrich_insider(fresh)
            telegram_notifier.send_news_digest(fresh)
            for r in fresh:
                db.mark_headline_seen(r["headline_hash"], r["symbol"])
                db.mark_sent(r["symbol"], "news")
            _log.info("news scan: %d new catalyst alerts sent", len(fresh))

        db.prune_old_headlines()
    except Exception as e:
        _log.warning("news scan failed (non-fatal): %s", e)


def start_bot():
    global _scheduler
    _log.info("Starting Tusa Finance Bot...")
    db.init_db()
    if _scheduler is not None:
        return

    # Dedup guard: only send once per 60s per container (prevents double
    # message during Railway zero-downtime deploys where old + new
    # instances briefly overlap) — same pattern as the other tusa bots.
    _flag = "/tmp/tusa_finance_started"
    try:
        skip = os.path.exists(_flag) and time.time() - os.path.getmtime(_flag) < 60
        if not skip:
            open(_flag, "w").close()
            news_note = (
                f"Новостной катализатор — каждые {NEWS_SCAN_INTERVAL_MINUTES}мин."
                if news_scanner.is_enabled() else
                "Новостной катализатор — выключен (нет GROQ_API_KEY)."
            )
            telegram_notifier.send_message(
                "🤖 <b>Tusa Finance Bot Online</b>\n"
                f"Дивиденды — раз в неделю ({DIV_SCAN_DAY_OF_WEEK}, {SCAN_HOUR_UTC:02d}:00 UTC). "
                f"Моментум + аномальный объём — каждые {MOMENTUM_SCAN_INTERVAL_HOURS}ч. "
                f"{news_note} "
                "Инсайдерские покупки — на всех кандидатах."
            )
    except Exception as e:
        _log.warning("Could not send startup message: %s", e)

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(run_dividend_scan, CronTrigger(day_of_week=DIV_SCAN_DAY_OF_WEEK, hour=SCAN_HOUR_UTC, minute=0))
    _scheduler.add_job(run_momentum_scan, IntervalTrigger(hours=MOMENTUM_SCAN_INTERVAL_HOURS))
    _scheduler.add_job(run_news_scan, IntervalTrigger(minutes=NEWS_SCAN_INTERVAL_MINUTES))
    _scheduler.start()
    _log.info("scheduler started — dividends %s %02d:00 UTC, momentum every %dh, news every %dmin",
               DIV_SCAN_DAY_OF_WEEK, SCAN_HOUR_UTC, MOMENTUM_SCAN_INTERVAL_HOURS, NEWS_SCAN_INTERVAL_MINUTES)

    telegram_bot.start_polling()


start_bot()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
