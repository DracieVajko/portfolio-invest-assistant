"""Phase 1 canonical financial core: single recon producer, ISIN-first rows.

Offline only. No live API.
"""

from __future__ import annotations

import pytest

from investment_engine.reporting.regime_report import (
    build_unified_portfolio_rows,
    compute_reconciliation,
)


def _pos(symbol, value, qty=1.0, isin=None):
    p = {
        "symbol": symbol,
        "quantity": qty,
        "average_price_eur": 10.0,
        "current_price_eur": 10.0,
        "value_eur": value,
        "cost_basis_eur": value,
        "pnl_eur": 0.0,
        "pnl_pct": 0.0,
        "is_pie_constituent": False,
        "validation_status": "PASS",
        "validation_reason": "",
    }
    if isin:
        p["isin"] = isin
    return p


def _t212(all_positions, total=3243.51, free=0.61, pie_cash=1.03, blocked=23.01, extra_summary=None):
    summary = {
        "total_equity": total,
        "all_positions": all_positions,
        "positions": [p for p in all_positions if not p.get("is_pie_constituent")],
        "cash_free": free,
        "cash_pie": pie_cash,
        "cash_blocked": blocked,
        "reconciliation_threshold": 3.24,
    }
    if extra_summary:
        summary.update(extra_summary)
    return {
        "status": "ok",
        "account_summary": summary,
        "cash": {"free": free, "pie_cash": pie_cash, "invested": 0, "blocked": blocked},
        "positions": summary["positions"],
        "all_positions": all_positions,
    }


def test_canonical_fail_wins_over_row_math():
    """Thin view: canonical FAIL verdict stands even when row math would PASS."""
    data = _t212([_pos("AAPL_US_EQ", 3200.0)])
    rows = build_unified_portfolio_rows(data)
    out = compute_reconciliation(data, rows, canonical={"status": "FAIL", "threshold": 2.0})
    assert out["status"] == "FAIL"
    assert out["verdict_source"] == "canonical"
    assert out["threshold"] == pytest.approx(2.0)


def test_canonical_pass_wins_over_row_math():
    """Thin view: canonical PASS verdict stands even when row math would FAIL."""
    data = _t212([_pos("AAPL_US_EQ", 100.0)])  # huge gap vs equity -> rows say FAIL
    rows = build_unified_portfolio_rows(data)
    plain = compute_reconciliation(data, rows)
    assert plain["status"] == "FAIL"
    out = compute_reconciliation(data, rows, canonical={"status": "PASS", "threshold": 5000.0})
    assert out["status"] == "PASS"
    assert out["verdict_source"] == "canonical"


def test_no_canonical_never_inherits_legacy_status():
    """Without canonical, a stale summary PASS must not override row math (R1 leak closed)."""
    data = _t212([_pos("AAPL_US_EQ", 100.0)],
                 extra_summary={"reconciliation_status": "PASS"})
    rows = build_unified_portfolio_rows(data)
    out = compute_reconciliation(data, rows)
    assert out["status"] == "FAIL"
    assert out["verdict_source"] == "recomputed"


def test_empty_snapshot_never_passes():
    out = compute_reconciliation({"account_summary": {"total_equity": 0}}, [])
    assert out["status"] == "UNKNOWN"


def test_isin_dedupe_rows_alias_fixture():
    """Same ISIN under two broker tickers counts once (canonical identity)."""
    positions = [_pos("VWSBd_EQ", 1000.0), _pos("VWSB", 1000.0)]
    data = _t212(positions)
    rows = build_unified_portfolio_rows(
        data, isin_map={"VWSBD_EQ": "DK0061539921", "VWSB": "DK0061539921"})
    assert len(rows) == 1
    assert rows[0]["identity_provenance"] == "ISIN"
    assert "duplicate broker entry ignored" in rows[0]["notes"]


def test_position_isin_field_used_without_map():
    positions = [_pos("AAA_EQ", 500.0, isin="US0000000001"),
                 _pos("AAA_alt", 500.0, isin="US0000000001")]
    rows = build_unified_portfolio_rows(_t212(positions))
    assert len(rows) == 1
    assert rows[0]["identity_provenance"] == "ISIN"


def test_distinct_lots_same_isin_both_count():
    """Live Apple case (2026-09-26): AAPL_US_EQ + APCd_EQ share ISIN
    US0378331005 but are distinct lots (qty/avg differ) — both must count."""
    from investment_engine.portfolio.broker_first import (
        BrokerInstrument,
        BrokerPosition,
        deduplicate_positions,
    )

    def _lot(ticker, qty, avg, value):
        return BrokerPosition(
            instrument=BrokerInstrument(broker_instrument_id=ticker, broker_ticker=ticker,
                                        display_symbol=ticker.split("_")[0], isin="US0378331005"),
            quantity=qty, average_price_raw=avg, broker_market_value_eur=value)

    a = _lot("AAPL_US_EQ", 0.52829496, 323.73960183, 3220.67)
    b = _lot("APCd_EQ", 0.06945003, 298.20001518, 20.63)
    unique, duplicates = deduplicate_positions([a, b])
    assert len(unique) == 2 and not duplicates
    assert sum(p.broker_market_value_eur for p in unique) == pytest.approx(3241.30)

    rows = build_unified_portfolio_rows(
        _t212([_pos("AAPL_US_EQ", 3220.67, qty=0.52829496),
               _pos("APCd_EQ", 20.63, qty=0.06945003)]),
        isin_map={"AAPL_US_EQ": "US0378331005", "APCD_EQ": "US0378331005"})
    assert len(rows) == 2
    assert {r["identity_provenance"] for r in rows} == {"ISIN"}


def test_identical_rows_still_merge():
    """Byte-identical mirror rows (alias case) keep merging into one."""
    from investment_engine.portfolio.broker_first import deduplicate_positions

    positions = [_pos("VWSBd_EQ", 30.0), _pos("VWSB", 30.0)]
    rows = build_unified_portfolio_rows(
        _t212(positions),
        isin_map={"VWSBD_EQ": "DK0061539921", "VWSB": "DK0061539921"})
    assert len(rows) == 1
    assert "duplicate broker entry ignored" in rows[0]["notes"]


def test_symbol_fallback_dedupe_and_provenance():
    positions = [_pos("AAPL_US_EQ", 700.0), _pos("AAPL_US_EQ", 700.0)]
    rows = build_unified_portfolio_rows(_t212(positions))
    assert len(rows) == 1
    assert rows[0]["identity_provenance"] == "BROKER_ID"
    distinct = build_unified_portfolio_rows(
        _t212([_pos("AAPL_US_EQ", 700.0), _pos("MSFT_US_EQ", 300.0)]))
    assert len(distinct) == 2
    assert {r["identity_provenance"] for r in distinct} == {"BROKER_ID"}


def test_mismatch_never_adjusts_broker_values():
    """Canonical FAIL must not rewrite broker market values in rows."""
    data = _t212([_pos("AAPL_US_EQ", 3200.0)])
    rows = build_unified_portfolio_rows(data)
    before = [r["market_value"] for r in rows]
    out = compute_reconciliation(data, rows, canonical={"status": "FAIL", "threshold": 2.0})
    assert out["status"] == "FAIL"
    assert [r["market_value"] for r in rows] == before


def test_row_pnl_is_broker_truth_never_recomputed():
    """Snapshot P&L must equal broker ppl even when technicals imply otherwise."""
    pos = _pos("AAPL_US_EQ", 100.0)
    pos["pnl_eur"] = 10.15
    rows = build_unified_portfolio_rows(
        _t212([pos]),
        technicals={"AAPL": {"RSI_14": 90, "Support": 1.0, "Resistance": 999.0}},
    )
    assert rows[0]["unrealized_pnl"] == pytest.approx(10.15)


def test_r1_helper_math_and_aliases_present():
    from trading212.portfolio import R1_DEPRECATED_ALIASES, compute_legacy_r1

    diff, thr, status = compute_legacy_r1(3246.22, 3243.72)
    assert diff == pytest.approx(2.50)
    assert thr == pytest.approx(3.24622)
    assert status == "PASS"  # legacy tolerance; canonical min(0.1%, €2) would FAIL this
    assert set(R1_DEPRECATED_ALIASES) == {
        "reconciliation_status_legacy",
        "reconciliation_threshold_legacy",
        "reconciliation_difference_legacy",
    }


def test_engine_guard_prefers_canonical_v2():
    """run_engine's recon guard must use the canonical snapshot, never R1 fields."""
    import pathlib

    src = pathlib.Path("investment_engine/main.py").read_text(encoding="utf-8")
    guard_at = src.find("Canonical broker-first snapshot FIRST")
    assert guard_at != -1
    guard_block = src[guard_at:guard_at + 4500]
    assert "reconcile_snapshot as _reconcile_snap" in guard_block
    assert 'summary.get("reconciliation_status"' not in guard_block
    assert 'summary.get("reconciliation_difference"' not in guard_block
    assert "T212 Reconciliation:" not in src


def test_ledger_cannot_flip_live_status():
    """Ledger audit result is a different type; reporting never imports it."""
    import pathlib

    from investment_engine.accounting.reconciliation import ReconciliationResult as LedgerRecon
    from investment_engine.portfolio.broker_first import ReconciliationResult as BrokerRecon

    assert LedgerRecon is not BrokerRecon
    assert "deposit_reconciled" in LedgerRecon.__dataclass_fields__
    assert "reconciliation_status" in BrokerRecon.__dataclass_fields__
    # The ledger stack (reconciliation/ledger/reporting/__init__ re-exports)
    # must stay out of live reporting; accounting.cashflows (broker-equity
    # performance + FAIL note) is the only allowed accounting import.
    src = pathlib.Path("investment_engine/reporting/regime_report.py").read_text(encoding="utf-8")
    assert "accounting.reconciliation" not in src
    assert "accounting.ledger" not in src
    assert "accounting.reporting" not in src
    assert "from investment_engine.accounting import" not in src
