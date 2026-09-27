"""ISIN-grouped holdings rendering: one group row + lot sub-rows.

Offline only. Grouping lives in render_snapshot; canonical rows untouched.
"""

from __future__ import annotations

import pytest


def _row(display, isin, qty, value, avg=10.0, pnl=0.0, signal="HOLD", internal=None):
    return {"display_symbol": display, "ticker": display, "company": display,
            "quantity": qty, "avg_cost": avg, "current_price": avg,
            "market_value": value, "unrealized_pnl": pnl, "realized_pnl": None,
            "total_pnl": pnl, "pnl_pct": 0.0, "weight": value / 100.0,
            "signal": signal, "internal_id": internal or f"{display}_EQ", "isin": isin,
            "market_data_state": "SUPPORTED", "notes": ""}


def test_apple_pair_groups_with_lots():
    from investment_engine.reporting.documents import _group_rows_by_isin

    rows = [_row("AAPL", "US0378331005", 0.5, 170.0, avg=320.0, pnl=10.0),
            _row("APC", "US0378331005", 0.07, 20.0, avg=298.0, pnl=-0.1),
            _row("MSFT", "US5949181045", 1.0, 500.0)]
    groups = _group_rows_by_isin(rows)
    assert len(groups) == 2  # Apple group + MSFT
    key, primary, lots = groups[1] if groups[0][0] == "US5949181045" else groups[0]
    assert key == "US0378331005" and len(lots) == 2
    assert primary["market_value"] == 190.0
    assert primary["quantity"] == pytest.approx(0.57)
    assert primary["avg_cost"] == pytest.approx((0.5 * 320.0 + 0.07 * 298.0) / 0.57)
    assert "APC" in primary["notes"] and "same ISIN" in primary["notes"]


def test_group_sums_match_lots_and_isinless_never_merge():
    from investment_engine.reporting.documents import _group_rows_by_isin

    rows = [_row("AAA", "", 1.0, 100.0, internal="AAA_1"),
            _row("AAA", "", 2.0, 200.0, internal="AAA_2"),
            _row("BBB", "", 1.0, 50.0, internal="BBB_1")]
    groups = _group_rows_by_isin(rows)
    assert len(groups) == 3  # no ISIN -> broker-ID keys never merge
    total = sum(g[1]["market_value"] for g in groups)
    assert total == 350.0
    # Same broker ID without ISIN = same holding reported twice -> merges.
    dupes = _group_rows_by_isin([_row("AAA", "", 1.0, 100.0),
                                 _row("AAA", "", 1.0, 100.0)])
    assert len(dupes) == 1 and len(dupes[0][2]) == 2


def test_lot_company_backfilled_from_group():
    from investment_engine.reporting.documents import _group_rows_by_isin

    a = _row("AAPL", "US0378331005", 0.5, 170.0)
    b = _row("APC", "US0378331005", 0.07, 20.0)
    b["company"] = "Unknown instrument"
    groups = _group_rows_by_isin([a, b])
    lots = groups[0][2]
    assert all(r["company"] not in ("", "Unknown instrument") for r in lots)


def test_snapshot_renders_lot_subrows():
    from investment_engine.reporting.documents import render_snapshot

    rows = [_row("AAPL", "US0378331005", 0.5, 170.0, avg=320.0, pnl=10.0),
            _row("APC", "US0378331005", 0.07, 20.0, avg=298.0, pnl=-0.1)]
    md = render_snapshot(
        generated_at="t", recon={"status": "PASS", "total_equity": 1000.0,
                                 "positions_value": 190.0, "reported_cash": 810.0,
                                 "implied_cash": 810.0, "cash_delta": 0.0, "threshold": 2.0},
        cash={}, rows=rows, monitoring_by_display={}, earnings_status={})
    assert "| ↳ APC" in md or "| ↳ AAPL" in md
    assert "same ISIN" in md
    # header + column contract unchanged
    assert md.index("## Holdings") < md.index("## Data Coverage")
    assert "Canonical Signal" in md


def test_snapshot_group_totals_match_recon():
    import re

    from investment_engine.reporting.documents import render_snapshot

    rows = [_row("AAPL", "US0378331005", 0.5, 170.0),
            _row("APC", "US0378331005", 0.07, 20.0)]
    md = render_snapshot(
        generated_at="t", recon={"status": "PASS", "total_equity": 1000.0,
                                 "positions_value": 190.0, "reported_cash": 810.0,
                                 "implied_cash": 810.0, "cash_delta": 0.0, "threshold": 2.0},
        cash={}, rows=rows, monitoring_by_display={}, earnings_status={})
    floats = [float(x.replace(",", "")) for x in re.findall(r"\| €([\d,]+\.\d\d) \|", md)]
    assert any(abs(v - 190.0) < 0.01 for v in floats)  # group total present
