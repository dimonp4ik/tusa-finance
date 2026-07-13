"""Telegram alert formatting/sending for Tusa Finance."""
import logging
import sys
import os

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

_log = logging.getLogger(__name__)
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


def send_message(text: str, chat_id: str = None) -> bool:
    if not TELEGRAM_TOKEN:
        _log.warning("TELEGRAM_TOKEN not set — message not sent:\n%s", text)
        return False
    try:
        resp = requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={
                "chat_id": chat_id or TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            _log.error("Telegram send failed: %s %s", resp.status_code, resp.text)
            return False
        return True
    except Exception as e:
        _log.error("Telegram send exception: %s", e)
        return False


def format_dividend_candidate(row: dict) -> str:
    return (
        f"💰 <b>{row['symbol']}</b> — {row.get('name') or ''}\n"
        f"Дивдоходность: {row['yield_pct']:.2f}%\n"
        f"Payout ratio: {row['payout_ratio'] * 100:.0f}%\n"
        f"Капитализация: ${row['market_cap'] / 1e9:.2f}B\n"
        f"Платит дивиденды: {row['years_history']}+ лет\n"
        f"Score: {row['score']:.0f}/100"
    )


def format_momentum_candidate(row: dict) -> str:
    kind = "🪙 Крипта" if row["asset_type"] == "crypto" else "📈 Акция"
    lev_note = ""
    if row["asset_type"] == "crypto":
        lev_note = (
            "\n⚡ Можно с плечом 2-5x (X-Perp)"
            if row["suggest_leverage"]
            else "\n🔹 Только spot (плечо не рекомендуется)"
        )
    return (
        f"{kind} <b>{row['symbol']}</b>\n"
        f"Рост за {row.get('lookback_weeks', 4)} нед: {row['price_change_pct']:+.1f}%\n"
        f"Объём: ${row['volume_usd'] / 1e6:.1f}M/день\n"
        f"RSI: {row['rsi']:.0f}\n"
        f"Score: {row['score']:.0f}/100{lev_note}"
    )


def send_digest(dividend_rows: list[dict], momentum_rows: list[dict]) -> None:
    if not dividend_rows and not momentum_rows:
        send_message("📭 Сегодня новых кандидатов нет.")
        return

    if dividend_rows:
        blocks = [format_dividend_candidate(r) for r in dividend_rows]
        send_message("💰 <b>ДИВИДЕНДНЫЕ КАНДИДАТЫ</b>\n\n" + "\n\n".join(blocks))

    if momentum_rows:
        blocks = [format_momentum_candidate(r) for r in momentum_rows]
        send_message("🚀 <b>МОМЕНТУМ / КАНДИДАТЫ НА ИКС</b>\n\n" + "\n\n".join(blocks))
