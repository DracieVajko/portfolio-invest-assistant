"""T02 units + T03 staleness gate. Offline."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from investment_engine.portfolio import metrics as metrics
from investment_engine.research.market_data import quote_freshness, staleness_gate


def test_gbx_to_gbp_to_eur():
    out = metrics.position_values(
        [{"symbol": "X", "quantity": 10, "price": 8250, "currency": "GBP",
          "price_unit": "GBX"}],
        base_ccy="EUR", fx={"GBP": 1.15})
    pos = out["positions"][0]
    assert pos["price_native"] == 82.50
    assert pos["value_native"] == 825.00
    assert pos["value_base"] == pytest.approx(948.75)


def test_missing_unit_raises_data_quality_error():
    with pytest.raises(ValueError):
        metrics.position_values(
            [{"symbol": "X", "quantity": 1, "price": 5.0, "currency": "EUR"}],
            base_ccy="EUR", fx={"EUR": 1.0})


def test_staleness_states():
    as_of = datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc)
    session = datetime(2026, 3, 9, 21, 0, tzinfo=timezone.utc)
    assert quote_freshness(session, session, as_of)["state"] == "fresh"
    assert quote_freshness(datetime(2026, 3, 9, 21, 0, tzinfo=timezone.utc),
                           session, as_of)["state"] == "fresh"
    old = quote_freshness(datetime(2026, 3, 6, 21, 0, tzinfo=timezone.utc),
                          session, as_of)
    assert old["state"] == "stale" and old["reason"] != ""
    assert quote_freshness(None, session, as_of)["state"] == "missing"


def test_staleness_gate_caps_confidence():
    gate = staleness_gate({"A": "fresh", "B": "stale", "C": "missing"})
    assert gate["confidence_cap"] == "provisional"
    assert gate["allow_high_confidence"] is False
    assert sorted(gate["insufficient"]) == ["B", "C"]
    gate_ok = staleness_gate({"A": "fresh"})
    assert gate_ok["confidence_cap"] == "ok"
    assert gate_ok["allow_high_confidence"] is True
