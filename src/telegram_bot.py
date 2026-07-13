"""
Telegram bot — persistent bottom-bar menu + inline admin panel.

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
    "keyboard": _USER_KB["keyboard"] + [[{"text": "🛠 Админ панель"}]],
    "resize_keyboard": True,
    "is_persistent": True,
}

_HELP_TEXT = (
    "🤖 <b>Tusa Finance</b> — инвест-скринер на недели\n\n"
    "💰 Дивиденды — последний найденный список дивидендных акций\n"
    "🚀 Моментум — акции + крипта с сильным ростом за 4 недели\n"
    "🔥 Аномальный объём — движение началось сегодня\n"
    "🕐 Рынок США — открыт/закрыт сейчас\n\n"
    "Дивиденды сканятся раз в сутки, моментум/объём — каждые несколько часов. "
    "Бот только шлёт сигналы, покупки делаешь сам (акции — Trading212, крипта — OKX EU)."
)

# user_id -> True while we're waiting for them to send a Telegram ID to add as admin
_pending_add_admin: dict = {}


def _is_super_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS


def _is_admin(user_id: int) -> bool:
    return _is_super_admin(user_id) or db.is_dynamic_admin(user_id)


def _send(chat_id: int, text: str, reply_markup: dict = None) -> int | None:
    try:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        resp = requests.post(f"{API}/sendMessage", json=payload, timeout=15)
        return resp.json().get("result", {}).get("message_id")
    except Exception as e:
        _log.warning("send failed: %s", e)
        return None


def _edit(chat_id: int, message_id: int, text: str, reply_markup: dict = None) -> None:
    try:
        payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        requests.post(f"{API}/editMessageText", json=payload, timeout=15)
    except Exception as e:
        _log.warning("edit failed: %s", e)


def _answer_callback(callback_id: str, text: str = "") -> None:
    try:
        requests.post(f"{API}/answerCallbackQuery",
                       json={"callback_query_id": callback_id, "text": text}, timeout=10)
    except Exception as e:
        _log.debug("answerCallbackQuery failed: %s", e)


# ── On-demand digest buttons ─────────────────────────────────────────────────

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


# ── Admin panel ───────────────────────────────────────────────────────────────

_PANEL_ROOT_KB = {"inline_keyboard": [
    [{"text": "📊 Статус", "callback_data": "af_status"}],
    [{"text": "🔄 Сканы", "callback_data": "af_scans"}],
    [{"text": "👮 Админы", "callback_data": "af_admins"}],
    [{"text": "✖️ Закрыть", "callback_data": "af_close"}],
]}
_BACK_ROW = [{"text": "« Назад", "callback_data": "af_root"}]


def _fmt_ago(ts: float | None) -> str:
    if ts is None:
        return "ещё не было"
    delta = time.time() - ts
    h = int(delta // 3600)
    m = int((delta % 3600) // 60)
    return f"{h}ч {m}м назад" if h else f"{m}м назад"


def _panel_status_text() -> str:
    s = db.get_scan_summary()
    return (
        "📊 <b>Статус сканов</b>\n\n"
        f"💰 Дивиденды: {s['dividend']['count']} канд. — {_fmt_ago(s['dividend']['last_at'])}\n"
        f"🚀 Моментум: {s['momentum']['count']} канд. — {_fmt_ago(s['momentum']['last_at'])}\n"
        f"🔥 Аномальный объём: {s['unusual_volume']['count']} канд. — {_fmt_ago(s['unusual_volume']['last_at'])}"
    )


def _panel_admins_text_kb(user_id: int) -> tuple:
    dynamic = db.get_dynamic_admins()
    lines = ["👮 <b>Управление админами</b>\n", "🔒 <b>Супер-админы (в коде):</b>"]
    for aid in sorted(ADMIN_USER_IDS):
        lines.append(f"  <code>{aid}</code>")
    if dynamic:
        lines.append("\n➕ <b>Добавленные админы:</b>")
        for a in dynamic:
            name = a.get("first_name") or ""
            uname = f" @{a['username']}" if a.get("username") else ""
            lines.append(f"  • {name}{uname} <code>{a['user_id']}</code>")
    else:
        lines.append("\n<i>Добавленных админов нет.</i>")

    kb_rows = []
    if _is_super_admin(user_id):
        for a in dynamic:
            label = a.get("first_name") or str(a["user_id"])
            kb_rows.append([{"text": f"❌ Удалить {label}", "callback_data": f"af_rm_admin_{a['user_id']}"}])
        kb_rows.append([{"text": "➕ Добавить администратора", "callback_data": "af_add_admin"}])
    kb_rows.append(_BACK_ROW)
    return "\n".join(lines), {"inline_keyboard": kb_rows}


_SCANS_KB = {"inline_keyboard": [
    [{"text": "💰 Скан дивидендов", "callback_data": "af_scan_div"}],
    [{"text": "🚀 Скан моментума", "callback_data": "af_scan_mom"}],
    [{"text": "🔁 Оба скана", "callback_data": "af_scan_all"}],
    _BACK_ROW,
]}


def _handle_callback(update: dict) -> None:
    cb = update["callback_query"]
    data = cb.get("data", "")
    user_id = cb.get("from", {}).get("id", 0)
    msg = cb.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    message_id = msg.get("message_id")
    _answer_callback(cb["id"])

    if not _is_admin(user_id):
        _edit(chat_id, message_id, "⛔ Нет доступа.")
        return

    if data == "af_root":
        _edit(chat_id, message_id, "🛠 <b>Tusa Finance — Admin</b>\nВыбери раздел:", _PANEL_ROOT_KB)

    elif data == "af_status":
        _edit(chat_id, message_id, _panel_status_text(), {"inline_keyboard": [_BACK_ROW]})

    elif data == "af_scans":
        _edit(chat_id, message_id, "🔄 <b>Ручной запуск скана</b>\nЗаймёт несколько минут.", _SCANS_KB)

    elif data in ("af_scan_div", "af_scan_mom", "af_scan_all"):
        _edit(chat_id, message_id, "🔄 Запускаю...", {"inline_keyboard": [_BACK_ROW]})
        _send(chat_id, "🔄 Скан запущен, пришлю результат отдельным сообщением.")

        def _run():
            from main import run_dividend_scan, run_momentum_scan
            if data in ("af_scan_div", "af_scan_all"):
                run_dividend_scan()
            if data in ("af_scan_mom", "af_scan_all"):
                run_momentum_scan()

        threading.Thread(target=_run, daemon=True).start()

    elif data == "af_admins":
        text, kb = _panel_admins_text_kb(user_id)
        _edit(chat_id, message_id, text, kb)

    elif data == "af_add_admin":
        if not _is_super_admin(user_id):
            _edit(chat_id, message_id, "⛔ Только супер-админ может добавлять.")
            return
        _pending_add_admin[user_id] = True
        _edit(chat_id, message_id, "Отправь Telegram ID нового админа следующим сообщением.\n"
                                    "Узнать ID можно через @userinfobot.",
              {"inline_keyboard": [_BACK_ROW]})

    elif data.startswith("af_rm_admin_"):
        if not _is_super_admin(user_id):
            _edit(chat_id, message_id, "⛔ Только супер-админ может удалять.")
            return
        rm_id = int(data[len("af_rm_admin_"):])
        db.remove_dynamic_admin(rm_id)
        text, kb = _panel_admins_text_kb(user_id)
        _edit(chat_id, message_id, f"✅ Удалено: <code>{rm_id}</code>\n\n{text}", kb)

    elif data == "af_close":
        _edit(chat_id, message_id, "🛠 Панель закрыта. /start чтобы открыть снова.")


def _handle_pending_add_admin(msg: dict) -> bool:
    user_id = msg.get("from", {}).get("id", 0)
    chat_id = msg["chat"]["id"]
    if user_id not in _pending_add_admin:
        return False
    text = (msg.get("text") or "").strip()
    _pending_add_admin.pop(user_id, None)
    if not text.lstrip("-").isdigit():
        _send(chat_id, f"⚠️ Не похоже на Telegram ID: {text}")
        return True
    new_id = int(text)
    db.add_dynamic_admin(new_id, added_by=user_id)
    _send(chat_id, f"✅ Добавлен админ: <code>{new_id}</code>")
    return True


def _handle_message(msg: dict) -> None:
    chat_id = msg["chat"]["id"]
    user_id = msg.get("from", {}).get("id", 0)
    text = (msg.get("text") or "").strip()

    if _handle_pending_add_admin(msg):
        return

    if text == "/start":
        kb = _ADMIN_KB if _is_admin(user_id) else _USER_KB
        tag = "\n👑 Тебе доступна кнопка «🛠 Админ панель»." if _is_admin(user_id) else ""
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
    elif text == "🛠 Админ панель":
        if _is_admin(user_id):
            _send(chat_id, "🛠 <b>Tusa Finance — Admin</b>\nВыбери раздел:", _PANEL_ROOT_KB)
        else:
            _send(chat_id, "⛔ Нет доступа.")


def _handle_update(update: dict) -> None:
    if "callback_query" in update:
        _handle_callback(update)
        return
    msg = update.get("message")
    if msg:
        _handle_message(msg)


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
