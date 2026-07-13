"""
Insider open-market buying — SEC EDGAR (official, free, no API key).

"Кто-то жёстко закупился": a Form 4 with transaction code 'P' (open-market
purchase) means an insider (exec/director/10%+ owner) spent their own real
money buying the stock — a much stronger signal than the same person merely
holding options/RSU grants (which is routine, not conviction).

Scoped to already-shortlisted candidates (not the full universe) — this is
an enrichment layer on dividend/momentum/unusual-volume hits, not another
full-market scan.
"""
import logging
import sys
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import INSIDER_LOOKBACK_DAYS, INSIDER_MIN_VALUE_USD, SEC_USER_AGENT

_log = logging.getLogger(__name__)
_HEADERS = {"User-Agent": SEC_USER_AGENT}

_cik_cache = {"at": 0.0, "by_ticker": {}}
_CIK_TTL = 24 * 3600


def _cik_for_symbol(symbol: str) -> str | None:
    now = time.time()
    if now - _cik_cache["at"] > _CIK_TTL or not _cik_cache["by_ticker"]:
        try:
            resp = requests.get("https://www.sec.gov/files/company_tickers.json",
                                 headers=_HEADERS, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            _cik_cache["by_ticker"] = {
                row["ticker"].upper(): str(row["cik_str"]).zfill(10)
                for row in data.values()
            }
            _cik_cache["at"] = now
        except Exception as e:
            _log.warning("SEC ticker->CIK map fetch failed: %s", e)
    return _cik_cache["by_ticker"].get(symbol.upper())


def _recent_form4_index_urls(cik: str, count: int = 10) -> list[str]:
    try:
        resp = requests.get(
            "https://www.sec.gov/cgi-bin/browse-edgar",
            params={"action": "getcompany", "CIK": cik, "type": "4",
                    "dateb": "", "owner": "include", "count": str(count), "output": "atom"},
            headers=_HEADERS, timeout=15,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        return [e.find("a:link", ns).get("href") for e in root.findall("a:entry", ns)
                if e.find("a:link", ns) is not None]
    except Exception as e:
        _log.debug("Form4 index fetch failed for CIK %s: %s", cik, e)
        return []


def _form4_xml_url(index_url: str) -> str | None:
    """index_url like .../000114036126025622/0001140361-26-025622-index.htm ->
    find the .xml doc listed in the filing directory's index.json manifest."""
    base = index_url.rsplit("/", 1)[0]
    json_url = f"{base}/index.json"
    try:
        resp = requests.get(json_url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        items = resp.json().get("directory", {}).get("item", [])
        for it in items:
            name = it.get("name", "")
            if name.endswith(".xml") and "form4" in name.lower():
                return f"{base}/{name}"
        # fallback: any .xml that isn't a schema/stylesheet
        for it in items:
            name = it.get("name", "")
            if name.endswith(".xml") and not name.endswith(".xsd"):
                return f"{base}/{name}"
    except Exception as e:
        _log.debug("Form4 manifest fetch failed for %s: %s", json_url, e)
    return None


def _parse_form4_purchases(xml_bytes: bytes, min_value_usd: float) -> list[dict]:
    out = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return out

    owner_name = ""
    owner_el = root.find(".//reportingOwner/reportingOwnerId/rptOwnerName")
    if owner_el is not None and owner_el.text:
        owner_name = owner_el.text.strip()

    for txn in root.findall(".//nonDerivativeTransaction"):
        code_el = txn.find("./transactionCoding/transactionCode")
        code = (code_el.text or "").strip() if code_el is not None else ""
        if code != "P":   # 'P' = open-market purchase; skip grants/awards/sales/gifts
            continue
        try:
            shares = float(txn.find("./transactionAmounts/transactionShares/value").text)
            price = float(txn.find("./transactionAmounts/transactionPricePerShare/value").text)
        except (AttributeError, TypeError, ValueError):
            continue
        value_usd = shares * price
        if value_usd < min_value_usd:
            continue
        date_el = txn.find("./transactionDate/value")
        out.append({
            "insider": owner_name,
            "shares": shares,
            "price": price,
            "value_usd": value_usd,
            "date": date_el.text if date_el is not None else "",
        })
    return out


def get_recent_insider_purchases(symbol: str) -> list[dict]:
    """Recent open-market insider BUYS for a US-listed ticker (SEC EDGAR data
    only covers US filers). Empty list on any failure — never raises, this
    is an enrichment, not a required step."""
    cik = _cik_for_symbol(symbol)
    if not cik:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=INSIDER_LOOKBACK_DAYS)
    purchases = []
    for index_url in _recent_form4_index_urls(cik):
        xml_url = _form4_xml_url(index_url)
        if not xml_url:
            continue
        try:
            resp = requests.get(xml_url, headers=_HEADERS, timeout=15)
            resp.raise_for_status()
        except Exception as e:
            _log.debug("Form4 xml fetch failed %s: %s", xml_url, e)
            continue
        for p in _parse_form4_purchases(resp.content, INSIDER_MIN_VALUE_USD):
            try:
                txn_date = datetime.strptime(p["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
            if txn_date >= cutoff:
                purchases.append(p)
        time.sleep(0.15)   # be polite to SEC's free endpoint
    return purchases
