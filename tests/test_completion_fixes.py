"""Completion fixes: ETF earnings guard, FinViz pre-gate, order-plans section.

Offline only. No live API.
"""

from __future__ import annotations


def test_is_etf_like_matrix():
    from investment_engine.research.market_data import is_etf_like

    assert is_etf_like({"name": "iShares Physical Gold ETC", "group": "LONG_RUN_DCA"})
    assert is_etf_like({"name": "WisdomTree BioRevolution UCITS ETF", "group": "LONG_RUN_DCA"})
    assert is_etf_like({"name": "VanEck Space Innovators UCITS ETF", "group": "TECH_PIE"})
    assert is_etf_like({"name": "Bitcoin", "group": "CRYPTO"})
    assert not is_etf_like({"name": "Apple", "group": "TECH_PIE"})
    assert not is_etf_like({"name": "Microsoft", "group": "TECH_PIE"})
    assert not is_etf_like({"name": "Vestas Wind Systems", "group": "LONG_RUN_DCA"})
    assert not is_etf_like(None)
    assert not is_etf_like({"name": "GLD"})


def test_should_use_finviz_matrix():
    from investment_engine.research.technical_analysis import should_use_finviz

    assert should_use_finviz("AAPL")
    assert should_use_finviz("BRK.B")
    assert not should_use_finviz("EXI2.DE")
    assert not should_use_finviz("RWE.DE")
    assert not should_use_finviz("BTC-USD")
    assert not should_use_finviz("")
    assert not should_use_finviz(None)


def test_regime_skips_finviz_quote_for_non_us():
    import pathlib

    src = pathlib.Path("investment_engine/research/market_regime.py").read_text(encoding="utf-8")
    assert "should_use_finviz" in src


def test_litm_display_resolves():
    from investment_engine.portfolio.symbols import support_state, to_yahoo_symbol

    assert to_yahoo_symbol("LITMm_EQ") == "LITM.L"
    assert support_state("LITM.L", "market_data") == "SUPPORTED"


def _brief_kwargs(**over):
    base = dict(generated_at="2026-09-26 12:00 CEST", regime_result=None,
                recon={"status": "PASS", "total_equity": 1000.0}, rows=[],
                monitoring_items=[], earnings_7d={}, decision_news=[], ideas=[],
                cash_line="n/a")
    base.update(over)
    return base


def test_order_plans_section_renders_only_when_gated():
    from investment_engine.reporting.documents import render_brief

    plans = {"plans": [{"ticker": "AAPL", "direction": "BUY",
                        "indicative_notional_eur": 100.0,
                        "binding_constraint": "cash", "sleeve": "tactical"}]}
    md = render_brief(**_brief_kwargs(order_plans=plans))
    assert "## Advisory Order Plans (not executed)" in md
    assert "Advisory only — not executed" in md
    assert "AAPL" in md
    md_empty = render_brief(**_brief_kwargs(order_plans={"plans": []}))
    assert "Advisory Order Plans" not in md_empty
    md_fail = render_brief(**_brief_kwargs(
        order_plans=plans, recon={"status": "FAIL", "total_equity": 1000.0}))
    assert "Advisory Order Plans" not in md_fail
