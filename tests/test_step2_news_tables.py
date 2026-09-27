"""Step 2: SK sources, TV symbol-news adapter, NaN-safe tables.

Offline only: stubbed HTTP sessions, synthetic rows/DataFrames, no network.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone


class _StubResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


class _StubSession:
    def __init__(self, payload, status=200):
        self._payload = payload
        self._status = status
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return _StubResp(self._payload, self._status)


def _tv_payload(now_ts):
    return {"items": [
        {"id": "1", "title": "Apple beats estimates", "provider": "Reuters",
         "link": "https://www.tradingview.com/news/reuters:1/",
         "published": now_ts - 3600, "storyPath": "/news/reuters:1/"},
        {"id": "2", "title": "Old news", "provider": "Reuters",
         "link": "https://e.example/old", "published": now_ts - 10 * 86400},
        {"id": "3", "title": "", "provider": "X", "link": "https://e.example/x",
         "published": now_ts - 100},
    ]}


def test_tvnews_items_filtered_and_shaped():
    from investment_engine.research.corpus import adapters_tvnews as tvn

    tvn.reset()
    now_ts = datetime.now(timezone.utc).timestamp()
    sess = _StubSession(_tv_payload(now_ts))
    res = tvn.fetch_symbol_news([("AAPL", "AAPL", "AAPL_US_EQ")], session=sess)
    assert res["stats"] == {"attempted": 1, "symbols": 1, "items": 1}
    item = res["items"][0]
    assert item["title"] == "Apple beats estimates"
    assert item["source"] == "TradingView/Reuters"
    assert item["ticker_tags"] == ["AAPL"] and item["ticker_match"] == ["AAPL"]
    assert item["tier"] == 2 and item["category"] == "tv_symbol"
    assert "published" in item and item["published"]  # renderer-readable date
    assert sess.calls == 1


def test_tvnews_block_disables_and_caps():
    from investment_engine.research.corpus import adapters_tvnews as tvn

    tvn.reset()
    sess = _StubSession({}, status=403)
    res = tvn.fetch_symbol_news([("AAPL", "AAPL", None), ("MSFT", "MSFT", None)],
                                session=sess)
    assert tvn.disabled()
    assert res["items"] == []
    assert sess.calls == 1  # stopped at first block, second symbol untouched
    tvn.reset()

    # crypto / underivable venues are skipped, never guessed
    sess2 = _StubSession(_tv_payload(datetime.now(timezone.utc).timestamp()))
    res2 = tvn.fetch_symbol_news([("BTC", "BTC-USD", None), ("X", None, None)],
                                 session=sess2)
    assert res2["items"] == [] and sess2.calls == 0


def test_slovak_source_tables_current():
    from investment_engine.research.news_sources import SLOVAK_NEWS_SOURCES

    assert set(SLOVAK_NEWS_SOURCES) == {"aktuality.sk", "pravda.sk", "teraz.sk", "googlenews-sk"}
    assert SLOVAK_NEWS_SOURCES["pravda.sk"]["rss_url"] == "https://www.pravda.sk/spravy/rss/xml"
    assert "sme.sk" not in SLOVAK_NEWS_SOURCES

    import inspect
    import investment_engine.research.news_engine as ne

    src = inspect.getsource(ne.StrictNewsFetcher.fetch_slovak_news)
    assert "sme.sk/rss" not in src and "pravda.sk/rss/" not in src
    assert "pravda.sk/spravy/rss/xml" in src and "teraz.sk/rss" in src
    assert "news.google.com/rss?hl=sk" in src


def test_fnum_feur_rsi_nan_safe():
    from investment_engine.reporting.documents import _feur, _fnum, _rsi_of

    assert _fnum(float("nan")) == "n/a"
    assert _fnum(float("inf")) == "n/a"
    assert _feur(float("nan")) == "n/a"
    assert _fnum(None) == "n/a" and _feur(None) == "n/a"
    assert _fnum(12.345) == "12.35"
    assert _rsi_of({"AAPL": {"RSI_14": float("nan")}}, "AAPL") is None
    assert _rsi_of({"AAPL": {"RSI_14": 55.5}}, "AAPL") == 55.5


def test_snapshot_row_carries_lot_id_and_broker_only_line():
    from investment_engine.reporting.documents import _snapshot_row_lines, render_snapshot

    row = {"display_symbol": "AAPL", "internal_id": "APCd_EQ", "company": "Apple",
           "quantity": 1.0, "support": float("nan"), "resistance": float("inf"),
           "market_data_state": "BROKER_ONLY", "pnl_validated": True}
    line = _snapshot_row_lines(row, {})[0]
    assert "APCd_EQ" in line  # lot identity disambiguates same-display rows
    assert "nan" not in line and "inf" not in line.replace("Info", "")

    md = render_snapshot(
        generated_at="2026-09-26 21:00", recon={"status": "PASS"},
        cash={}, rows=[row], monitoring_by_display={}, earnings_status={})
    assert "| Lot / Broker ID |" in md
    assert "- Broker-only positions" in md and "APCd" in md
