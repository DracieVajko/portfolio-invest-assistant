"""Report truthfulness: dynamic attribution, normalized averages, brief drivers,
dividends, snapshot consistency (Step 4). Offline only (stubbed yfinance)."""
from __future__ import annotations

from types import SimpleNamespace


def test_stage_origin_and_format():
    from investment_engine.providers.attribution import format_stage_line, stage_origin

    assert stage_origin("LM Studio") == "local"
    assert stage_origin("llama.cpp") == "local"
    assert stage_origin("Ollama") == "local"
    assert stage_origin("Gemini") == "API"
    assert stage_origin("Mistral") == "API"
    assert stage_origin("deterministic") == "Python"
    assert stage_origin("deterministic(skills)") == "Python"
    line = format_stage_line("decision", {"provider": "Gemini",
                                          "model_served": "gemini-2.5-flash",
                                          "fallback_depth": 1})
    assert line == "decision: Gemini/gemini-2.5-flash (API, depth 1)"
    assert format_stage_line("risk", {"provider": "deterministic",
                                      "model_served": "deterministic(skills)",
                                      "fallback_depth": 99}) == "risk: deterministic (Python)"


def test_model_attribution_dynamic_no_static_chain():
    from investment_engine.reporting.regime_report import AIContextReportBuilder

    stages = {"decision": {"provider": "Gemini", "model_served": "gemini-2.5-flash",
                           "fallback_depth": 1},
              "risk": {"provider": "deterministic",
                       "model_served": "deterministic(skills)", "fallback_depth": 99}}
    md = AIContextReportBuilder()._build_model_attribution({}, stages)
    assert "decision: Gemini/gemini-2.5-flash (API, depth 1)" in md
    assert "risk: deterministic (Python)" in md
    assert "Fallback 1" not in md and "Fallback 5" not in md
    assert "finr1 for math" not in md


def _snap_pos(avg_raw, cur_raw, factor=1, ccy="USD"):
    ins = SimpleNamespace(broker_instrument_id="TST_EQ", display_symbol="TST",
                          isin="US0000000000", currency=ccy)
    fx = SimpleNamespace(source_currency=ccy, minor_unit_factor=factor,
                         normalized_price=(cur_raw / factor if cur_raw is not None else None))
    return SimpleNamespace(included_in_position_total=True, instrument=ins, fx_audit=fx,
                           quantity=10.0, average_price_raw=avg_raw,
                           current_price_raw=cur_raw, broker_market_value_eur=99.96,
                           valuation_source="live_fx", mapping_status="OK",
                           exclusion_reason="")


def test_positions_normalized_average_and_legend():
    from investment_engine.reporting.regime_report import AIContextReportBuilder

    snap = SimpleNamespace(positions=[_snap_pos(1900.0, 2024.7, factor=100, ccy="GBP"),
                                      _snap_pos(1.85, 1.73, factor=1, ccy="USD")])
    md = AIContextReportBuilder()._build_broker_portfolio_namespace([], None, None, snap)
    assert "Legend:" in md
    assert "19.0" in md  # 1900 pence -> 19.0 GBP average (was a copy of current)
    assert "20.247" in md  # current untouched


def test_brief_triggers_and_drivers():
    from investment_engine.reporting.documents import build_intelligence_brief

    result = {
        "portfolio_rows": [{"display_symbol": "TTWO", "weight": 12.0}],
        "reconciliation": {"status": "PASS", "cash_delta": -0.86},
        "regime_result": None,
        "t212_data": {"account_summary": {"total_equity": 3265.37},
                      "cash": {"free": 0.0},
                      "fx_rates_used": {"source": "live-fx"}},
        "account_performance": {"net_pnl_after_costs_eur": 63.86, "return_pct": 1.99},
        "monitoring_items": [{"display": "TTWO", "presentation": "SELL",
                              "triggers": "weight 12% over cap", "weight": 12.0}],
        "decision_news": [], "earnings_7d": {"in_window": []}, "ideas": [],
        "providers_per_stage": {
            "decision": {"provider": "Gemini", "model_served": "gemini-2.5-flash",
                         "fallback_depth": 1}},
    }
    md = build_intelligence_brief(result)
    assert "weight 12% over cap" in md  # trigger reason shown, not bare SELL
    assert "decision: Gemini/gemini-2.5-flash (API, depth 1)" in md
    assert "## Portfolio drivers (no fresh catalysts)" in md
    assert "TTWO 12.0%" in md
    assert len(md.splitlines()) <= 60


def test_deep_dive_dividends_esg_appendix():
    from investment_engine.reporting.documents import build_deep_dive

    md = build_deep_dive(
        generated_at="2026-09-27 09:00", run_id="test1234", regime_result=None,
        recon={"status": "PASS"}, rows=[], monitoring_items=[], t212_data={},
        news_by_symbol={}, decision_news=[], earnings_7d={"in_window": []},
        ideas=[], trump_tracking={}, commodity_news={}, analyst_news={},
        order_plans=None,
        dividends_12m={"O": {"ttm_per_share": 2.5, "yield_pct": 4.41,
                             "last_ex_date": "2026-09-01", "payments_12m": 4}},
        stage_attribution={"summary": {"provider": "Mistral",
                                       "model_served": "open-mistral-nemo",
                                       "fallback_depth": 0}},
        result_modes={"api_execution_mode": "API_ONLY"})
    assert "O**: TTM yield 4.41%" in md
    assert "No ESG feed wired" in md
    assert "summary: Mistral/open-mistral-nemo (API, depth 0)" in md
    assert "Execution mode: API_ONLY" in md
    assert "see run_manifest `providers_per_stage`" not in md


def test_snapshot_consistency_stable_with_timestamp_keys():
    from investment_engine.reporting.regime_report import AIContextReportBuilder

    t212 = {"account_summary": {"timestamp": "2026-09-27T10:02:11.538320"}}
    md = AIContextReportBuilder()._build_snapshot_consistency(t212, {}, None)
    assert "STATUS: STABLE" in md
    assert "insufficient timestamps" not in md


def test_snapshot_consistency_naive_local_vs_utc():
    """Naive broker timestamps are local machine time, not UTC.

    Regression: run 066ac8be showed a 7208s "unstable" spread for a ~8s real
    gap because naive CEST was read as UTC against an aware UTC FX stamp.
    """
    from datetime import datetime, timedelta, timezone

    from investment_engine.reporting.regime_report import AIContextReportBuilder

    now_local = datetime.now().astimezone().replace(microsecond=0)
    t212 = {"account_summary": {"timestamp": now_local.isoformat()}}
    fx_ts = (now_local - timedelta(seconds=8)).astimezone(timezone.utc).timestamp()
    md = AIContextReportBuilder()._build_snapshot_consistency(
        t212, {"retrieved_at": fx_ts}, None)
    assert "STATUS: STABLE" in md
    assert "SNAPSHOT_TIMING_UNSTABLE" not in md


def test_fetch_dividend_summary_stubbed(monkeypatch):
    import datetime as _dt

    import pandas as pd

    import investment_engine.research.market_data as mdmod

    now = _dt.datetime.now(_dt.timezone.utc)
    divs = pd.Series(
        [0.625, 0.625, 0.625, 0.625],
        index=pd.DatetimeIndex([now - _dt.timedelta(days=30 * i) for i in range(1, 5)]),
    )

    class _Close:
        iloc = [100.0]

    class _Hist:
        empty = False

        def __getitem__(self, key):
            assert key == "Close"
            return _Close()

    class _FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        @property
        def dividends(self):
            return divs

        def history(self, period="5d", interval="1d"):
            return _Hist()

    import yfinance as yf
    monkeypatch.setattr(yf, "Ticker", _FakeTicker)
    out = mdmod.fetch_dividend_summary("O")
    assert out is not None
    assert out["ttm_per_share"] == 2.5
    assert out["yield_pct"] == 2.5
    assert out["payments_12m"] == 4
    assert out["last_ex_date"] == divs.index[-1].date().isoformat()
