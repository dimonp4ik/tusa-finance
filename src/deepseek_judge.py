"""
Optional DeepSeek sanity-check on final digest candidates.

Why DeepSeek: 5M free tokens on signup, then ~$0.14-0.28/M — verifying 5
candidates per scan costs well under $1/month, in line with this project's
"as cheap as possible" rule. No key set → the whole step is skipped and the
bot behaves exactly as before.

This is a second opinion, not a gatekeeper: verdicts are appended to the
Telegram message, nothing gets filtered out. (The crypto bot's history
showed LLM calibration needs months of live data before it earns trust —
so we log verdicts to the DB alongside candidates for later comparison.)
"""
import json
import logging
import sys
import os

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL, DEEPSEEK_BASE_URL

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
    return bool(DEEPSEEK_API_KEY)


def judge_candidates(rows: list[dict]) -> dict:
    """symbol -> {'verdict': 'BUY'|'SKIP', 'reason': str}. Empty dict when
    disabled or on any failure — callers treat this as purely additive."""
    if not is_enabled() or not rows:
        return {}

    compact = [{
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

    try:
        resp = requests.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
            json={
                "model": DEEPSEEK_MODEL,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(compact, ensure_ascii=False)},
                ],
                "temperature": 0.2,
                "max_tokens": 800,
            },
            timeout=60,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        # strip markdown fences if the model wrapped the JSON
        if content.startswith("```"):
            content = content.strip("`")
            content = content[content.find("["):content.rfind("]") + 1]
        verdicts = json.loads(content)
        return {
            v["symbol"]: {"verdict": v.get("verdict", "?"), "reason": v.get("reason", "")}
            for v in verdicts if isinstance(v, dict) and v.get("symbol")
        }
    except Exception as e:
        _log.warning("DeepSeek judge failed (non-fatal): %s", e)
        return {}
