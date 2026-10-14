"""T04 status/coverage + T05 sizing/exposure from fixtures. Offline."""
from __future__ import annotations

import json
import pathlib

from scripts.decision_log import append_record, build_record, lookup_prior
from investment_engine.portfolio import metrics as metrics

FIX = pathlib.Path(__file__).parent / "fixtures"


def _t04():
    return json.loads((FIX / "t04_coverage.json").read_text(encoding="utf-8"))


def _t05():
    return json.loads((FIX / "t05_sizing.json").read_text(encoding="utf-8"))


def test_status_and_coverage(tmp_path):
    data = _t04()
    rec = build_record(dict(data["log_record_X"], asset={"ticker": "X"}),
                       created_utc="20260310T120000Z")
    append_record(rec, log_dir=tmp_path)
    prior = lookup_prior("X", log_dir=tmp_path)
    assert prior is not None
    assert prior.get("portfolio_status") == "discussed_only"
    cov = metrics.classify_coverage(data["accounts"])
    assert cov["complete"] == ["A"] and cov["partial"] == ["B"]
    out = metrics.position_values(data["holdings_A"], base_ccy="EUR",
                                  fx={"EUR": 1.0})
    assert "covered" in metrics.classify_coverage(
        data["accounts"])["denominator"]
    assert out["positions"][0]["weight_pct"] == 100.0


def test_unknown_vs_not_found_depends_on_coverage():
    full = metrics.classify_coverage([{"id": "A", "coverage": "complete"}])
    partial = metrics.classify_coverage([{"id": "A", "coverage": "complete"},
                                         {"id": "B", "coverage": "partial"}])
    assert full["partial"] == [] and partial["partial"] == ["B"]
    assert metrics.classify_holding_status(held=True) == "confirmed_current_holding"
    assert metrics.classify_holding_status(
        held=False, log_status="discussed_only") == "discussed_only"
    assert metrics.classify_holding_status(
        held=False, coverage_complete=False) == "unknown"
    assert metrics.classify_holding_status(
        held=False, coverage_complete=True) == "not_found"


def test_t05_sizing_cases():
    data = _t05()
    pv = data["portfolio_value"]
    c1 = metrics.size_position(portfolio_value=pv, adverse_move_pct=40,
                               loss_budget_pct=None,
                               caps=data["cases"]["case1"]["caps"])
    assert c1["notional_pct"] == 2.5 and c1["budget_defaulted"] is True
    c2 = metrics.size_position(portfolio_value=pv, adverse_move_pct=25,
                               loss_budget_pct=2,
                               caps=data["cases"]["case2"]["caps"])
    assert c2["notional_pct"] == 5.0
    assert c2["binding_constraint"] == "max_single_stock_pct"
    c3 = metrics.size_position(portfolio_value=pv, adverse_move_pct=40,
                               loss_budget_pct=None, caps={})
    assert c3["provisional"] is True and c3["range_pct"][1] <= 5.0
    c4 = metrics.size_position(
        portfolio_value=pv, adverse_move_pct=40, loss_budget_pct=None,
        caps=data["cases"]["case4"]["caps"],
        max_drawdown_tolerance_pct=data["cases"]["case4"]["max_drawdown_tolerance_pct"])
    assert c4["notional_pct"] == 2.5


def test_t05_resulting_exposure():
    data = _t05()
    out = metrics.resulting_exposure(
        data["holdings_3"], data["candidate_D"], caps={"max_single_stock_pct": 5})
    assert out["post_weight_pct"] == 9.0909
    assert out["post_sector_weight_pct"] == 27.2727
    assert out["post_currency_weight_pct"] == 54.5455
    assert out["breach"] is True
