"""Portfolio metrics: hand-computed expectations (AGENTS.md 9.2/9.3, T05)."""
from __future__ import annotations

import pytest

from investment_engine.portfolio import metrics as m


def test_position_values_gbx_and_weights():
    """T02-shape: 8250 GBX x10 @1.15 -> GBP 825.00, EUR 948.75."""
    out = m.position_values(
        [{"symbol": "X", "quantity": 10, "price": 8250, "currency": "GBP",
          "price_unit": "GBX", "sector": "E"}],
        base_ccy="EUR", fx={"GBP": 1.15})
    pos = out["positions"][0]
    assert pos["price_native"] == 82.50
    assert pos["value_native"] == 825.00
    assert pos["value_base"] == pytest.approx(948.75)
    assert pos["weight_pct"] == pytest.approx(100.0)
    assert out["denominator"] == "covered positions + cash"


def test_missing_unit_raises_not_guesses():
    with pytest.raises(ValueError):
        m.position_values([{"symbol": "X", "quantity": 1, "price": 5.0,
                            "currency": "EUR"}],
                          base_ccy="EUR", fx={"EUR": 1.0})


def test_concentration_hand_computed():
    out = m.concentration({"A": 50.0, "B": 30.0, "C": 20.0})
    assert out["effective_n"] == pytest.approx(1 / 0.38)
    assert out["top5_weight_pct"] == pytest.approx(100.0)


def test_pl_fx_split_hand_computed():
    out = m.position_pl(quantity=10, avg_cost=100.0, cost_ccy_fx=0.9,
                        price=110.0, price_ccy_fx=0.95)
    assert out["pnl_local"] == pytest.approx(100.0)
    assert out["pnl_base"] == pytest.approx(145.0)
    assert out["fx_contribution"] == pytest.approx(50.0)


def test_clusters_deterministic():
    out = m.correlation_clusters({"A": [1, 2, 3, 4, 5], "B": [2, 4, 6, 8, 10],
                                  "C": [5, 1, 4, 2, 3]})
    assert sorted(map(sorted, out)) == [["A", "B"], ["C"]]


def test_risk_share_symmetric_pair():
    out = m.risk_contribution({"A": 60.0, "B": 40.0}, {"A": 0.2, "B": 0.3}, {})
    assert out["A"]["vol_share"] == pytest.approx(0.5)
    assert out["B"]["vol_share"] == pytest.approx(0.5)


def test_lookthrough_unknown_without_files():
    assert m.lookthrough_overlap(None, {"AAPL": 5.0})["overlap"] == "unknown"
    got = m.lookthrough_overlap({"AAPL": 10.0, "MSFT": 5.0}, {"AAPL": 3.0, "NVDA": 4.0})
    assert got["overlap_pct"] == pytest.approx(3.0)
    assert got["shared_issuers"] == ["AAPL"]


def test_sizing_case1_default_budget():
    out = m.size_position(portfolio_value=10000, adverse_move_pct=40,
                          loss_budget_pct=None, caps={"max_single_stock_pct": 5})
    assert out["notional_pct"] == pytest.approx(2.5)
    assert out["notional_value"] == pytest.approx(250.0)
    assert out["budget_defaulted"] is True
    assert out["binding_constraint"] == "formula"


def test_sizing_case2_capped():
    out = m.size_position(portfolio_value=10000, adverse_move_pct=25,
                          loss_budget_pct=2, caps={"max_single_stock_pct": 5})
    assert out["notional_pct"] == pytest.approx(5.0)
    assert out["notional_value"] == pytest.approx(500.0)
    assert out["binding_constraint"] == "max_single_stock_pct"


def test_sizing_case3_provisional_range():
    out = m.size_position(portfolio_value=10000, adverse_move_pct=40,
                          loss_budget_pct=None, caps={})
    assert out["provisional"] is True
    assert out["range_pct"][1] <= 5.0


def test_sizing_case4_drawdown_tolerance_ignored():
    out = m.size_position(portfolio_value=10000, adverse_move_pct=40,
                          loss_budget_pct=None,
                          caps={"max_single_stock_pct": 5},
                          max_drawdown_tolerance_pct=30)
    assert out["notional_pct"] == pytest.approx(2.5)  # never 75%


def test_resulting_exposure_hand_computed():
    holdings = [
        {"symbol": "A", "value_base": 5000.0, "sector": "T", "currency": "USD"},
        {"symbol": "B", "value_base": 3000.0, "sector": "T", "currency": "EUR"},
        {"symbol": "C", "value_base": 2000.0, "sector": "E", "currency": "EUR"},
    ]
    out = m.resulting_exposure(
        holdings, {"symbol": "D", "amount_base": 1000.0, "sector": "E", "currency": "USD"},
        caps={"max_single_stock_pct": 5})
    assert out["post_weight_pct"] == 9.0909
    assert out["post_sector_weight_pct"] == 27.2727
    assert out["post_currency_weight_pct"] == 54.5455
    assert out["breach"] is True
    assert "downgraded" in out["suggested_verdict"]


def test_coverage_labels():
    out = m.classify_coverage([{"id": "A", "coverage": "complete"},
                               {"id": "B", "coverage": "partial"}])
    assert out["complete"] == ["A"] and out["partial"] == ["B"]
    assert "covered" in out["denominator"]
