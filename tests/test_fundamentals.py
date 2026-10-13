"""Fundamentals pack: hand-computed expectations on synthetic statements."""
from __future__ import annotations

import pandas as pd

from investment_engine.research.fundamentals import (
    _cagr,
    annual_series,
    build_pack,
    pick_row,
)


def _frame(rows: dict[str, list[float]], years=("2025", "2024", "2023")) -> pd.DataFrame:
    """yfinance column order: newest first."""
    return pd.DataFrame(rows, index=list(years)).T


def _stmts():
    fin = _frame({
        "Total Revenue": [121.0, 110.0, 100.0],
        "Gross Profit": [72.6, 66.0, 60.0],
        "Operating Income": [24.2, 22.0, 20.0],
        "Net Income": [18.15, 16.5, 15.0],
        "Diluted EPS": [1.815, 1.65, 1.5],
        "Pretax Income": [24.2, 22.0, 20.0],
        "Tax Provision": [6.05, 5.5, 5.0],
        "Interest Expense": [2.0, 2.0, 2.0],
    })
    bs = _frame({
        "Long Term Debt": [30.0, 30.0, 30.0],
        "Cash And Cash Equivalents": [14.0, 12.0, 10.0],
        "Total Equity Gross Minority Interest": [120.0, 110.0, 100.0],
        "Ordinary Shares Number": [10.0, 10.0, 10.0],
    })
    cf = _frame({
        "Operating Cash Flow": [30.0, 27.0, 25.0],
        "Capital Expenditure": [-6.0, -5.0, -5.0],
        "Stock Based Compensation": [1.0, 1.0, 1.0],
        "Cash Dividends Paid": [-3.0, -3.0, -3.0],
    })
    q = _frame({"Total Revenue": [33.0, 30.0, 29.0, 29.0],
                "Net Income": [5.0, 4.5, 4.0, 4.65]},
               years=("2025-Q4", "2025-Q3", "2025-Q2", "2025-Q1"))
    return fin, bs, cf, q


def test_alias_pick_and_cagr():
    fin, _, _, _ = _stmts()
    assert pick_row(fin, "revenue").iloc[0] == 121.0  # newest-first (yfinance order)
    assert pick_row(fin, "nope") is None
    import pytest
    assert _cagr([100.0, 110.0, 121.0, 133.1]) == pytest.approx(0.10)
    assert _cagr([100.0]) is None


def test_pack_math():
    fin, bs, cf, q = _stmts()
    pack = build_pack(fin, bs, cf, q, symbol="TST", currency="USD")
    assert pack["statements_available"] == {"annual": 3, "quarterly": 4}
    assert pack["ttm"]["revenue"] == 121.0  # 33+30+29+29
    assert pack["ttm"]["fcf_margin"] == pack["ttm"]["fcf"] / 121.0
    assert pack["margins_latest"]["gross"] == 72.6 / 121.0
    assert pack["margins_latest"]["operating"] == 24.2 / 121.0
    assert pack["balance"]["net_debt"] == 30.0 - 14.0
    assert pack["balance"]["effective_tax_rate"] == 6.05 / 24.2
    # ROIC = 24.2*(1-0.25)/(120+16)
    assert pack["returns"]["roic"] == (24.2 * 0.75) / 136.0
    assert pack["dividends"]["dps_latest"] == 3.0 / 10.0
    assert pack["growth"]["revenue_cagr_3y"] is None  # only 3 annuals
    assert len(pack["history"]) == 3
    assert pack["history"][-1]["eps_diluted"] == 1.815


def test_pack_gaps_are_null_with_notes():
    pack = build_pack(symbol="EMPTY")
    assert pack["ttm"]["revenue"] is None
    assert pack["returns"]["roic"] is None
    assert any("no annual" in n for n in pack["notes"])
    pack2 = build_pack(*_stmts()[:3], symbol="NOQ")
    assert pack2["statements_available"]["quarterly"] == 0
    assert pack2["ttm"]["revenue"] == 121.0  # annual fallback
