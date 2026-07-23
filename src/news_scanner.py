"""
News-catalyst scanner — runs every NEWS_SCAN_INTERVAL_MINUTES (default 5).

Why this exists: momentum and unusual-volume only see a move AFTER it shows
up in price/volume — by definition, after the fact. A genuine catalyst
headline (earnings beat, FDA approval, exchange listing, partnership) can be
the FIRST signal, before the market has caught up. This is the closest thing
in the bot to "get in before the x", not after.

Pipeline: free RSS headlines (macro business + crypto) → cheap regex ticker
match against the live universe → free Groq sentiment/relevance check (is
this headline ACTUALLY a tradeable catalyst for that specific ticker, not
just an incidental mention) → dedup by headline hash + a short per-symbol
cooldown so one story reported by 5 outlets doesn't fire 5 alerts.

No GROQ_API_KEY → matched headlines are never sent (a raw ticker-in-headline
match alone is too noisy to trust — "Fed raises rates, ALL sectors react"
would false-positive-match ticker ALL).
"""
import hashlib
import logging
import re
import sys
import os
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import xml.etree.ElementTree as ET

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import GROQ_API_KEY, GROQ_MODEL

_log = logging.getLogger(__name__)

RSS_FEEDS = [
    # Macro/business — same free sources as the other tusa bots' news_agent.py
    ("Reuters",     "https://feeds.reuters.com/reuters/businessNews"),
    ("CNBC",        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839135"),
    ("BBC Biz",     "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    # Crypto-specific — tusa finance covers crypto too, unlike tusa stocks
    ("CoinDesk",      "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt",       "https://decrypt.co/feed"),
]

# Common full names for major coins — crypto headlines usually say "Bitcoin"
# or "Solana", not "BTC"/"SOL". Covers the majors; matches the backtest
# finding that momentum edge concentrates there anyway (long-tail alts are
# rarely named in mainstream headlines in the first place).
_CRYPTO_NAME_MAP = {
    "bitcoin": "BTC", "ethereum": "ETH", "ether": "ETH", "solana": "SOL",
    "ripple": "XRP", "xrp": "XRP", "dogecoin": "DOGE", "cardano": "ADA",
    "avalanche": "AVAX", "chainlink": "LINK", "polkadot": "DOT",
    "litecoin": "LTC", "tron": "TRX", "shiba inu": "SHIB", "toncoin": "TON",
    "near protocol": "NEAR", "cosmos": "ATOM", "uniswap": "UNI", "aave": "AAVE",
    "arbitrum": "ARB", "optimism": "OP", "sui": "SUI", "aptos": "APT",
    "injective": "INJ", "filecoin": "FIL", "internet computer": "ICP",
    "stellar": "XLM", "binance coin": "BNB", "bnb": "BNB", "pepe": "PEPE",
    "monero": "XMR", "celestia": "TIA", "worldcoin": "WLD",
    "hyperliquid": "HYPE", "zcash": "ZEC", "bitcoin cash": "BCH",
}

# Tickers that collide with common English words — pure standalone-uppercase
# matching on these is unreliable (Groq still gets a shot, but skip the easy
# false-positive cases outright to save API calls).
_STOCK_TICKER_DENYLIST = {
    "A", "I", "IT", "ON", "SO", "GO", "ALL", "ARE", "SEE", "ONE", "NEW",
    "FOR", "CAN", "NOW", "OUT", "TOP", "BIG", "WELL", "CEO", "USA", "GDP",
    "CPI", "FED", "OK", "BE", "ME", "US", "AI",
}

_CASHTAG_RE = re.compile(r"\$([A-Z]{1,5})\b")
_UPPER_WORD_RE = re.compile(r"\b([A-Z]{3,5})\b")


def _fetch_rss(url: str, timeout: int = 8) -> list[dict]:
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0 TusaFinance/1.0"})
        if resp.status_code != 200:
            return []
        root = ET.fromstring(resp.content)
        items = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            pub = item.findtext("pubDate", "")
            if not title:
                continue
            try:
                pub_dt = parsedate_to_datetime(pub).astimezone(timezone.utc) if pub else None
            except Exception:
                pub_dt = None
            items.append({"title": title, "published": pub_dt})
        return items
    except Exception as e:
        _log.debug("RSS fetch failed for %s: %s", url, e)
        return []


def fetch_recent_headlines(minutes: int) -> list[dict]:
    """{'title', 'source', 'published'} for headlines published in the last
    `minutes` minutes. published=None (feed with no pubDate) is kept — we'd
    rather over-check a headline than silently drop undated ones."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    out = []
    for name, url in RSS_FEEDS:
        for it in _fetch_rss(url)[:20]:
            if it["published"] and it["published"] < cutoff:
                continue
            out.append({"title": it["title"], "source": name, "published": it["published"]})
    return out


def headline_hash(title: str) -> str:
    return hashlib.sha256(title.strip().lower().encode()).hexdigest()[:16]


def match_tickers(title: str, stock_universe: set, crypto_bases: set) -> list[tuple]:
    """Returns [(symbol, asset_type), ...] — cheap regex prefilter, not the
    final verdict (Groq confirms relevance downstream)."""
    hits = []
    seen = set()

    for m in _CASHTAG_RE.finditer(title):
        sym = m.group(1)
        if sym in stock_universe and sym not in seen:
            hits.append((sym, "stock")); seen.add(sym)
        elif sym in crypto_bases and sym not in seen:
            hits.append((sym, "crypto")); seen.add(sym)

    for m in _UPPER_WORD_RE.finditer(title):
        sym = m.group(1)
        if sym in seen or sym in _STOCK_TICKER_DENYLIST:
            continue
        if sym in crypto_bases:
            hits.append((sym, "crypto")); seen.add(sym)
        elif sym in stock_universe:
            hits.append((sym, "stock")); seen.add(sym)

    lower = title.lower()
    for name, base in _CRYPTO_NAME_MAP.items():
        if name in lower and base not in seen:
            hits.append((base, "crypto")); seen.add(base)

    return hits


def is_enabled() -> bool:
    return bool(GROQ_API_KEY)


def score_catalyst(title: str, symbol: str, asset_type: str) -> dict | None:
    """Ask Groq: is this headline an actual tradeable catalyst for `symbol`,
    not just an incidental mention? Returns {'is_catalyst', 'sentiment',
    'reason'} or None on failure/disabled — treated as purely additive by
    callers, never blocks anything downstream."""
    if not GROQ_API_KEY:
        return None
    label = "крипто-монеты" if asset_type == "crypto" else "акции"
    prompt = (
        f"Заголовок новости: \"{title}\"\n"
        f"Это реально о {label} с тикером {symbol} (не случайное совпадение слов)? "
        "И это потенциальный катализатор роста цены (не просто упоминание, не негатив)? "
        'Ответь СТРОГО JSON: {"is_catalyst": true|false, "sentiment": "BULLISH|BEARISH|NEUTRAL", "reason": "<=12 слов на русском"}'
    )
    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 150,
            },
            timeout=15,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content[content.find("{"):content.rfind("}") + 1]
        import json
        data = json.loads(content)
        return {
            "is_catalyst": bool(data.get("is_catalyst")),
            "sentiment": data.get("sentiment", "NEUTRAL"),
            "reason": data.get("reason", ""),
        }
    except Exception as e:
        _log.debug("Groq catalyst scoring failed for %s: %s", symbol, e)
        return None


def scan(stock_universe: list[str], crypto_universe: list[str], minutes: int) -> list[dict]:
    """Full pipeline. Returns candidate rows ready for db.save + Telegram —
    only BULLISH is_catalyst=True hits (BEARISH/NEUTRAL and non-catalyst
    mentions are dropped here, not surfaced as "SKIP" noise)."""
    if not is_enabled():
        return []

    stock_set = set(stock_universe)
    crypto_bases = {inst.split("-")[0] for inst in crypto_universe}
    headlines = fetch_recent_headlines(minutes)

    candidates = []
    seen_this_scan = set()
    for h in headlines:
        for symbol, asset_type in match_tickers(h["title"], stock_set, crypto_bases):
            key = (symbol, headline_hash(h["title"]))
            if key in seen_this_scan:
                continue
            seen_this_scan.add(key)
            score = score_catalyst(h["title"], symbol, asset_type)
            if not score or not score["is_catalyst"] or score["sentiment"] != "BULLISH":
                continue
            candidates.append({
                "symbol": symbol,
                "asset_type": asset_type,
                "headline": h["title"],
                "source": h["source"],
                "reason": score["reason"],
                "headline_hash": headline_hash(h["title"]),
            })
    return candidates
