"""Sanitation: NewsItem boundary, RSS fallbacks, single AI-context path.

Offline only (RSS fetch tested with a stubbed session, never the network).
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone


@dataclasses.dataclass
class _FakeNewsItem:
    title: str
    url: str
    source: str
    published_dt: datetime
    published_str: str = ""


def test_as_record_coerces_dataclass_pydantic_and_dict():
    from investment_engine.research.news_sources import _as_record

    now = datetime.now(timezone.utc)
    assert _as_record({"title": "t"})["title"] == "t"
    assert _as_record(None) == {}
    rec = _as_record(_FakeNewsItem("T", "https://e.example/x", "S", now, "2026"))
    assert rec["title"] == "T" and rec["url"] == "https://e.example/x"

    class _Pyd:
        def model_dump(self):
            return {"title": "p"}

    assert _as_record(_Pyd())["title"] == "p"


def test_builder_accepts_newsitem_objects():
    from investment_engine.research.news_engine import NewsItem
    from investment_engine.research.news_sources import NewsContextBuilder

    now = datetime.now(timezone.utc)
    item = NewsItem(title="T", url="https://e.example/x", source="S",
                    published_dt=now, published_str="now",
                    relevance_score=80, content_hash="h", query="q")
    out = NewsContextBuilder().build_news_context(
        news_by_symbol={"AAPL": [item]}, trump_tracking={"tariffs": [item]},
        run_id="t")
    assert "T" in out and "https://e.example/x" in out


def test_rss_fallback_sources_verified_only():
    from investment_engine.research.news_sources import RSS_FALLBACK_SOURCES

    urls = [s["rss_url"] for s in RSS_FALLBACK_SOURCES]
    assert len(urls) == 3
    assert all(u.startswith("https://") for u in urls)
    joined = " ".join(urls)
    assert "reuters.com/rssFeed" not in joined and "ft.com" not in joined \
        and "bloomberg.com" not in joined


def test_fetch_rss_fallbacks_filters_and_never_raises(monkeypatch):
    from investment_engine.research.news_sources import EnhancedNewsFetcher

    now = datetime.now(timezone.utc)
    fresh = now.strftime("%a, %d %b %Y %H:%M:%S GMT")
    rss = ("<rss><channel>"
           "<item><title>Apple earnings beat</title><link>https://e.example/a</link>"
           f"<pubDate>{fresh}</pubDate><description>Apple stock</description></item>"
           "<item><title>Old news</title><link>https://e.example/o</link>"
           "<pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate></item>"
           "<item><title>No date item</title><link>https://e.example/n</link></item>"
           "</channel></rss>").encode()

    class _Resp:
        def raise_for_status(self):
            return None
        content = rss

    fetcher = EnhancedNewsFetcher()
    monkeypatch.setattr(fetcher._session, "get", lambda *a, **k: _Resp())
    items = fetcher.fetch_rss_fallbacks(["apple"], limit_total=10)
    # Same stub served to all 3 feeds: stale + undated entries filtered out,
    # one fresh item kept per feed.
    assert len(items) == 3
    assert all(i["title"] == "Apple earnings beat" for i in items)
    assert {i["source"] for i in items} == {"Yahoo Finance", "CNBC Markets", "MarketWatch"}
    assert all(i["published_dt"] is not None and i["tier"] == 2 for i in items)

    def _boom(*a, **k):
        raise ConnectionError("down")

    monkeypatch.setattr(fetcher._session, "get", _boom)
    assert fetcher.fetch_rss_fallbacks(["apple"]) == []


def test_ai_context_layer_skips_identical_copy(monkeypatch, tmp_path):
    import time

    from investment_engine.reporting import report_structure as rs

    monkeypatch.chdir(tmp_path)
    ai_dir = tmp_path / "reports" / "ai_context"
    ai_dir.mkdir(parents=True)
    target = ai_dir / "ai_context_r1.md"
    target.write_text("SAME", encoding="utf-8")
    before = target.stat().st_mtime
    time.sleep(0.02)
    ctx, analysis = rs.save_ai_context_layer("r1", "SAME", {"a": 1})
    assert ctx.resolve() == target and analysis.is_file()
    assert target.stat().st_mtime == before  # no redundant rewrite
    rs.save_ai_context_layer("r1", "DIFFERENT", {"a": 1})
    assert target.read_text(encoding="utf-8") == "DIFFERENT"
