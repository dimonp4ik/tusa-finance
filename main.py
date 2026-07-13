"""
Tusa Finance — signal-only investing bot (weeks horizon).

Two independent scan cycles:
  1. Dividend screener — once/day (SCAN_HOUR_UTC). Fundamentals don't move
     intraday, and .info is a slow one-request-per-symbol call, so there's
     no upside to running it more often (see DIV_SCAN_BATCH_PER_DAY).
  2. Momentum + unusual-volume screener — every MOMENTUM_SCAN_INTERVAL_HOURS.
     Unusual-volume specifically exists to catch a move the same day it
     starts, so it benefits from freshness; momentum rides along since it
     shares the same bulk OHLCV pull.

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
from config import SCAN_HOUR_UTC, MOMENTUM_SCAN_INTERVAL_HOURS
from src import db
from src import universe_stocks, universe_crypto
from src import dividend_screener, momentum_screener
from src import insider_buying
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


def run_dividend_scan():
    _log.info("=== dividend scan start ===")
    try:
        stock_universe = universe_stocks.get_stock_universe()
        dividend_hits = dividend_screener.screen(stock_universe)
        if dividend_hits:
            db.save_dividend_candidates(dividend_hits)

        new_dividend = [r for r in dividend_hits if not db.was_recently_sent(r["symbol"], "dividend")]
        _enrich_insider(new_dividend)
        telegram_notifier.send_digest(new_dividend, [], [])
        for r in new_dividend:
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
        momentum_hits = momentum_stock_hits + momentum_crypto_hits
        unusual_volume_hits = momentum_screener.screen_unusual_volume(stock_universe, history=stock_history)

        if momentum_hits:
            db.save_momentum_candidates(momentum_hits)
        if unusual_volume_hits:
            db.save_unusual_volume_candidates(unusual_volume_hits)

        new_momentum = [r for r in momentum_hits if not db.was_recently_sent(r["symbol"], "momentum")]
        new_unusual = [r for r in unusual_volume_hits if not db.was_recently_sent(r["symbol"], "unusual_volume")]

        # SEC lookups only on what's actually about to be sent.
        _enrich_insider(new_momentum)
        _enrich_insider(new_unusual)

        telegram_notifier.send_digest([], new_momentum, new_unusual)

        for r in new_momentum:
            db.mark_sent(r["symbol"], "momentum")
        for r in new_unusual:
            db.mark_sent(r["symbol"], "unusual_volume")

        _log.info("momentum scan done: %d new momentum, %d new unusual-volume alerts sent",
                   len(new_momentum), len(new_unusual))
    except Exception as e:
        _log.exception("momentum scan failed: %s", e)
        telegram_notifier.send_message(f"⚠️ Моментум-скан упал с ошибкой: {e}")
    _log.info("=== momentum scan end ===")


def run_scan():
    """Full manual refresh — both cycles back to back (admin button)."""
    run_dividend_scan()
    run_momentum_scan()


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
            telegram_notifier.send_message(
                "🤖 <b>Tusa Finance Bot Online</b>\n"
                f"Дивиденды — раз в сутки ({SCAN_HOUR_UTC:02d}:00 UTC). "
                f"Моментум + аномальный объём — каждые {MOMENTUM_SCAN_INTERVAL_HOURS}ч. "
                "Инсайдерские покупки — на всех кандидатах."
            )
    except Exception as e:
        _log.warning("Could not send startup message: %s", e)

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(run_dividend_scan, CronTrigger(hour=SCAN_HOUR_UTC, minute=0))
    _scheduler.add_job(run_momentum_scan, IntervalTrigger(hours=MOMENTUM_SCAN_INTERVAL_HOURS))
    _scheduler.start()
    _log.info("scheduler started — dividends daily at %02d:00 UTC, momentum every %dh",
               SCAN_HOUR_UTC, MOMENTUM_SCAN_INTERVAL_HOURS)

    telegram_bot.start_polling()


start_bot()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
