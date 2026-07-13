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


def _judge_tag(row: dict) -> str:
    """Second-opinion line — main.py attaches row['judge'] when the optional
    AI judge is enabled (CLAUDE_API_KEY or DEEPSEEK_API_KEY set)."""
    j = row.get("judge")
    if not j:
        return ""
    icon = "✅" if j.get("verdict") == "BUY" else "⛔"
    provider = j.get("provider") or "AI"
    return f"\n🤖 {provider}: {icon} {j.get('verdict', '?')} — {j.get('reason', '')}"


def _insider_tag(row: dict) -> str:
    """Common enrichment line — main.py attaches row['insider_purchases']
    (SEC EDGAR Form 4 open-market buys) before formatting, US stocks only."""
    purchases = row.get("insider_purchases") or []
    if not purchases:
        return ""
    total = sum(p["value_usd"] for p in purchases)
    names = ", ".join(sorted({p["insider"] for p in purchases if p.get("insider")})[:2])
    return f"\n💼 Инсайдер купил: ${total:,.0f} ({names})" if names else f"\n💼 Инсайдер купил: ${total:,.0f}"


def format_dividend_candidate(row: dict) -> str:
    return (
        f"💰 <b>{row['symbol']}</b> — {row.get('name') or ''}\n"
        f"Дивдоходность: {row['yield_pct']:.2f}%\n"
        f"Payout ratio: {row['payout_ratio'] * 100:.0f}%\n"
        f"Капитализация: ${row['market_cap'] / 1e9:.2f}B\n"
        f"Платит дивиденды: {row['years_history']}+ лет\n"
        f"Score: {row['score']:.0f}/100"
        f"{_insider_tag(row)}"
        f"{_judge_tag(row)}"
    )


def format_momentum_candidate(row: dict) -> str:
    from config import CRYPTO_LOW_LIQUIDITY_USD
    kind = "🪙 Крипта" if row["asset_type"] == "crypto" else "📈 Акция"
    lev_note = ""
    if row["asset_type"] == "crypto":
        lev_note = (
            "\n⚡ Можно с плечом 2-5x (X-Perp)"
            if row["suggest_leverage"]
            else "\n🔹 Только spot (плечо не рекомендуется)"
        )
        if row["volume_usd"] < CRYPTO_LOW_LIQUIDITY_USD:
            lev_note += "\n🎰 Низкая ликвидность — лотерейный тикет, размер позиции маленький"
        if row.get("funding_hot"):
            lev_note += "\n🌡 Фандинг перегрет — лонги переполнены, вход рискованнее"
    return (
        f"{kind} <b>{row['symbol']}</b>\n"
        f"Рост за {row.get('lookback_weeks', 4)} нед: {row['price_change_pct']:+.1f}%\n"
        f"Объём: ${row['volume_usd'] / 1e6:.1f}M/день\n"
        f"RSI: {row['rsi']:.0f}\n"
        f"Score: {row['score']:.0f}/100{lev_note}"
        f"{_insider_tag(row)}"
        f"{_judge_tag(row)}"
    )


def format_unusual_volume_candidate(row: dict) -> str:
    return (
        f"🔥 <b>{row['symbol']}</b>\n"
        f"Объём сегодня: x{row['relative_volume']:.1f} от среднего (${row['volume_usd'] / 1e6:.1f}M)\n"
        f"Цена сегодня: {row['price_change_pct']:+.1f}%\n"
        + (f"RSI: {row['rsi']:.0f}\n" if row.get("rsi") is not None else "")
        + f"Score: {row['score']:.0f}/100"
        f"{_insider_tag(row)}"
        f"{_judge_tag(row)}"
    )


def send_digest(dividend_rows: list[dict], momentum_rows: list[dict],
                 unusual_volume_rows: list[dict] = None,
                 context_lines: list[str] = None) -> None:
    unusual_volume_rows = unusual_volume_rows or []
    if not dividend_rows and not momentum_rows and not unusual_volume_rows:
        send_message("📭 Сегодня новых кандидатов нет.")
        return

    ctx = ("\n".join(context_lines) + "\n\n") if context_lines else ""

    if dividend_rows:
        blocks = [format_dividend_candidate(r) for r in dividend_rows]
        send_message("💰 <b>ДИВИДЕНДНЫЕ КАНДИДАТЫ</b>\n" + ctx + "\n\n".join(blocks))

    if unusual_volume_rows:
        blocks = [format_unusual_volume_candidate(r) for r in unusual_volume_rows]
        send_message("🔥 <b>АНОМАЛЬНЫЙ ОБЪЁМ — ДВИЖЕНИЕ НАЧИНАЕТСЯ</b>\n" + ctx + "\n\n".join(blocks))

    if momentum_rows:
        has_crypto = any(r["asset_type"] == "crypto" for r in momentum_rows)
        header = "🚀 <b>МОМЕНТУМ / КАНДИДАТЫ НА ИКС</b>\n"
        if has_crypto:
            # Backtest (backtest_momentum.py): stock momentum has a clear
            # positive edge (+3pp vs baseline); crypto momentum edge is near
            # zero/negative even restricted to top-volume pairs — hype coins
            # (WLD/NEAR/PEPE-type) drag it down. Flag until enough live
            # momentum_candidates history accumulates to recalibrate.
            header += "⚠️ Крипто-часть — экспериментально, бектест edge под вопросом\n"
        blocks = [format_momentum_candidate(r) for r in momentum_rows]
        send_message(header + ctx + "\n\n".join(blocks))
