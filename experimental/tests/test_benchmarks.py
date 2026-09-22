"""Benchmark correctness tests."""
import pandas as pd
import numpy as np
from pathlib import Path
import math

from experimental.backtest.benchmarks import equal_weight_buy_and_hold, periodic_equal_weight_rebalance
from experimental.backtest.costs import CostsConfig
from experimental.backtest.io import load_ohlcv_from_dataframe

FX = Path("experimental/backtest/config/fx_rates.json")


def _make_benchmark_df(symbols=("A","B","C"), n=30, start="2023-01-02", base_price=100, drift=0.1, seed=0):
    np.random.seed(seed)
    dates = pd.bdate_range(start, periods=n, tz="UTC")
    rows = []
    for sym in symbols:
        # Each symbol price series with slight drift and noise
        prices = base_price + np.cumsum(np.random.randn(n) * 0.5 + drift)
        for i, d in enumerate(dates):
            c = float(prices[i])
            o = c + np.random.randn()*0.1
            h = max(o,c) + abs(np.random.randn())*0.3 + 0.2
            l = min(o,c) - abs(np.random.randn())*0.3 - 0.2
            v = 1_000_000 + int(np.random.randn()*100000)
            if v<100000:
                v=100000
            rows.append({"date": d.isoformat(), "symbol": sym, "open": o, "high": h, "low": l, "close": c, "volume": v, "currency": "EUR", "earnings_date": ""})
    return pd.DataFrame(rows)


def test_equal_weight_weights_sum_to_one():
    df_raw = _make_benchmark_df(symbols=("X","Y","Z"), n=10)
    df, _ = load_ohlcv_from_dataframe(df_raw, FX)
    costs = CostsConfig(0,0,0,0)
    res = equal_weight_buy_and_hold(df, start_equity=10000, costs=costs, min_history=5)
    assert abs(sum(res["weights"].values()) - 1.0) < 1e-9
    # Each weight equal
    for w in res["weights"].values():
        assert abs(w - 1/3) < 1e-9


def test_entry_costs_reduce_buy_and_hold_equity():
    df_raw = _make_benchmark_df(n=20, drift=0.0)
    df, _ = load_ohlcv_from_dataframe(df_raw, FX)
    costs_zero = CostsConfig(0,0,0,0)
    costs_with = CostsConfig(10,5,10,1)
    res_zero = equal_weight_buy_and_hold(df, 10000, costs_zero, min_history=5)
    res_with = equal_weight_buy_and_hold(df, 10000, costs_with, min_history=5)
    # With entry costs, ending equity lower (since same price path flat, costs drag)
    assert res_with["equity_curve"].iloc[-1] < res_zero["equity_curve"].iloc[-1]
    assert res_with["costs_paid"] > 0
    assert res_zero["costs_paid"] == 0


def test_periodic_creates_turnover_and_costs():
    # Create trending up data to cause rebalances (equal weight will drift)
    df_raw = _make_benchmark_df(symbols=("A","B"), n=90, drift=0.2)
    # Make A trend up faster than B to create drift and need rebalance
    # We'll manually adjust
    df, _ = load_ohlcv_from_dataframe(df_raw, FX)
    costs = CostsConfig(10,5,10,1)
    bnh = equal_weight_buy_and_hold(df, 10000, costs, min_history=5)
    per = periodic_equal_weight_rebalance(df, 10000, costs, frequency="monthly", min_history=5)
    # Periodic should have at least one rebalance and higher costs
    assert per["num_rebalances"] > 0
    assert per["costs_paid"] > bnh["costs_paid"]
    assert per["rebalance_turnover"] > 0
    assert bnh["turnover"] == 0
    # For B&H, turnover 0 (only entry), for periodic, turnover >0


def test_benchmark_no_future_access():
    df_raw = _make_benchmark_df(n=20)
    df, _ = load_ohlcv_from_dataframe(df_raw, FX)
    costs = CostsConfig(0,0,0,0)
    res1 = equal_weight_buy_and_hold(df, 10000, costs, min_history=5)
    curve1 = res1["equity_curve"]
    # Append future extreme bar: double prices for one symbol on next business day
    last_date = pd.to_datetime(df_raw["date"].max(), utc=True)
    next_date = last_date + pd.offsets.BDay(1)
    # Add row for each symbol on future date with extreme price
    extra_rows = []
    for sym in df["symbol"].unique():
        extra_rows.append({"date": next_date.isoformat(), "symbol": sym, "open": 1000, "high": 1001, "low": 999, "close": 1000, "volume": 1_000_000, "currency": "EUR", "earnings_date": ""})
    df_raw2 = pd.concat([df_raw, pd.DataFrame(extra_rows)], ignore_index=True)
    df2, _ = load_ohlcv_from_dataframe(df_raw2, FX)
    res2 = equal_weight_buy_and_hold(df2, 10000, costs, min_history=5)
    curve2 = res2["equity_curve"]
    # First len(curve1) values must be unchanged (no leak)
    # Align by index
    common = curve1.index.intersection(curve2.index)
    # Check first n-1 equal (last of curve1 is second last of curve2 before extreme)
    # Actually curve2 has one extra date at end, so first len(curve1) points should equal
    assert len(curve1) + 1 == len(curve2)
    # Compare prefix
    assert np.allclose(curve1.values, curve2.values[:len(curve1)], atol=1e-9)


def test_missing_symbol_handling_deterministic():
    # Create df where one symbol has missing data on some dates
    df_raw = _make_benchmark_df(symbols=("A","B","C"), n=20)
    # Remove B for some dates
    df_raw = df_raw[~((df_raw["symbol"]=="B") & (df_raw["date"].str.contains("2023-01-1")))]
    df, _ = load_ohlcv_from_dataframe(df_raw, FX)
    costs = CostsConfig(0,0,0,0)
    res = equal_weight_buy_and_hold(df, 10000, costs, min_history=5)
    # Should still produce equity curve for all dates, using forward fill
    assert len(res["equity_curve"]) == len(df["date"].unique())
    # Coverage should still show bars for B less than A
    assert res["coverage"]["B"] < res["coverage"]["A"]


def test_weights_when_fully_invested():
    df_raw = _make_benchmark_df(symbols=("P","Q","R","S"), n=10)
    df, _ = load_ohlcv_from_dataframe(df_raw, FX)
    costs = CostsConfig(0,0,0,0)
    res = equal_weight_buy_and_hold(df, 10000, costs, min_history=5)
    # When fully invested with zero costs, weights sum to 1
    # Also each weight 0.25
    assert len(res["weights"]) == 4
    assert abs(sum(res["weights"].values()) - 1.0) < 1e-9
