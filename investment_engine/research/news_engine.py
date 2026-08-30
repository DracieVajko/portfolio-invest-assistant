from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote_plus

import feedparser
import requests

logger = logging.getLogger(__name__)


@dataclass
class NewsItem:
    title: str
    url: str
    source: str
    published_dt: datetime
    published_str: str
    relevance_score: int
    content_hash: str
    query: str


class StrictNewsFetcher:
    """
    Fetches news with STRICT temporal validation.
    - Parses actual publication date (not feed date)
    - Deduplicates by content hash across ALL queries
    - Filters by relevance score
    - Returns only verified recent items
    """

    def __init__(
        self,
        max_age_hours: int = 48,
        min_relevance: int = 60,
        timeout: int = 15,
    ):
        self.max_age = timedelta(hours=max_age_hours)
        self.min_relevance = min_relevance
        self.timeout = timeout
        self._seen_hashes: set[str] = set()
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "PortfolioAI/2.0"})

    def fetch_for_symbol(
        self,
        symbol: str,
        name: str,
        limit: int = 5,
        extra_queries: list[str] | None = None,
    ) -> list[NewsItem]:
        """Fetch and strictly filter news for a symbol."""
        queries = self._build_queries(symbol, name, extra_queries)

        all_items: list[NewsItem] = []
        for query in queries:
            items = self._fetch_google_news(query)
            for item in items:
                item.query = query
            all_items.extend(items)

        # Deduplicate by content hash (global across all queries)
        unique_items = self._deduplicate(all_items)

        # Strict temporal filter
        cutoff = datetime.now(timezone.utc) - self.max_age
        recent_items = [item for item in unique_items if item.published_dt >= cutoff]

        # Relevance scoring
        scored = self._score_relevance(recent_items, symbol, name)
        filtered = [item for item in scored if item.relevance_score >= self.min_relevance]

        # Sort by relevance * recency
        filtered.sort(key=lambda x: (x.relevance_score, x.published_dt), reverse=True)

        return filtered[:limit]

    def fetch_market_news(
        self,
        queries: list[str],
        limit_per_query: int = 3,
    ) -> dict[str, list[NewsItem]]:
        """Fetch news for multiple market-wide queries."""
        results: dict[str, list[NewsItem]] = {}
        for query in queries:
            items = self._fetch_google_news(query)
            for item in items:
                item.query = query
            # Deduplicate globally
            unique = self._deduplicate(items)
            cutoff = datetime.now(timezone.utc) - self.max_age
            recent = [item for item in unique if item.published_dt >= cutoff]
            scored = self._score_generic_relevance(recent)
            filtered = [item for item in scored if item.relevance_score >= self.min_relevance]
            filtered.sort(key=lambda x: (x.relevance_score, x.published_dt), reverse=True)
            results[query] = filtered[:limit_per_query]
        return results

    def _build_queries(
        self,
        symbol: str,
        name: str,
        extra_queries: list[str] | None,
    ) -> list[str]:
        base = [
            f'"{name}" {symbol} stock earnings',
            f'"{name}" {symbol} guidance',
            f'{symbol} stock price target analyst',
            f'{symbol} earnings call transcript',
        ]
        if extra_queries:
            base.extend(extra_queries)
        return base

    def _fetch_google_news(self, query: str) -> list[NewsItem]:
        url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
        try:
            resp = self._session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
        except Exception as e:
            logger.warning("News fetch failed for '%s': %s", query, e)
            return []

        items: list[NewsItem] = []
        for entry in feed.entries:
            try:
                pub_dt = self._parse_date(entry)
                if not pub_dt:
                    continue

                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()
                source = entry.get("source", {}).get("title", "unknown").strip()

                if title and link:
                    content_hash = hashlib.md5(f"{title}{link}".encode()).hexdigest()[:16]
                    items.append(
                        NewsItem(
                            title=title,
                            url=link,
                            source=source,
                            published_dt=pub_dt,
                            published_str=pub_dt.date().isoformat(),
                            relevance_score=0,
                            content_hash=content_hash,
                            query=query,
                        )
                    )
            except Exception as e:
                logger.debug("Failed parsing news entry: %s", e)
                continue
        return items

    def _parse_date(self, entry) -> datetime | None:
        """Parse publication date from multiple possible fields."""
        for field in ("published_parsed", "updated_parsed", "created_parsed"):
            if hasattr(entry, field) and getattr(entry, field):
                try:
                    return datetime(*getattr(entry, field)[:6], tzinfo=timezone.utc)
                except Exception:
                    continue
        for field in ("published", "updated", "created"):
            val = entry.get(field, "")
            if val:
                try:
                    return parsedate_to_datetime(val).astimezone(timezone.utc)
                except Exception:
                    continue
        return None

    def _deduplicate(self, items: list[NewsItem]) -> list[NewsItem]:
        unique = []
        for item in items:
            if item.content_hash not in self._seen_hashes:
                self._seen_hashes.add(item.content_hash)
                unique.append(item)
        return unique

    def _score_relevance(
        self,
        items: list[NewsItem],
        symbol: str,
        name: str,
    ) -> list[NewsItem]:
        symbol_upper = symbol.upper()
        name_upper = name.upper()

        keywords_high = {
            symbol_upper: 30,
            name_upper: 30,
            "earnings": 25,
            "guidance": 25,
            "upgrade": 20,
            "downgrade": 20,
            "target": 15,
            "beat": 20,
            "miss": 20,
            "raise": 15,
            "cut": 15,
        }
        keywords_med = {
            "revenue": 10,
            "profit": 10,
            "margin": 10,
            "dividend": 10,
            "buyback": 10,
            "acquisition": 15,
            "merger": 15,
            "partnership": 10,
        }
        keywords_low = {
            "stock": 5,
            "shares": 5,
            "market": 3,
            "trading": 3,
            "investor": 3,
        }

        for item in items:
            text = f"{item.title} {item.source}".upper()
            score = 0
            for kw, val in keywords_high.items():
                if kw in text:
                    score += val
            for kw, val in keywords_med.items():
                if kw in text:
                    score += val
            for kw, val in keywords_low.items():
                if kw in text:
                    score += val
            # Penalize generic/noisy sources
            source_upper = item.source.upper()
            if source_upper in {"STOCKTWITS", "REDDIT", "TWITTER", "X.COM"}:
                score = int(score * 0.7)
            elif source_upper in {"YAHOO FINANCE", "GOOGLE FINANCE"}:
                score = int(score * 0.9)
            item.relevance_score = min(score, 100)
        return items

    def _score_generic_relevance(self, items: list[NewsItem]) -> list[NewsItem]:
        keywords = {
            "fed": 20,
            "federal reserve": 20,
            "inflation": 15,
            "interest rate": 15,
            "cpi": 15,
            "ppi": 15,
            "gdp": 15,
            "unemployment": 10,
            "earnings": 15,
            "guidance": 15,
            "recession": 15,
            "rally": 10,
            "selloff": 10,
            "crash": 10,
        }
        for item in items:
            text = f"{item.title} {item.source}".upper()
            score = 0
            for kw, val in keywords.items():
                if kw in text:
                    score += val
            item.relevance_score = min(score, 100)
        return items

    def clear_cache(self) -> None:
        """Clear deduplication cache (call between report runs)."""
        self._seen_hashes.clear()


def analyze_news_sentiment(news_items: list[NewsItem]) -> dict[str, Any]:
    """Aggregate sentiment from news headlines."""
    if not news_items:
        return {
            "sentiment": "NEUTRAL",
            "score": 50,
            "count": 0,
            "positive_signals": 0,
            "negative_signals": 0,
            "key_topics": [],
            "latest_headline": None,
        }

    positive_kw = [
        "beat",
        "raise",
        "upgrade",
        "strong",
        "growth",
        "record",
        "bullish",
        "outperform",
        "buy",
        "surge",
        "rally",
        "gain",
        "profit",
        "positive",
        "optimistic",
        "confident",
    ]
    negative_kw = [
        "miss",
        "cut",
        "downgrade",
        "weak",
        "decline",
        "loss",
        "bearish",
        "underperform",
        "sell",
        "risk",
        "fall",
        "drop",
        "crash",
        "plunge",
        "warn",
        "concern",
        "negative",
        "pessimistic",
    ]

    pos_count = neg_count = 0
    for item in news_items:
        title = item.title.lower()
        pos_count += sum(1 for kw in positive_kw if kw in title)
        neg_count += sum(1 for kw in negative_kw if kw in title)

    total = pos_count + neg_count
    if total == 0:
        sentiment_score = 50
    else:
        sentiment_score = 50 + (pos_count - neg_count) / total * 50

    # Extract key topics (simple noun phrase extraction)
    topics = extract_key_topics(news_items)

    return {
        "sentiment": "POSITIVE" if sentiment_score > 60 else "NEGATIVE" if sentiment_score < 40 else "NEUTRAL",
        "score": round(sentiment_score, 1),
        "count": len(news_items),
        "positive_signals": pos_count,
        "negative_signals": neg_count,
        "key_topics": topics,
        "latest_headline": news_items[0].title if news_items else None,
    }


def extract_key_topics(news_items: list[NewsItem], max_topics: int = 5) -> list[str]:
    """Extract key topics from news headlines."""
    # Simple approach: find capitalized words/phrases that appear multiple times
    from collections import Counter
    import re

    all_text = " ".join(item.title for item in news_items)
    # Find potential entities (capitalized words, 2-3 word phrases)
    words = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b", all_text)
    # Filter common words
    stopwords = {
        "The", "A", "An", "And", "Or", "But", "In", "On", "At", "To", "For",
        "Of", "With", "By", "From", "As", "Is", "Was", "Are", "Were", "Be",
        "Been", "Being", "Have", "Has", "Had", "Do", "Does", "Did", "Will",
        "Would", "Could", "Should", "May", "Might", "Must", "Can", "Stock",
        "Stocks", "Market", "Markets", "Trading", "Trader", "Investor", "Investors",
        "Company", "Companies", "Business", "Report", "Reports", "News", "Analysis",
    }
    filtered = [w for w in words if w not in stopwords and len(w) > 2]
    counted = Counter(filtered).most_common(max_topics)
    return [topic for topic, _ in counted]


def news_items_to_dict(items: list[NewsItem]) -> list[dict[str, Any]]:
    """Convert NewsItem list to dict for JSON serialization."""
    return [
        {
            "title": item.title,
            "url": item.url,
            "source": item.source,
            "published": item.published_str,
            "published_dt": item.published_dt.isoformat(),
            "relevance_score": item.relevance_score,
            "content_hash": item.content_hash,
            "query": item.query,
        }
        for item in items
    ]