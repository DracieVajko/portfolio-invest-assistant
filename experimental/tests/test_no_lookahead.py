"""No look-ahead: future bar must not affect earlier signal."""
import pandas as pd
import numpy as np

from experimental.backtest.strategy.tech_pie_pullback_v1 import TechPiePullbackV1
from experimental.backtest.io import load_ohlcv_from_dataframe
from pathlib import Path
import json

FX = Path("experimental/backtest/config/fx_rates.json")


def _load_config():
    return json.loads(Path("experimental/backtest/config/tech_pie_pullback_v1.json").read_text())


def _make_daily_df(n=300, symbol="NOLEAK", start="2022-01-03"):
    # Create deterministic daily data that will produce varied signals
    dates = pd.bdate_range(start, periods=n, tz="UTC")
    np.random.seed(0)
    # Construct close that trends up with pullbacks
    close = 100 + np.cumsum(np.random.randn(n) * 0.6 + 0.08)
    # Ensure high/low
    rows = []
    for i, d in enumerate(dates):
        c = close[i]
        o = c + np.random.randn() * 0.2
        h = max(o, c) + abs(np.random.randn()) * 0.5 + 0.3
        l = min(o, c) - abs(np.random.randn()) * 0.5 - 0.3
        v = 2_000_000 + int(np.random.randn() * 200_000)
        if v < 500_000:
            v = 500_000
        # Ensure volume stable for ratio test
        rows.append({"date": d.isoformat(), "symbol": symbol, "open": o, "high": h, "low": l, "close": c, "volume": v, "currency": "EUR", "earnings_date": ""})
    return pd.DataFrame(rows)


def test_future_extreme_bar_does_not_change_earlier_signal():
    df_raw = _make_daily_df(300)
    # Need longer for weekly SMA200 (200 weeks ~ 1000 days) but our synthetic not that long,
    # so weekly gate will be false anyway -> signals all 0, but we test no-leak still holds when we force conditions
    # Instead test that appending extreme bar doesn't change earlier signals for same length prefix
    df, meta = load_ohlcv_from_dataframe(df_raw, FX)
    cfg = _load_config()
    # Relax weekly gate to always true for this test to get some signals? We'll monkey-patch by using a config that disables weekly gate?
    # Instead we directly test signal stability: generate signals for df, then for df+extreme and compare prefix
    strat = TechPiePullbackV1(cfg)
    sig1 = strat.generate_signals(df)
    # Append extreme bar: huge spike
    extreme_row = df_raw.iloc[-1].copy()
    extreme_date = pd.to_datetime(df_raw["date"].iloc[-1], utc=True) + pd.Timedelta(days=1)
    # Ensure it's a business day
    while extreme_date.weekday() >= 5:
        extreme_date += pd.Timedelta(days=1)
    extreme_row["date"] = extreme_date.isoformat()
    extreme_row["close"] = 500.0
    extreme_row["open"] = 500.0
    extreme_row["high"] = 510.0
    extreme_row["low"] = 490.0
    extreme_row["volume"] = 10_000_000
    df_raw2 = pd.concat([df_raw, extreme_row.to_frame().T], ignore_index=True)
    df2, _ = load_ohlcv_from_dataframe(df_raw2, FX)
    sig2 = strat.generate_signals(df2)
    # Compare first N signals identical
    merged = sig1.merge(sig2, on=["date", "symbol"], suffixes=("_1", "_2"))
    # All prefix signals equal
    assert (merged["signal_1"] == merged["signal_2"]).all(), "Future bar leaked into earlier signal"
    assert (merged["weekly_gate_ok_1"] == merged["weekly_gate_ok_2"]).all()
    assert (merged["daily_ok_1"] == merged["daily_ok_2"]).all()


def test_fill_on_next_open_not_same_close():
    # Create minimal engine test: signal at close[t] must fill at open[t+1]
    from experimental.backtest.engine import BacktestEngine
    from experimental.backtest.strategy.base import StrategyBase
    # Create 60 days of flat data with one engineered pullback
    dates = pd.bdate_range("2023-01-02", periods=60, tz="UTC")
    # Need to engineer conditions: make weekly gate true requires 50/200 SMA etc. That's hard with short data.
    # Instead we will directly test engine's pending logic: create a custom strategy that emits signal on known date
    # Monkey patch strategy

    class FixedSignalStrategy(StrategyBase):
        def generate_signals(self, df):
            # Signal on second last date
            sig_dates = df["date"].unique()
            target = sig_dates[-3]
            rows = []
            for _, row in df.iterrows():
                sig = 1 if row["date"] == target and row["symbol"] == "FILLTEST" else 0
                rows.append({"date": row["date"], "symbol": row["symbol"], "signal": sig, "weekly_gate_ok": True, "daily_ok": True, "is_blackout": False, "reason": "test"})
            return pd.DataFrame(rows)

    # Build df
    rows = []
    for d in dates:
        # Use distinct open/close to detect fill price
        # Set close 100, next open 105 to see fill difference
        idx = (d - dates[0]).days
        close = 100 + idx * 0.1
        open_ = 100 + idx * 0.1 + 0.5  # open slightly higher
        # But for target+1 open, set 110
        rows.append({"date": d.isoformat(), "symbol": "FILLTEST", "open": open_, "high": open_+1, "low": open_-1, "close": close, "volume": 1_000_000, "currency": "EUR", "earnings_date": ""})
    df_raw = pd.DataFrame(rows)
    # Make target+1 open distinct
    target_idx = len(dates) - 3
    # Set next open to 999 to prove fill uses next open
    df_raw.loc[target_idx + 1, "open"] = 999.0
    df_raw.loc[target_idx + 1, "high"] = 1000.0
    df_raw.loc[target_idx + 1, "low"] = 998.0
    # Need to ensure validation passes (high>low, etc, gaps ok)
    from experimental.backtest.io import load_ohlcv_from_dataframe
    df, meta = load_ohlcv_from_dataframe(df_raw, FX)
    cfg = _load_config()
    cfg["risk"]["max_alloc_per_instrument_pct"] = 50  # allow larger to see fill
    cfg["costs"]["commission_bps"] = 0
    cfg["costs"]["slippage_bps"] = 0
    cfg["costs"]["fx_spread_bps"] = 0
    engine = BacktestEngine("experimental/backtest/config/tech_pie_pullback_v1.json", FX)
    # Monkey patch strategy and costs for this test
    engine.strategy = FixedSignalStrategy(cfg)
    engine.costs.commission_bps = 0
    engine.costs.slippage_bps = 0
    engine.costs.fx_spread_bps = 0
    engine.costs.min_ticket_eur = 0
    engine.max_alloc_pct = 50
    engine.start_equity = 10000
    result = engine.run(df, meta)
    # There should be one trade
    assert len(result["trades"]) == 1 or len(result["fills"]) >= 1
    fill = [f for f in result["fills"] if f["side"] == "BUY"][0]
    # Fill price should be next open (999) not signal close (~105)
    assert abs(fill["price"] - 999.0) < 1e-6, f"Fill on next open failed, got {fill['price']}"
    # Ensure fill date is target+1
    assert fill["date"] == dates[target_idx + 1]
