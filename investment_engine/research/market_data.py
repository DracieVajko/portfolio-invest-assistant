from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote_plus

import requests


logger = logging.getLogger(__name__)


def fetch_recent_headlines(query: str, *, limit: int = 2, max_age_days: int = 2) -> list[dict[str, str]]:
    """Fetch only recent headline metadata from Google News RSS.

    The report receives title, source, date and URL only. This avoids passing
    full article text to the model while keeping every statement traceable.
    """
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    try:
        response = requests.get(url, timeout=15, headers={"User-Agent": "PortfolioAI/1.0"})
        response.raise_for_status()
        root = ET.fromstring(response.content)
    except Exception as exc:
        logger.warning("News fetch failed for %s: %s", query, exc)
        return []

    items: list[dict[str, str]] = []
    for item in root.findall("./channel/item"):
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "").astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
        if published < cutoff:
            continue
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        source = (item.findtext("source") or "unknown").strip()
        if title and link:
            items.append({"title": title, "source": source, "published": published.date().isoformat(), "url": link})
        if len(items) >= limit:
            break
    return items


def recent_earnings_date(symbol: str) -> str | None:
    """Return the closest reported/upcoming earnings date when Yahoo exposes it."""
    try:
        import yfinance as yf

        dates = yf.Ticker(symbol).get_earnings_dates(limit=4)
        if dates is None or dates.empty:
            return None
        index = dates.index[0]
        return index.date().isoformat() if hasattr(index, "date") else str(index)
    except Exception as exc:
        logger.debug("Earnings lookup unavailable for %s: %s", symbol, exc)
        return None


def analyst_consensus(symbol: str) -> str | None:
    """Return a compact broker-consensus snapshot when Yahoo exposes one."""
    try:
        import yfinance as yf

        summary = yf.Ticker(symbol).recommendations_summary
        if summary is None or summary.empty:
            return None
        row = summary.iloc[0].to_dict()
        period = row.get("period", "recent")
        buys = int(row.get("strongBuy", 0) or 0) + int(row.get("buy", 0) or 0)
        holds = int(row.get("hold", 0) or 0)
        sells = int(row.get("sell", 0) or 0) + int(row.get("strongSell", 0) or 0)
        return f"{period}: buy={buys}, hold={holds}, sell={sells}"
    except Exception as exc:
        logger.debug("Analyst consensus unavailable for %s: %s", symbol, exc)
        return None
