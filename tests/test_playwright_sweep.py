"""Playwright sweep: TV resolution, corpus conversion, caps, kill-switch.

Offline only: browser transport is monkeypatched, never launched.
"""

from __future__ import annotations


class _Bundle:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def to_dict(self):
        return dict(self.__dict__)


def test_tv_exchange_resolution_matrix():
    from investment_engine.research.corpus.adapters_tradingview import (
        resolve_tv_symbol,
        tv_exchange_for,
    )

    assert tv_exchange_for("AAPL", "NASDAQ:AAPL") == "NASDAQ"
    assert tv_exchange_for("NCLR.L") == "LSE"
    assert tv_exchange_for("EXI2.DE") == "XETRA"
    assert tv_exchange_for("SU.PA") == "EURONEXT"
    assert tv_exchange_for("IBE.MC") == "BME"
    assert tv_exchange_for("BTC-USD") is None
    assert tv_exchange_for("") is None
    assert tv_exchange_for("VWS.CO") is None  # unknown venue: skip, never guess
    assert resolve_tv_symbol("AAPL", "AAPL", "AAPL_US_EQ", []) == ("AAPL", "NASDAQ")
    assert resolve_tv_symbol("NCLR", "NCLR.L", None, [])[1] == "LSE"
    assert resolve_tv_symbol("SUI", "SUI-USD", None, []) is None
    assert resolve_tv_symbol("", None, None, []) is None
    cfg = [{"broker_symbol": "X_EQ", "tradingview_symbol": "LSE:X"}]
    assert resolve_tv_symbol("X", "X", "X_EQ", cfg) == ("X", "LSE")


def test_bundle_mapping_keeps_tv_keys_distinct():
    from investment_engine.research.corpus.adapters_tradingview import (
        bundle_to_items,
        bundle_to_technicals,
    )

    bundle = _Bundle(symbol="NCLR", exchange="LSE", price=42.5, change_pct=1.2,
                     technicals_rating="Buy", analyst_rating="Buy",
                     key_facts_today="New mine approved.",
                     source="tradingview.com/symbols/LSE-NCLR")
    tech = bundle_to_technicals(bundle)
    assert tech["TV_PRICE"] == 42.5 and tech["TV_TECHNICALS_RATING"] == "Buy"
    assert "RSI_14" not in tech and "Support" not in tech
    items = bundle_to_items(bundle, "NCLR", "2026-09-26T12:00:00+00:00")
    assert len(items) == 2
    assert all(i["published_dt"] == "2026-09-26T12:00:00+00:00" for i in items)
    assert {i["category"] for i in items} == {"company", "analyst"}
    assert "estimate" in items[1]["title"].lower()
    assert bundle_to_technicals(None) == {} and bundle_to_items(None, "X", "t") == []


def test_run_sweep_caps_skips_and_kill_switch(monkeypatch):
    from investment_engine.research.corpus import adapters_tradingview as tv
    from investment_engine.research import web_researcher as wr

    tv.reset()
    calls = []

    def _fake_fetch(symbols, include_financials=True, headless=True,
                    timeout_ms=30000, max_concurrent=1):
        calls.append(list(symbols))
        return [_Bundle(symbol=s, exchange=e, price=10.0,
                        technicals_rating="Neutral", key_facts_today="Fact.",
                        source=f"tradingview.com/symbols/{e}-{s}")
                for s, e in symbols]

    monkeypatch.setattr(wr, "fetch_tradingview_symbols_sync", _fake_fetch)
    cands = [("DEAD", None, None), ("BTC", "BTC-USD", None)] + [
        (f"S{i}", f"S{i}", None) for i in range(10)]
    out = tv.run_sweep(cands, [], max_symbols=8, timeout_ms=5000)
    assert out["stats"]["attempted"] == 8
    assert len(out["technicals"]) == 8 and len(out["items"]) == 8
    assert out["stats"]["skipped"] >= 2  # underivable entries skipped first
    assert sum(len(c) for c in calls) == 8  # serial, capped


def test_run_sweep_block_disables_and_never_raises(monkeypatch):
    from investment_engine.research.corpus import adapters_tradingview as tv
    from investment_engine.research import web_researcher as wr

    tv.reset()

    def _boom(*a, **k):
        raise RuntimeError("403 blocked by challenge")

    monkeypatch.setattr(wr, "fetch_tradingview_symbols_sync", _boom)
    out = tv.run_sweep([("AAPL", "AAPL", None)], [])
    assert out["technicals"] == {} and out["items"] == []
    assert tv.disabled()
    out2 = tv.run_sweep([("AAPL", "AAPL", None)], [])
    assert out2["stats"]["blocked"] == 1
    tv.reset()
    assert not tv.disabled()


def test_run_sweep_missing_browser_degrades(monkeypatch):
    from investment_engine.research.corpus import adapters_tradingview as tv
    from investment_engine.research import web_researcher as wr

    tv.reset()

    def _nobrowser(*a, **k):
        raise RuntimeError("playwright not installed")

    monkeypatch.setattr(wr, "fetch_tradingview_symbols_sync", _nobrowser)
    out = tv.run_sweep([("AAPL", "AAPL", None)], [])
    assert out == {"technicals": {}, "items": [],
                   "stats": {"attempted": 0, "hits": 0, "skipped": 1, "blocked": 0}}


_TV_SAMPLE = (
    "Skip to main content AAPL 341.07 USD +5.15 +1.53% At close Overview | "
    "Technicals\ufeff | Summarizing what the indicators are suggesting. | Neutral | "
    "Analyst rating\ufeff | An aggregate view of professional's ratings. | Buy | "
    "Key facts today\ufeff | A jury found Apple infringed two patents and ordered "
    "payment over $5.7 billion. | 1 | Apple extended patent license with Qualcomm "
    "through 2027. | 2 | "
)


def test_extract_tv_text_signals_real_shapes():
    from investment_engine.research.web_researcher import extract_tv_text_signals

    out = extract_tv_text_signals(_TV_SAMPLE, "AAPL")
    assert out["price"] == 341.07
    assert out["change_pct"] == "+1.53%"
    assert out["technicals_rating"] == "Neutral"
    assert out["analyst_rating"] == "Buy"
    assert "jury found Apple" in out["key_facts_today"]
    assert "patent license with Qualcomm" in out["key_facts_today"]


def test_extract_tv_text_signals_no_cross_block_leak():
    from investment_engine.research.web_researcher import extract_tv_text_signals

    # Analyst-label tab text followed (89 chars later) by the Technicals gauge:
    # must NOT inherit the wrong rating.
    text = ("Analyst rating tab | ideas | " + "x" * 60
            + "Technicals | Summarizing what the indicators are suggesting. | Sell |")
    out = extract_tv_text_signals(text, "XYZ")
    assert out.get("technicals_rating") == "Sell"
    assert out.get("analyst_rating") in (None, "Sell")


def test_extract_tv_text_signals_empty_safe():
    from investment_engine.research.web_researcher import extract_tv_text_signals

    assert extract_tv_text_signals("", "AAPL") == {}
    assert extract_tv_text_signals(None, "AAPL") == {}
    assert extract_tv_text_signals("no relevant content here", "AAPL") == {}


def test_extract_tv_text_signals_unicode_minus_change():
    """Live 2026-10-10: TV renders U+2212 MINUS ("-1.11%")."""
    from investment_engine.research.web_researcher import extract_tv_text_signals

    text = "AAPL\n336.64\nUSD\n\u22123.78\n\u22121.11%\nSee on Supercharts"
    out = extract_tv_text_signals(text, "AAPL")
    assert out["price"] == 336.64
    assert out["change_pct"] == "-1.11%"


def test_extract_tv_text_signals_no_gauge_on_overview_nav_tab():
    """Overview body only carries the Technicals NAV TAB (no values) —
    must not invent a rating (live 2026-10-10 shape)."""
    from investment_engine.research.web_researcher import extract_tv_text_signals

    text = ("OverviewFinancialsDocumentsNewsCommunityTechnicalsForecasts"
            "1 day -1.11% 5 days 1.01% Key facts today | Shares near high. | 1 |")
    out = extract_tv_text_signals(text, "AAPL")
    assert "technicals_rating" not in out and "analyst_rating" not in out


def test_extract_tv_text_signals_technicals_subpage_summary_gauge():
    """Technicals subpage shape (live 2026-10-10): standalone Summary
    heading + gauge value, then counter rows."""
    from investment_engine.research.web_researcher import extract_tv_text_signals

    text = ("Indicators' summary\nSee technical analysis overview.\n"
            "Oscillators\nSell\nSell\n2\nNeutral\n9\nBuy\n0\n"
            "Summary\nBuy\nSell\n3\nNeutral\n10\nBuy\n13\n"
            "Moving Averages\nStrong buy\n")
    out = extract_tv_text_signals(text, "AAPL")
    assert out["technicals_rating"] == "Buy"


def test_extract_tv_text_signals_summary_gauge_needs_standalone_heading():
    """The descriptive sentence ('...in the Summary gauge, where... buy,
    sell...') must not match — only a standalone heading counts."""
    from investment_engine.research.web_researcher import extract_tv_text_signals

    text = ("It includes key data summed up in the Summary gauge, where you "
            "can instantly see whether indicators suggest a buy, sell, or "
            "neutral signal.")
    out = extract_tv_text_signals(text, "AAPL")
    assert "technicals_rating" not in out


def test_tech_summary_line_renders_tv_readings():
    from investment_engine.main import _tech_summary_line

    line = _tech_summary_line("NCLR", {"TV_TECHNICALS_RATING": "Buy",
                                       "TV_ANALYST_RATING": "Strong Buy"})
    assert "TV gauge: Buy" in line and "TV analyst: Strong Buy" in line
    plain = _tech_summary_line("AAPL", {"RSI_14": 60.0})
    assert "TV gauge" not in plain and "RSI=60.0" in plain


def test_merge_calendar_earnings_helper():
    from investment_engine.main import _merge_calendar_earnings as merge

    class _Ev:
        def __init__(self, symbol, date):
            self.symbol = symbol
            self.date = date

    earnings = {"AAPL": "timeout", "MSFT": "2026-10-28", "ETF": "no_data"}
    filled = merge(earnings, [_Ev("AAPL", "2026-10-30"), _Ev("MSFT", "2026-11-01"),
                              _Ev("XYZ", "2026-10-01")],
                   ["AAPL"], lambda s: s)
    assert filled == 1 and earnings["AAPL"] == "2026-10-30"
    assert earnings["MSFT"] == "2026-10-28" and earnings["ETF"] == "no_data"


def test_layer2_fill_retries_calendar_then_merges():
    from investment_engine.main import _fill_earnings_layer2 as fill

    class _Ev:
        def __init__(self, symbol, date):
            self.symbol = symbol
            self.date = date

    calls = []
    sleeps = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("calendar down")
        return [_Ev("AAPL", "2026-10-30")]

    earnings = {"AAPL": "timeout"}
    out = fill(earnings, ["AAPL"], fetch_calendar=flaky,
               fetch_yahoo_date=lambda y: (_ for _ in ()).throw(AssertionError("no fallback expected")),
               to_display=lambda s: s, to_yahoo=lambda d: None,
               sleep=lambda s: sleeps.append(s))
    assert out == 1 and earnings["AAPL"] == "2026-10-30"
    assert len(calls) == 3 and len(sleeps) == 2  # 3 attempts + 2 backoff sleeps


def test_layer2_fill_falls_back_to_yfinance_dates():
    from investment_engine.main import _fill_earnings_layer2 as fill

    def dead():
        raise RuntimeError("calendar down")

    earnings = {"AAPL": "timeout", "MSFT": "no_data"}
    out = fill(earnings, ["AAPL", "MSFT"], fetch_calendar=dead,
               fetch_yahoo_date=lambda y: {"AAPL": "2026-11-05"}.get(y),
               to_display=lambda s: s, to_yahoo=lambda d: d,
               sleep=lambda s: None)
    assert out == 1 and earnings["AAPL"] == "2026-11-05"
    assert earnings["MSFT"] == "no_data"


def test_layer2_fill_never_raises_when_everything_fails():
    from investment_engine.main import _fill_earnings_layer2 as fill

    def dead():
        raise RuntimeError("calendar down")

    def bad_yahoo(y):
        raise RuntimeError("yahoo down")

    earnings = {"AAPL": "timeout"}
    assert fill(earnings, ["AAPL"], fetch_calendar=dead,
                fetch_yahoo_date=bad_yahoo,
                to_display=lambda s: s, to_yahoo=lambda d: d,
                sleep=lambda s: None) == 0
    assert earnings["AAPL"] == "timeout"


def test_no_bare_excepts_in_playwright_paths():
    import pathlib
    import re

    for rel in ("investment_engine/research/market_data.py",
                "investment_engine/research/web_researcher.py"):
        src = pathlib.Path(rel).read_text(encoding="utf-8")
        bare = [i + 1 for i, l in enumerate(src.splitlines())
                if re.match(r"\s*except\s*:\s*(#.*)?$", l)]
        assert not bare, (rel, bare)


def test_rss_fallback_source_table():
    from investment_engine.research.news_sources import RSS_FALLBACK_SOURCES

    urls = [s["rss_url"] for s in RSS_FALLBACK_SOURCES]
    assert len(urls) == 3 and all(u.startswith("https://") for u in urls)
    assert all(s["tier"] == 2 for s in RSS_FALLBACK_SOURCES)
