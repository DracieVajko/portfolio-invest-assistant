"""Extended news source modules for portfolio AI assistant.

Adds Slovak news sites, Reddit, enhanced Trump tracking,
crypto/commodity feeds, and analyst recommendation sources
to the existing Google News RSS pipeline.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote_plus

import feedparser
import requests

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Slovak news sources (verified live 2026-09-26: all HTTP 200 + XML).
# SME removed (rss.sme.sk dead: 403 + robots-blocked); Pravda moved to
# /spravy/rss/xml (old /rss/ is 404); TERAZ (TASR) and Google News SK added.
# ---------------------------------------------------------------------------

SLOVAK_NEWS_SOURCES = {
    "aktuality.sk": {
        "rss_url": "https://www.aktuality.sk/rss/",
        "name": "Aktuality",
        "base_url": "https://www.aktuality.sk",
        "category": "general",
    },
    "pravda.sk": {
        "rss_url": "https://www.pravda.sk/spravy/rss/xml",
        "name": "Pravda",
        "base_url": "https://www.pravda.sk",
        "category": "general",
    },
    "teraz.sk": {
        "rss_url": "https://www.teraz.sk/rss/vsetky-spravy.rss",
        "name": "TERAZ",
        "base_url": "https://www.teraz.sk",
        "category": "general",
    },
    "googlenews-sk": {
        "rss_url": "https://news.google.com/rss?hl=sk&gl=SK&ceid=SK:sk",
        "name": "Google News",
        "base_url": "https://news.google.com",
        "category": "general",
    },
}


# ---------------------------------------------------------------------------
# Verified RSS fallback feeds (checked live 2026-09-26: HTTP 200 + body).
# COMPLIANCE: public RSS endpoints only, headlines + links, modest rate.
# Reuters rssFeed is dead (401); FT/Bloomberg are paywalled homepage scrapes
# and are deliberately NOT included (ToS + auth walls).
# ---------------------------------------------------------------------------

RSS_FALLBACK_SOURCES = (
    {"name": "Yahoo Finance", "rss_url": "https://finance.yahoo.com/news/rssindex",
     "tier": 2, "category": "market"},
    {"name": "CNBC Markets", "rss_url": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
     "tier": 2, "category": "macro"},
    {"name": "MarketWatch", "rss_url": "https://www.marketwatch.com/rss/topstories",
     "tier": 2, "category": "market"},
)


# ---------------------------------------------------------------------------
# Trump-specific tracking keywords and topics
# ---------------------------------------------------------------------------

TRUMP_KEYWORD_MAP = {
    "tariffs": ["tariff", "trade war", "import duty", "customs"],
    "greenland": ["greenland", "arctic", "rare earth minerals"],
    "mining": ["mining", "mineral", "extraction", "drilling"],
    "rare_earths": ["rare earth", "critical minerals", "strategic minerals"],
    "regulation": ["regulation", "deregulation", "SEC", "compliance"],
    "economy": ["economy", "GDP", "inflation", "fiscal policy", "tax"],
}


# ---------------------------------------------------------------------------
# Commodities and crypto tracking
# ---------------------------------------------------------------------------

COMMODITY_QUERY_MAP = {
    "gold": ["gold price", "gold market", "gold investment", "gold bullion"],
    "silver": ["silver price", "silver market", "silver investment"],
    "copper": ["copper price", "copper market"],
    "oil": ["oil price", "crude oil", "WTI", "Brent crude"],
    "lithium": ["lithium price", "lithium market", "lithium battery"],
    "uranium": ["uranium price", "uranium market", "nuclear fuel"],
}

CRYPTO_QUERY_MAP = {
    "BTC": ["Bitcoin price", "Bitcoin ETF", "BTC market", "cryptocurrency regulation"],
    "ETH": ["Ethereum price", "ETH market", "Ethereum ETF", "DeFi"],
    "SOL": ["Solana price", "SOL market", "Solana ecosystem"],
}


# ---------------------------------------------------------------------------
# Analyst and forecast tracking
# ---------------------------------------------------------------------------

ANALYST_QUERY_TEMPLATES = [
    "{name} {symbol} analyst rating price target",
    "{name} {symbol} upgrade downgrade 2024 2025",
    "{name} {symbol} investment outlook forecast",
    "{symbol} stock analyst recommendation consensus",
]


class NewsSourceResult:
    """Result from a single news source fetch."""
    def __init__(self, source_name: str, items: list[dict], error: str | None = None):
        self.source_name = source_name
        self.items = items
        self.error = error
        self.fetched_at = datetime.now(timezone.utc)

    @property
    def success(self) -> bool:
        return self.error is None and len(self.items) > 0


def _as_record(item):
    """Coerce a news record to a plain dict (NewsItem dataclass, Pydantic
    model, or mapping). Never raises: unusable shapes become {}.

    Also synthesizes the display-date key ``published`` when the record only
    carries ``published_str``/``published_dt`` (RSS-fallback shape). Renderers
    below read ``published``; without this the holdings/trump/commodity
    sections printed empty ``Date:`` (run 524fffa0). Records with no date at
    all get ``undated: True`` instead of a silent blank.
    """
    if item is None:
        return {}
    if isinstance(item, dict):
        out = dict(item)  # copy: synthesis below must not mutate fetcher output
    else:
        out = None
        try:
            import dataclasses as _dc
            if _dc.is_dataclass(item) and not isinstance(item, type):
                out = _dc.asdict(item)
                if not isinstance(out, dict):
                    out = None
        except Exception:
            pass
        if out is None:
            for attr in ("model_dump", "dict", "to_dict"):
                try:
                    conv = getattr(item, attr, None)
                    if callable(conv):
                        cand = conv()
                        if isinstance(cand, dict):
                            out = cand
                            break
                except Exception:
                    continue
        if out is None:
            try:
                out = {k: getattr(item, k) for k in dir(item)
                       if not k.startswith("_") and not callable(getattr(item, k, None))}
            except Exception:
                return {}
        if not isinstance(out, dict):
            return {}
    if not out.get("published"):
        synth = out.get("published_str") or ""
        if not synth and out.get("published_dt") is not None:
            try:
                synth = out["published_dt"].strftime("%Y-%m-%d %H:%M")
            except Exception:
                synth = str(out.get("published_dt") or "")
        if synth:
            out["published"] = synth
        else:
            out["undated"] = True
    return out


class EnhancedNewsFetcher:
    """Extended news fetcher with multiple source types.

    Adds Slovak news, Reddit, enhanced Trump tracking,
    crypto/commodity feeds, and analyst recommendations
    to the existing Google News RSS pipeline.
    """

    def __init__(
        self,
        max_age_hours: int = 48,
        timeout: int = 10,
        enable_slovak: bool = True,
        enable_reddit: bool = True,
        enable_slovak_sites: list[str] | None = None,
    ):
        self.max_age = timedelta(hours=max_age_hours)
        self.timeout = timeout
        self.enable_slovak = enable_slovak
        self.enable_reddit = enable_reddit
        self.enable_slovak_sites = enable_slovak_sites or list(SLOVAK_NEWS_SOURCES.keys())
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "PortfolioAI/2.0"})
        self._lock = threading.Lock()
        # Slovak sites with a hard failure (HTTP 403/404) stay disabled rest of run.
        self._slovak_disabled: set[str] = set()

    # -----------------------------------------------------------------
    # Slovak news sites
    # -----------------------------------------------------------------

    def fetch_slovak_news(
        self,
        symbols: list[str] | None = None,
        companies: list[str] | None = None,
    ) -> list[dict]:
        """Fetch news from Slovak news sites (Aktuality, Pravda, TERAZ, Google News SK).

        Filters by symbols/company names if provided. Returns items
        with title, url, source, published_dt, and content preview.
        """
        if not self.enable_slovak:
            return []

        all_items: list[dict] = []
        cutoff = datetime.now(timezone.utc) - self.max_age

        for site_key in self.enable_slovak_sites:
            site_info = SLOVAK_NEWS_SOURCES.get(site_key)
            if not site_info:
                continue
            if site_key in self._slovak_disabled:
                continue
            try:
                items = self._fetch_slovak_feed(site_info, cutoff, symbols, companies)
                all_items.extend(items)
            except Exception as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                try:
                    status = int(status) if status else None
                except (TypeError, ValueError):
                    status = None
                if status in (403, 404):
                    self._slovak_disabled.add(site_key)
                    logger.warning("Slovak source unavailable: %s (HTTP %s); "
                                   "disabled for the rest of the run",
                                   site_info.get("name", site_key), status)
                else:
                    logger.debug("Slovak news fetch failed for %s: %s", site_key, exc)
        return all_items

    def _fetch_slovak_feed(
        self,
        site_info: dict,
        cutoff: datetime,
        symbols: list[str] | None,
        companies: list[str] | None,
    ) -> list[dict]:
        """Fetch and parse a Slovak RSS feed."""
        url = site_info["rss_url"]
        try:
            resp = self._session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
        except Exception:
            # Fallback: try HTML scraping of main page
            return self._fetch_slovak_html(site_info, cutoff, symbols, companies)

        items: list[dict] = []
        for entry in feed.entries:
            try:
                pub_dt = self._parse_slovak_date(entry)
                if pub_dt is None or pub_dt < cutoff:
                    continue
                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()
                # Extract content preview from summary or description
                summary = entry.get("summary", entry.get("description", ""))
                preview = self._strip_html(summary)[:300] if summary else ""

                if not title or not link:
                    continue

                # Filter by symbols/companies if provided
                if symbols or companies:
                    text = f"{title} {summary}".upper()
                    if symbols:
                        if not any(s.upper() in text for s in symbols):
                            continue
                    if companies:
                        if not any(c.upper() in text for c in companies):
                            continue

                items.append({
                    "title": title,
                    "url": link,
                    "source": site_info["name"],
                    "published_dt": pub_dt,
                    "published_str": pub_dt.strftime("%Y-%m-%d %H:%M"),
                    "preview": preview,
                    "category": "slovak",
                    "ticker_match": self._find_ticker_matches(text, symbols or []),
                })
            except Exception:
                continue
        return items

    def _fetch_slovak_html(self, site_info: dict, cutoff: datetime,
                           symbols: list[str], companies: list[str]) -> list[dict]:
        """Fallback HTML scraping for Slovak news sites."""
        try:
            resp = self._session.get(site_info["base_url"], timeout=self.timeout)
            resp.raise_for_status()
            text = resp.text
        except Exception:
            return []

        # Simple HTML title extraction
        items: list[dict] = []
        title_pattern = re.compile(r"<h[1-3][^>]*>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>([^<]+)</a>", re.IGNORECASE)
        for match in title_pattern.finditer(text):
            url, title = match.groups()
            # Phase 5: HTML fallback carries no trustworthy timestamp. Never
            # stamp now() (that would defeat the 48h freshness gate); mark
            # undated so the corpus excludes it from decision-relevant sets.
            items.append({
                "title": title.strip(),
                "url": url.strip(),
                "source": site_info["name"],
                "published_dt": None,
                "published_str": "undated",
                "undated": True,
                "preview": "",
                "category": "slovak",
                "ticker_match": [],
            })
        return items[:5]

    def _parse_slovak_date(self, entry) -> datetime | None:
        """Parse date from Slovak RSS feed entry."""
        for field in ("published_parsed", "updated_parsed"):
            if hasattr(entry, field) and getattr(entry, field):
                try:
                    return datetime(*getattr(entry, field)[:6], tzinfo=timezone.utc)
                except Exception:
                    continue
        for field in ("published", "updated"):
            val = entry.get(field, "")
            if val:
                try:
                    return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
                except Exception:
                    continue
        return None

    # -----------------------------------------------------------------
    # Verified RSS fallbacks (Yahoo/CNBC/MarketWatch — see RSS_FALLBACK_SOURCES)
    # -----------------------------------------------------------------

    def fetch_rss_fallbacks(
        self,
        query_terms: list[str] | None = None,
        limit_total: int = 12,
    ) -> list[dict]:
        """Keyword-filtered items from verified RSS fallback feeds.

        Returns Enhanced-shape dicts (category/tier/timestamps real from RSS).
        Never raises: per-feed failures degrade to an empty contribution.
        """
        import email.utils as _eu

        terms = [str(t or "").strip().lower() for t in (query_terms or []) if str(t or "").strip()]
        if not terms:
            terms = ["stock", "market", "earnings"]
        cutoff = datetime.now(timezone.utc) - self.max_age
        out: list[dict] = []
        for src in RSS_FALLBACK_SOURCES:
            if len(out) >= limit_total:
                break
            try:
                resp = self._session.get(src["rss_url"], timeout=self.timeout)
                resp.raise_for_status()
                feed = feedparser.parse(resp.content)
            except Exception as exc:
                logger.debug("RSS fallback fetch failed for %s: %s", src["name"], exc)
                continue
            for entry in feed.entries:
                if len(out) >= limit_total:
                    break
                try:
                    title = str(entry.get("title", "") or "").strip()
                    link = str(entry.get("link", "") or "").strip()
                    if not title or not link:
                        continue
                    hay = f"{title} {entry.get('summary', '')}".lower()
                    if not any(t in hay for t in terms):
                        continue
                    pub_dt = None
                    for field in ("published_parsed", "updated_parsed"):
                        val = getattr(entry, field, None) if not isinstance(entry, dict) else entry.get(field)
                        if val:
                            try:
                                pub_dt = datetime(*val[:6], tzinfo=timezone.utc)
                                break
                            except Exception:
                                continue
                    if pub_dt is None:
                        try:
                            pub_dt = _eu.parsedate_to_datetime(
                                str(entry.get("published", "") or "")).astimezone(timezone.utc)
                        except Exception:
                            continue
                    if pub_dt < cutoff:
                        continue
                    out.append({
                        "title": title,
                        "url": link,
                        "source": src["name"],
                        "published_dt": pub_dt,
                        "published_str": pub_dt.strftime("%Y-%m-%d %H:%M"),
                        "preview": re.sub(r"\s+", " ", str(entry.get("summary", "") or ""))[:300],
                        "category": src["category"],
                        "tier": src["tier"],
                        "ticker_match": [],
                    })
                except Exception:
                    continue
        return out

    # -----------------------------------------------------------------
    # Reddit search
    # -----------------------------------------------------------------

    def fetch_reddit_search(
        self,
        queries: list[str],
        limit_per_query: int = 5,
    ) -> list[dict]:
        """Search Reddit for relevant news and discussions.

        Uses Reddit's public JSON search endpoint.
        Returns items with title, url, source, published_dt.
        """
        if not self.enable_reddit:
            return []

        all_items: list[dict] = []
        cutoff = datetime.now(timezone.utc) - self.max_age

        for query in queries:
            try:
                items = self._fetch_reddit_query(query, limit_per_query, cutoff)
                all_items.extend(items)
            except Exception as exc:
                logger.debug("Reddit fetch failed for '%s': %s", query, exc)
        return all_items

    def _fetch_reddit_query(
        self, query: str, limit: int, cutoff: datetime
    ) -> list[dict]:
        """Fetch Reddit search results for a query."""
        url = f"https://www.reddit.com/search.json?q={quote_plus(query)}&limit={limit}&t=day"
        try:
            resp = self._session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        items: list[dict] = []
        for post in data.get("data", {}).get("children", []):
            try:
                data_obj = post.get("data", {})
                title = data_obj.get("title", "").strip()
                url_str = data_obj.get("url", "").strip()
                permalink = data_obj.get("permalink", "")
                if permalink:
                    url_str = f"https://reddit.com{permalink}"
                created_utc = data_obj.get("created_utc", 0)
                pub_dt = datetime.fromtimestamp(created_utc, tz=timezone.utc)
                if pub_dt < cutoff:
                    continue
                author = data_obj.get("author", "")
                subreddit = data_obj.get("subreddit", "")
                score = data_obj.get("score", 0)

                items.append({
                    "title": title,
                    "url": url_str,
                    "source": f"r/{subreddit}",
                    "published_dt": pub_dt,
                    "published_str": pub_dt.strftime("%Y-%m-%d %H:%M"),
                    "score": score,
                    "author": author,
                    "category": "reddit",
                    "ticker_match": [],
                })
            except Exception:
                continue
        return items[:limit]

    # -----------------------------------------------------------------
    # Trump tracking
    # -----------------------------------------------------------------

    def fetch_trump_tracking(
        self,
        fetcher: Any,
    ) -> dict[str, list[dict]]:
        """Fetch Trump-specific news with categorized keyword tracking.

        Returns dict of category -> list of news items.
        Categories: tariffs, greenland, mining, rare_earths, regulation, economy.
        """
        results: dict[str, list[dict]] = {}
        for category, keywords in TRUMP_KEYWORD_MAP.items():
            queries = [f"Trump {kw} policy stock market" for kw in keywords]
            items = fetcher.fetch_market_news(queries, limit_per_query=3)
            merged: list[dict] = []
            for q_items in items.values():
                for item in q_items:
                    merged.append(item.__dict__ if hasattr(item, "__dict__") else item)
            # Add category metadata
            for item in merged:
                item["trump_category"] = category
                item["trump_keywords"] = keywords
            results[category] = merged[:5]
        return results

    # -----------------------------------------------------------------
    # Commodities and crypto tracking
    # -----------------------------------------------------------------

    def fetch_commodity_news(
        self,
        fetcher: Any,
        commodities: list[str] | None = None,
    ) -> dict[str, list[dict]]:
        """Fetch commodity and crypto news (gold, silver, oil, lithium, uranium)."""
        targets = commodities or list(COMMODITY_QUERY_MAP.keys()) + list(CRYPTO_QUERY_MAP.keys())
        results: dict[str, list[dict]] = {}
        query_map = {**COMMODITY_QUERY_MAP, **CRYPTO_QUERY_MAP}
        for target in targets:
            if target in query_map:
                queries = query_map[target]
                items = fetcher.fetch_market_news(queries, limit_per_query=3)
                merged: list[dict] = []
                for q_items in items.values():
                    for item in q_items:
                        merged.append(item.__dict__ if hasattr(item, "__dict__") else item)
                for item in merged:
                    item["commodity_target"] = target
                results[target] = merged[:5]
        return results

    # -----------------------------------------------------------------
    # Analyst recommendations
    # -----------------------------------------------------------------

    def fetch_analyst_recommendations(
        self,
        fetcher: Any,
        assets: list[dict],
    ) -> dict[str, list[dict]]:
        """Fetch analyst ratings and price targets for portfolio assets.

        Returns dict of symbol -> list of analyst news items.
        """
        results: dict[str, list[dict]] = {}
        for asset in assets:
            symbol = asset.get("symbol", "")
            name = asset.get("name", symbol)
            if not symbol:
                continue
            queries = [q.format(name=name, symbol=symbol) for q in ANALYST_QUERY_TEMPLATES]
            items = fetcher.fetch_market_news(queries, limit_per_query=3)
            merged: list[dict] = []
            for q_items in items.values():
                for item in q_items:
                    merged.append(item.__dict__ if hasattr(item, "__dict__") else item)
            for item in merged:
                item["analyst_target"] = symbol
            if merged:
                results[symbol] = merged[:5]
        return results

    # -----------------------------------------------------------------
    # Helper methods
    # -----------------------------------------------------------------

    def _strip_html(self, html: str) -> str:
        """Remove HTML tags from text."""
        return re.sub(r"<[^>]+>", "", html).strip()

    def _find_ticker_matches(self, text: str, symbols: list[str]) -> list[str]:
        """Find which ticker symbols appear in text."""
        matches = []
        for s in symbols:
            if s.upper() in text.upper():
                matches.append(s)
        return matches

    @staticmethod
    def _resolve_company_name(ticker: str, assets: list[dict]) -> str | None:
        """Resolve a ticker to its company name from asset list."""
        for asset in assets:
            if asset.get("symbol") == ticker or ticker in asset.get("aliases", []):
                return asset.get("name")
        return None


# ---------------------------------------------------------------------------
# Global news context builder
# ---------------------------------------------------------------------------

class NewsContextBuilder:
    """Builds a clean-text .md file with all news for AI consumption."""

    def __init__(self, fetcher: EnhancedNewsFetcher | None = None):
        self.fetcher = fetcher or EnhancedNewsFetcher()

    def build_news_context(
        self,
        news_by_symbol: dict[str, list[dict]],
        trump_tracking: dict[str, list[dict]] | None = None,
        commodity_news: dict[str, list[dict]] | None = None,
        analyst_news: dict[str, list[dict]] | None = None,
        slovak_news: list[dict] | None = None,
        reddit_news: list[dict] | None = None,
        run_id: str = "",
    ) -> str:
        """Build a comprehensive news context .md file for AI.

        All items are formatted as clean text with title, source,
        date, URL, and optional preview text. This gives the AI
        maximum context for decision-making.
        """
        # Boundary normalization (A-sanitize): Strict fetchers yield NewsItem
        # dataclasses, Enhanced fetchers yield dicts. Every consumer below may
        # use .get(); normalize once here so a shape mismatch can never raise
        # "'NewsItem' object has no attribute 'get'" mid-run again.
        news_by_symbol = {k: [_as_record(i) for i in (v or [])]
                          for k, v in (news_by_symbol or {}).items()}
        trump_tracking = {k: [_as_record(i) for i in (v or [])]
                          for k, v in (trump_tracking or {}).items()}
        commodity_news = {k: [_as_record(i) for i in (v or [])]
                          for k, v in (commodity_news or {}).items()}
        analyst_news = {k: [_as_record(i) for i in (v or [])]
                        for k, v in (analyst_news or {}).items()}
        slovak_news = [_as_record(i) for i in (slovak_news or [])]
        reddit_news = [_as_record(i) for i in (reddit_news or [])]

        lines: list[str] = []
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        lines.append(f"# News Context — {ts}")
        lines.append(f"Run ID: `{run_id}`")
        lines.append(f"Time window: last 48 hours")
        lines.append("")
        lines.append("This file contains ALL news sources for AI decision-making.")
        lines.append("Each entry has: title, source, date, URL, and optional preview.")
        lines.append("")

        # 1. Portfolio-specific news
        if news_by_symbol:
            lines.append("---")
            lines.append("## Portfolio Holdings News")
            lines.append("")
            for symbol, entries in sorted(news_by_symbol.items()):
                if not entries:
                    continue
                lines.append(f"### {symbol}")
                lines.append("")
                for item in entries[:5]:
                    title = item.get("title", "")
                    source = item.get("source", "")
                    pub = item.get("published", "")
                    url = item.get("url", "")
                    why = item.get("why", "")
                    lines.append(f"- **[{title}]({url})**")
                    lines.append(f"  - Source: {source} | Date: {pub}")
                    if why:
                        lines.append(f"  - Why it matters: {why}")
                lines.append("")

        # 2. Slovak news
        if slovak_news:
            lines.append("---")
            lines.append("## Slovak News (Aktuality, Pravda, TERAZ, Google News SK)")
            lines.append("")
            for item in slovak_news[:10]:
                title = item.get("title", "")
                source = item.get("source", "")
                pub = item.get("published_str", "")
                url = item.get("url", "")
                preview = item.get("preview", "")
                tickers = item.get("ticker_match", [])
                lines.append(f"- **[{title}]({url})**")
                lines.append(f"  - Source: {source} | Date: {pub}")
                if tickers:
                    lines.append(f"  - Ticker match: {', '.join(tickers)}")
                if preview:
                    lines.append(f"  - Preview: {preview}")
                lines.append("")

        # 3. Reddit discussions
        if reddit_news:
            lines.append("---")
            lines.append("## Reddit Discussions")
            lines.append("")
            for item in reddit_news[:10]:
                title = item.get("title", "")
                source = item.get("source", "")
                pub = item.get("published_str", "")
                url = item.get("url", "")
                score = item.get("score", 0)
                author = item.get("author", "")
                lines.append(f"- **[{title}]({url})**")
                lines.append(f"  - r/{source} | By: u/{author} | Score: {score} | Date: {pub}")
                lines.append("")

        # 4. Trump policy tracking
        if trump_tracking:
            lines.append("---")
            lines.append("## Trump Policy Watch")
            lines.append("")
            for category, items in trump_tracking.items():
                if not items:
                    continue
                lines.append(f"### {category.replace('_', ' ').title()}")
                lines.append("")
                for item in items[:3]:
                    title = item.get("title", "")
                    source = item.get("source", "")
                    pub = item.get("published", "")
                    url = item.get("url", "")
                    lines.append(f"- **[{title}]({url})** — {source} · {pub}")
                lines.append("")

        # 5. Commodities and crypto
        if commodity_news:
            lines.append("---")
            lines.append("## Commodities & Crypto")
            lines.append("")
            for target, items in commodity_news.items():
                if not items:
                    continue
                lines.append(f"### {target.upper()}")
                lines.append("")
                for item in items[:3]:
                    title = item.get("title", "")
                    source = item.get("source", "")
                    pub = item.get("published", "")
                    url = item.get("url", "")
                    lines.append(f"- **[{title}]({url})** — {source} · {pub}")
                lines.append("")

        # 6. Analyst recommendations
        if analyst_news:
            lines.append("---")
            lines.append("## Analyst Recommendations & Forecasts")
            lines.append("")
            for symbol, items in analyst_news.items():
                if not items:
                    continue
                lines.append(f"### {symbol}")
                lines.append("")
                for item in items[:3]:
                    title = item.get("title", "")
                    source = item.get("source", "")
                    pub = item.get("published", "")
                    url = item.get("url", "")
                    lines.append(f"- **[{title}]({url})** — {source} · {pub}")
                lines.append("")

        lines.append("---")
        lines.append("*End of news context.*")

        return "\n".join(lines)

    def build_news_context_file(
        self,
        news_by_symbol: dict[str, list[dict]],
        output_dir: str,
        run_id: str = "",
        **kwargs,
    ) -> str:
        """Build and save the news context .md file. Returns file path."""
        content = self.build_news_context(
            news_by_symbol=news_by_symbol,
            run_id=run_id,
            **kwargs,
        )
        from pathlib import Path
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_path = Path(output_dir) / f"news_context_{ts}.md"
        output_path.write_text(content, encoding="utf-8")
        return str(output_path)
