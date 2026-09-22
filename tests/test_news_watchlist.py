"""Trump watchlist offline tests. No network and no main.py import."""
from datetime import datetime, timedelta, timezone

from investment_engine.research.news_engine import (
    TRUMP_WATCH_QUERIES,
    NewsItem,
    fetch_trump_watch,
    top_macro_fallback,
)


def _mk(title, source="Reuters", hours=2, url="https://example.com/x", key="k"):
    dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    return NewsItem(
        title=title,
        url=url,
        source=source,
        published_dt=dt,
        published_str=dt.date().isoformat(),
        relevance_score=80,
        content_hash=key,
        query="q",
    )


def test_watch_queries_nonempty_with_tariff_and_megacaps():
    assert isinstance(TRUMP_WATCH_QUERIES, list)
    assert len(TRUMP_WATCH_QUERIES) >= 4
    lowered = [q.lower() for q in TRUMP_WATCH_QUERIES]
    assert any("tariff" in q for q in lowered)
    joined = " ".join(TRUMP_WATCH_QUERIES)
    for t in ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL", "AVGO", "AMD", "TSM"]:
        assert t in joined


def test_fetch_trump_watch_routes_and_caps():
    seen = {}

    class Fake:
        def fetch_market_news(self, queries, limit_per_query=3):
            seen["queries"] = list(queries)
            seen["limit"] = limit_per_query
            out = {}
            for q in queries:
                out[q] = [_mk("Title " + q + " " + str(i), key=q + str(i)) for i in range(5)]
            return out

    res = fetch_trump_watch(Fake(), limit_per_query=3)
    assert seen["queries"] == list(TRUMP_WATCH_QUERIES)
    assert seen["limit"] == 3
    for items in res.values():
        assert len(items) <= 3
    assert isinstance(res, dict)


def test_fetch_trump_watch_default_limit_caps():
    class Fake2:
        def fetch_market_news(self, queries, limit_per_query=3):
            return {q: [_mk("T " + str(i), key=q + str(i)) for i in range(10)] for q in queries}

    res = fetch_trump_watch(Fake2(), limit_per_query=2)
    for items in res.values():
        assert len(items) <= 2


def test_top_macro_fallback_tier_then_recency():
    old_wire = _mk("Old wire", source="Reuters", hours=10, url="https://reuters.com/a", key="a1")
    new_wire = _mk("New wire", source="Reuters", hours=1, url="https://reuters.com/b", key="b1")
    fresh_blog = _mk("Fresh blog", source="Some Blog", hours=0.2, url="https://blog.example/c", key="c1")
    ranked = top_macro_fallback([fresh_blog, old_wire, new_wire], n=3)
    assert [r.title for r in ranked] == ["New wire", "Old wire", "Fresh blog"]
    assert len(top_macro_fallback([fresh_blog, old_wire], n=1)) == 1


def test_top_macro_fallback_empty_safe():
    assert top_macro_fallback([], n=3) == []
    assert top_macro_fallback(None, n=3) == []
    assert top_macro_fallback({}, n=3) == []
    assert top_macro_fallback([], n=0) == []
