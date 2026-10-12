"""Catalyst radar V1: explicit future dates in linked news titles.

Pure offline tests, no network. Informational only — no signal changes.
"""
from __future__ import annotations

from datetime import datetime, timezone


def _now():
    return datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


def _item(title, links, url="https://example.com/x"):
    return {"title": title, "url": url, "links": links}


def test_future_dated_event_kept():
    from investment_engine.research.news_engine import extract_catalysts

    out = extract_catalysts(
        [_item("Weekly Recap: GTA VI Nov 19 release and subpoenas", ["TTWO"])],
        now=_now())
    assert out == [{"ticker": "TTWO", "date": "2026-11-19",
                    "title": "Weekly Recap: GTA VI Nov 19 release and subpoenas",
                    "url": "https://example.com/x"}]


def test_past_and_missing_dates_skipped():
    from investment_engine.research.news_engine import extract_catalysts

    out = extract_catalysts([
        _item("Recap: September 30 quarter close", ["TTWO"]),
        _item("Analysts weigh in with no date at all", ["TTWO"]),
        _item("Fake Feb 30 launch event", ["TTWO"]),
    ], now=_now())
    assert out == []


def test_year_rollover_and_explicit_year():
    from investment_engine.research.news_engine import extract_catalysts

    out = extract_catalysts([
        _item("CES Jan 5 keynote preview", ["NVDA"]),
        _item("Back to May 2025 launch retrospective", ["NVDA"]),
    ], now=_now())
    assert [(e["ticker"], e["date"]) for e in out] == [("NVDA", "2027-01-05")]


def test_macro_skipped_multi_ticker_expands_sorted():
    from investment_engine.research.news_engine import extract_catalysts

    out = extract_catalysts([
        _item("Macro Dec 1 outlook", ["MACRO"]),
        _item("Chips rally into Dec 2 summit", ["NVDA", "INTC"]),
    ], now=_now())
    assert [(e["ticker"], e["date"]) for e in out] == [
        ("INTC", "2026-12-02"), ("NVDA", "2026-12-02")]


def test_garbage_never_raises():
    from investment_engine.research.news_engine import extract_catalysts

    assert extract_catalysts(None) == []
    assert extract_catalysts("nope", now="garbage") == []
    assert extract_catalysts([None, 42, {}], now=_now()) == []
    assert extract_catalysts([_item("Nov 19 launch", [])], now=_now()) == []


def test_brief_renders_catalyst_section():
    from types import SimpleNamespace

    from investment_engine.reporting.documents import render_brief

    radar = [{"ticker": "TTWO", "date": "2026-11-19",
              "title": "Weekly Recap: GTA VI Nov 19 release",
              "url": "https://example.com/gta"}]
    md = render_brief(
        generated_at="2026-10-12 12:00 UTC",
        regime_result=SimpleNamespace(regime="PEAK_HOLD", timeframes={}),
        recon={"status": "PASS"}, rows=[], monitoring_items=[],
        earnings_7d={}, decision_news=[], ideas=[], cash_line="cash",
        catalyst_radar=radar)
    assert "## Catalyst Radar" in md
    assert "TTWO" in md and "Nov 19" in md
    md_empty = render_brief(
        generated_at="2026-10-12 12:00 UTC",
        regime_result=SimpleNamespace(regime="PEAK_HOLD", timeframes={}),
        recon={"status": "PASS"}, rows=[], monitoring_items=[],
        earnings_7d={}, decision_news=[], ideas=[], cash_line="cash")
    assert "No dated catalysts found in linked news." in md_empty
