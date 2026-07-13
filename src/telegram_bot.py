"""
Telegram bot — persistent bottom-bar menu + on-demand digest buttons.

Long-polling (not webhook): this service runs "Unexposed" on Railway (no
public domain needed), simplest thing that works for a single-user/small
group bot. Update offset is persisted in bot_state so a redeploy doesn't
replay old messages.
"""
import logging
import sys
import os
import threading
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import TELEGRAM_TOKEN, ADMIN_USER_IDS
from src import db, market_hours, telegram_notifier

_log = logging.getLogger(__name__)
API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

_USER_KB = {
    "keyboard": [
        [{"text": "💰 Дивиденды"}, {"text": "🚀 Моментум"}],
        [{"text": "🔥 Аномальный объём"}, {"text": "🕐 Рынок США"}],
        [{"text": "❓ Помощь"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}
_ADMIN_KB = {
    "keyboard": _USER_KB["keyboard"] + [[{"text": "🔄 Запустить скан сейчас"}]],
    "resize_keyboard": True,
    "is_persistent": True,
}

_HELP_TEXT = (
    "🤖 <b>Tusa Finance</b> — инвест-скринер на недели\n\n"
    "💰 Дивиденды — последний найденный список дивидендных акций\n"
    "🚀 Моментум — акции + крипта с сильным ростом за 4 недели\n"
    "🔥 Аномальный объём — движение началось сегодня\n"
    "🕐 Рынок США — открыт/закрыт сейчас\n\n"
    "Автоматический скан всего рынка — раз в сутки. Бот только шлёт сигналы, "
    "покупки делаешь сам (акции — Trading212, крипта — OKX EU)."
)


def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS


def _send(chat_id: int, text: str, reply_markup: dict = None) -> None:
    try:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        requests.post(f"{API}/sendMessage", json=payload, timeout=15)
    except Exception as e:
        _log.warning("send failed: %s", e)


def _handle_dividends(chat_id: int) -> None:
    rows = db.get_latest_dividend_candidates()
    if not rows:
        _send(chat_id, "📭 Пока нет данных — первый скан ещё не прошёл.")
        return
    blocks = [telegram_notifier.format_dividend_candidate(r) for r in rows]
    _send(chat_id, "💰 <b>ДИВИДЕНДНЫЕ КАНДИДАТЫ (последний скан)</b>\n\n" + "\n\n".join(blocks))


def _handle_momentum(chat_id: int) -> None:
    rows = db.get_latest_momentum_candidates()
    if not rows:
        _send(chat_id, "📭 Пока нет данных — первый скан ещё не прошёл.")
        return
    has_crypto = any(r["asset_type"] == "crypto" for r in rows)
    header = "🚀 <b>МОМЕНТУМ (последний скан)</b>"
    if has_crypto:
        header += "\n⚠️ Крипто-часть — экспериментально, бектест edge под вопросом"
    blocks = [telegram_notifier.format_momentum_candidate(r) for r in rows]
    _send(chat_id, header + "\n\n" + "\n\n".join(blocks))


def _handle_unusual_volume(chat_id: int) -> None:
    rows = db.get_latest_unusual_volume_candidates()
    if not rows:
        _send(chat_id, "📭 Пока нет данных — первый скан ещё не прошёл.")
        return
    blocks = [telegram_notifier.format_unusual_volume_candidate(r) for r in rows]
    _send(chat_id, "🔥 <b>АНОМАЛЬНЫЙ ОБЪЁМ (последний скан)</b>\n\n" + "\n\n".join(blocks))


def _handle_market_status(chat_id: int) -> None:
    _send(chat_id, market_hours.status_text())


def _handle_manual_scan(chat_id: int, user_id: int) -> None:
    if not _is_admin(user_id):
        _send(chat_id, "⛔ Только для админа.")
        return
    _send(chat_id, "🔄 Запускаю скан рынка — это займёт несколько минут...")

    def _run():
        from main import run_scan   # deferred import: avoids circular import at module load
        run_scan()

    threading.Thread(target=_run, daemon=True).start()


def _handle_update(update: dict) -> None:
    msg = update.get("message")
    if not msg:
        return
    chat_id = msg["chat"]["id"]
    user_id = msg.get("from", {}).get("id", 0)
    text = (msg.get("text") or "").strip()

    if text == "/start":
        kb = _ADMIN_KB if _is_admin(user_id) else _USER_KB
        tag = "\n👑 Ты супер-админ — доступен ручной запуск скана." if _is_admin(user_id) else ""
        _send(chat_id, "✅ Меню активировано." + tag, reply_markup=kb)
    elif text == "💰 Дивиденды":
        _handle_dividends(chat_id)
    elif text == "🚀 Моментум":
        _handle_momentum(chat_id)
    elif text == "🔥 Аномальный объём":
        _handle_unusual_volume(chat_id)
    elif text == "🕐 Рынок США":
        _handle_market_status(chat_id)
    elif text == "❓ Помощь":
        _send(chat_id, _HELP_TEXT)
    elif text == "🔄 Запустить скан сейчас":
        _handle_manual_scan(chat_id, user_id)


def _poll_loop() -> None:
    offset = int(db.get_state("tg_update_offset", "0"))
    _log.info("Telegram polling started (offset=%d)", offset)
    while True:
        try:
            resp = requests.get(
                f"{API}/getUpdates",
                params={"offset": offset, "timeout": 30},
                timeout=40,
            )
            updates = resp.json().get("result", [])
            for u in updates:
                offset = u["update_id"] + 1
                try:
                    _handle_update(u)
                except Exception as e:
                    _log.warning("update handling failed: %s", e)
            if updates:
                db.set_state("tg_update_offset", str(offset))
        except Exception as e:
            _log.warning("poll loop error: %s", e)
            time.sleep(5)


def start_polling() -> None:
    if not TELEGRAM_TOKEN:
        _log.warning("TELEGRAM_TOKEN not set — polling disabled")
        return
    threading.Thread(target=_poll_loop, daemon=True).start()
