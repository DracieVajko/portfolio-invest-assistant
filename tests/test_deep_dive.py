"""Deep-dive brief: 15 fixed sections, deterministic, no LLM prose.

Offline only. Missing input renders "No coverage this run." — never invented.
"""

from __future__ import annotations


def _result(**over):
    base = {
        "portfolio_rows": [], "monitoring_items": [], "regime_result": None,
        "t212_data": {"account_summary": {"total_equity": 10000.0},
                      "cash": {"free": 500.0, "pie_cash": 0.0, "blocked": 0.0}},
        "news_by_symbol": {}, "decision_news": [], "earnings_7d": {},
        "ideas": [], "trump_tracking": {}, "commodity_news": {},
        "analyst_news": {}, "order_plans": {"plans": []},
    }
    base.update(over)
    return base


def _call(result):
    from investment_engine.reporting.documents import build_deep_dive

    return build_deep_dive(
        generated_at="2026-09-26 12:00 CEST", run_id="t1", regime_result=None,
        recon={"status": "UNKNOWN"}, rows=result.get("portfolio_rows", []),
        monitoring_items=result.get("monitoring_items", []),
        t212_data=result.get("t212_data"),
        news_by_symbol=result.get("news_by_symbol"),
        decision_news=result.get("decision_news", []),
        earnings_7d=result.get("earnings_7d", {}), ideas=result.get("ideas", []),
        trump_tracking=result.get("trump_tracking"),
        commodity_news=result.get("commodity_news"),
        analyst_news=result.get("analyst_news"),
        order_plans=result.get("order_plans"))


def test_fifteen_sections_in_order():
    md = _call(_result())
    headers = [f"## {i}." for i in range(1, 16)]
    idx = [md.index(h) for h in headers]
    assert idx == sorted(idx)
    assert md.count("No coverage this run.") >= 4


def test_empty_result_never_crashes():
    md = _call({})
    assert md.startswith("# Portfolio Deep Dive")
    assert "No coverage this run." in md


def test_health_score_values():
    from investment_engine.reporting.documents import portfolio_health_score

    assert portfolio_health_score([], "FAIL") == (40, "CRITICAL")
    assert portfolio_health_score([{"signal": "SELL"}], "PASS") == (70, "WATCH")
    assert portfolio_health_score([{"signal": "HOLD"}], "PASS") == (90, "HEALTHY")
    assert portfolio_health_score([], "UNKNOWN") == (70, "WATCH")


def test_populated_sections():
    md = _call(_result(
        decision_news=[{"title": "t", "url": "https://e.example/x", "source": "S",
                        "published": "today"}],
        earnings_7d={"in_window": [{"display": "AAPL", "company": "Apple",
                                    "date": "2026-10-30", "status": "confirmed"}]},
        ideas=[{"ticker": "NVDA", "reason": "r"}],
        trump_tracking={"tariffs": [{"title": "tt", "url": "https://e.example/y",
                                     "source": "S"}]},
        commodity_news={"gold": [{"title": "g", "url": "https://e.example/z",
                                  "source": "S"}]},
        analyst_news={"AAPL": [{"title": "a", "url": "https://e.example/w",
                                "source": "S"}]},
        news_by_symbol={"AAPL": [{"title": "FDA trial", "preview": ""}]},
        order_plans={"plans": [{"ticker": "AAPL", "direction": "BUY",
                                "indicative_notional_eur": 100.0,
                                "binding_constraint": "cash", "sleeve": "tactical"}]},
    ))
    assert "FDA trial" in md  # healthcare keyword scan
    assert "No coverage this run." in md  # crypto/dividend/ESG stay honest
    assert "AAPL" in md and "BUY" in md  # advisory plans render gated content
    assert "Not executed" not in md  # guard text lives in brief, plans carry data here


def test_no_sized_orders_language():
    import re

    md = _call(_result())
    assert not re.search(r"(?i)\b(buy|sell)\s+\d+\s+shares?\b", md)
    assert "advisory" in md.lower() or "No advisory plans" in md
