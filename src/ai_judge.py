"""
Optional AI sanity-check on final digest candidates.

Provider is picked by which key is set: Claude first (CLAUDE_API_KEY — the
user already has one from the other tusa bots; Haiku 4.5 by default, ~$0.5/mo
on this load, JUDGE_MODEL switches to Sonnet), DeepSeek as fallback
(DEEPSEEK_API_KEY). No key → the whole step is skipped and the bot behaves
exactly as before.

This is a second opinion, not a gatekeeper: verdicts are appended to the
Telegram message, nothing gets filtered out. (The crypto bot's history
showed LLM calibration needs months of live data before it earns trust —
so verdicts are logged to the DB alongside candidates for later comparison.)
"""
import json
import logging
import sys
import os

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    CLAUDE_API_KEY, JUDGE_MODEL,
    DEEPSEEK_API_KEY, DEEPSEEK_MODEL, DEEPSEEK_BASE_URL,
)

_log = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "Ты — скептичный инвест-аналитик. Тебе дают кандидатов от механического "
    "скринера (моментум/дивиденды/аномальный объём) с горизонтом удержания в "
    "несколько недель. Для КАЖДОГО кандидата дай вердикт BUY или SKIP и одну "
    "короткую причину на русском (максимум 12 слов). Учитывай: перекупленность, "
    "устойчивость тренда, ликвидность, риск памп-энд-дамп для мелкой крипты. "
    "Отвечай СТРОГО JSON-массивом вида "
    '[{"symbol": "...", "verdict": "BUY|SKIP", "reason": "..."}] без другого текста.'
)


def is_enabled() -> bool:
    return bool(CLAUDE_API_KEY or DEEPSEEK_API_KEY)


def provider_name() -> str:
    if CLAUDE_API_KEY:
        return "Claude"
    if DEEPSEEK_API_KEY:
        return "DeepSeek"
    return ""


def _compact(rows: list[dict]) -> list[dict]:
    return [{
        "symbol": r["symbol"],
        "type": r.get("asset_type", "stock"),
        "price_change_pct": r.get("price_change_pct"),
        "rsi": r.get("rsi"),
        "volume_usd": r.get("volume_usd"),
        "relative_volume": r.get("relative_volume"),
        "yield_pct": r.get("yield_pct"),
        "payout_ratio": r.get("payout_ratio"),
        "insider_bought_usd": sum(p["value_usd"] for p in (r.get("insider_purchases") or [])) or None,
    } for r in rows]


def _parse_verdicts(content: str) -> dict:
    content = content.strip()
    # strip markdown fences if the model wrapped the JSON
    if content.startswith("```"):
        content = content[content.find("["):content.rfind("]") + 1]
    verdicts = json.loads(content)
    return {
        v["symbol"]: {"verdict": v.get("verdict", "?"), "reason": v.get("reason", "")}
        for v in verdicts if isinstance(v, dict) and v.get("symbol")
    }


def _ask_claude(user_content: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    response = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=1000,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    return next(b.text for b in response.content if b.type == "text")


def _ask_deepseek(user_content: str) -> str:
    resp = requests.post(
        f"{DEEPSEEK_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
        json={
            "model": DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.2,
            "max_tokens": 800,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def judge_candidates(rows: list[dict]) -> dict:
    """symbol -> {'verdict': 'BUY'|'SKIP', 'reason': str}. Empty dict when
    disabled or on any failure — callers treat this as purely additive."""
    if not is_enabled() or not rows:
        return {}
    user_content = json.dumps(_compact(rows), ensure_ascii=False)
    try:
        if CLAUDE_API_KEY:
            content = _ask_claude(user_content)
        else:
            content = _ask_deepseek(user_content)
        return _parse_verdicts(content)
    except Exception as e:
        _log.warning("AI judge (%s) failed (non-fatal): %s", provider_name(), e)
        return {}
