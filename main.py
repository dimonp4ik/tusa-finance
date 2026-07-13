"""
Tusa Finance — signal-only investing bot (weeks horizon).

Daily cycle:
  1. Pull full universes (US stocks via NASDAQ Trader listing, crypto via
     OKX EU public instruments — both free, no API key).
  2. Dividend screener (rotating daily slice, pure fundamentals) +
     momentum screener (full universe, pure price/volume/RSI).
  3. Skip anything alerted in the last 7 days, send the rest to Telegram.

No autotrading yet — Trading212 (stocks) and OKX (crypto, spot or 2-5x
X-Perp per-candidate) execution come in a later phase.
"""
import logging
import sys
import os

from flask import Flask
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import SCAN_HOUR_UTC, CRYPTO_MOMENTUM_UNIVERSE_SIZE
from src import db
from src import universe_stocks, universe_crypto
from src import dividend_screener, momentum_screener
from src import telegram_notifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
_log = logging.getLogger(__name__)

app = Flask(__name__)
_scheduler = None


@app.route("/")
def health():
    return {"status": "ok", "service": "tusa-finance"}


def run_scan():
    _log.info("=== daily scan start ===")
    try:
        stock_universe = universe_stocks.get_stock_universe()
        # Momentum-chasing alts loses money (backtested) — restrict crypto
        # momentum to the top-N most liquid pairs by live 24h volume.
        crypto_momentum_universe = universe_crypto.get_top_crypto_by_volume(CRYPTO_MOMENTUM_UNIVERSE_SIZE)
        _log.info("universe: %d stocks, %d crypto pairs (top by volume)",
                   len(stock_universe), len(crypto_momentum_universe))

        dividend_hits = dividend_screener.screen(stock_universe)
        momentum_stock_hits = momentum_screener.screen_stocks(stock_universe)
        momentum_crypto_hits = momentum_screener.screen_crypto(crypto_momentum_universe)
        momentum_hits = momentum_stock_hits + momentum_crypto_hits

        if dividend_hits:
            db.save_dividend_candidates(dividend_hits)
        if momentum_hits:
            db.save_momentum_candidates(momentum_hits)

        new_dividend = [r for r in dividend_hits if not db.was_recently_sent(r["symbol"], "dividend")]
        new_momentum = [r for r in momentum_hits if not db.was_recently_sent(r["symbol"], "momentum")]

        telegram_notifier.send_digest(new_dividend, new_momentum)

        for r in new_dividend:
            db.mark_sent(r["symbol"], "dividend")
        for r in new_momentum:
            db.mark_sent(r["symbol"], "momentum")

        _log.info("scan done: %d new dividend, %d new momentum alerts sent",
                   len(new_dividend), len(new_momentum))
    except Exception as e:
        _log.exception("scan failed: %s", e)
        telegram_notifier.send_message(f"⚠️ Скан упал с ошибкой: {e}")
    _log.info("=== daily scan end ===")


def start_bot():
    global _scheduler
    db.init_db()
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(run_scan, CronTrigger(hour=SCAN_HOUR_UTC, minute=0))
    _scheduler.start()
    _log.info("scheduler started — daily scan at %02d:00 UTC", SCAN_HOUR_UTC)


start_bot()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
