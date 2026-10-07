"""Yahoo Finance symbol suggestions for unresolvable tickers.

Primary source: Yahoo search API (``query1.finance.yahoo.com/v1/finance/search``)
— keyless, lightweight, uses the existing ``requests`` dependency. Never raises;
any network/parse problem yields ``[]`` so the run always continues.

Playwright stays a last-resort manual fallback (quote-page verification by the
user), never an automatic suggestion source: it is heavy, needs a browser, and
is currently optional (see requirements.txt).
"""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
_TIMEOUT_S = 10


def score_candidate(display: str, quote: dict[str, Any]) -> int:
    """Score a Yahoo quote against a broker display symbol (higher = better)."""
    disp = (display or "").strip().upper()
    sym = str(quote.get("symbol", "")).strip().upper()
    name = str(quote.get("shortname") or quote.get("longname") or "").upper()
    qtype = str(quote.get("type") or "").upper()
    score = 0
    base = sym.split("-")[0].split(".")[0]
    if base == disp:
        score += 50
    elif base.startswith(disp) or disp.startswith(base):
        score += 25
    if disp and disp in name:
        score += 15
    if qtype in ("EQUITY", "ETF", "CRYPTOCURRENCY", "CRYPTO"):
        score += 10
    if quote.get("exch"):
        score += 5
    return score


def suggest_yahoo_symbols(display: str, limit: int = 5) -> list[dict[str, Any]]:
    """Return up to ``limit`` Yahoo candidates for ``display``. Never raises."""
    query = (display or "").strip()
    if not query:
        return []
    try:
        resp = requests.get(
            _SEARCH_URL,
            params={"q": query, "quotesCount": max(int(limit), 1), "newsCount": 0},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=_TIMEOUT_S,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.debug("Yahoo symbol search failed for %r: %s", query, exc)
        return []
    try:
        quotes = (data or {}).get("quotes") or []
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for q in quotes:
        if not isinstance(q, dict) or not q.get("symbol"):
            continue
        out.append({
            "symbol": str(q.get("symbol")),
            "name": str(q.get("shortname") or q.get("longname") or ""),
            "exch": str(q.get("exchange") or q.get("exchDisp") or ""),
            "type": str(q.get("quoteType") or q.get("quote_type") or ""),
            "score": score_candidate(query, {
                "symbol": q.get("symbol"),
                "shortname": q.get("shortname") or q.get("longname"),
                "exch": q.get("exchange"),
                "type": q.get("quoteType"),
            }),
        })
    out.sort(key=lambda c: -c["score"])
    return out[:max(int(limit), 1)]


def suggest_for_failed(displays: list[str], limit: int = 3) -> dict[str, list[dict[str, Any]]]:
    """Best-effort suggestions for several displays. Never raises."""
    result: dict[str, list[dict[str, Any]]] = {}
    for disp in displays or []:
        try:
            result[str(disp)] = suggest_yahoo_symbols(str(disp), limit=limit)
        except Exception:
            result[str(disp)] = []
    return result
