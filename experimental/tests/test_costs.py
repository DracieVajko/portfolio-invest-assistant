"""Costs reduce post-cost outcome, turnover, etc."""
import pandas as pd
import numpy as np
from pathlib import Path
import json

from experimental.backtest.costs import CostsConfig, apply_costs
from experimental.backtest.io import load_ohlcv_from_dataframe
from experimental.backtest.engine import BacktestEngine
from experimental.backtest.strategy.base import StrategyBase

FX = Path("experimental/backtest/config/fx_rates.json")


class AlwaysEnterStrategy(StrategyBase):
    def generate_signals(self, df):
        # Signal on first date where close exists, for each symbol
        sigs = []
        for _, row in df.iterrows():
            # Only first date per symbol
            sigs.append({"date": row["date"], "symbol": row["symbol"], "signal": 0, "weekly_gate_ok": True, "daily_ok": True, "is_blackout": False, "reason": ""})
        # Set signal 1 on second date for each symbol
        sig_df = pd.DataFrame(sigs)
        # Mark second unique date per symbol as 1
        for sym in sig_df["symbol"].unique():
            sub = sig_df[sig_df["symbol"] == sym].sort_values("date")
            if len(sub) >= 2:
                second_date = sub.iloc[1]["date"]
                sig_df.loc[(sig_df["symbol"] == sym) & (sig_df["date"] == second_date), "signal"] = 1
        return sig_df


def _make_flat_df(n=20):
    dates = pd.bdate_range("2024-01-01", periods=n, tz="UTC")
    rows = []
    for d in dates:
        rows.append({"date": d.isoformat(), "symbol": "COST", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1_000_000, "currency": "EUR", "earnings_date": ""})
    return pd.DataFrame(rows)


def test_apply_costs():
    cfg = CostsConfig(commission_bps=10, slippage_bps=5, fx_spread_bps=10, min_ticket_eur=1)
    res = apply_costs(1000, cfg)
    assert abs(res["commission"] - 1.0) < 1e-9  # 0.10% of 1000
    assert abs(res["slippage"] - 0.5) < 1e-9  # 0.05%
    assert abs(res["total_cost"] - 1.5) < 1e-9


def test_commission_slippage_reduce_post_cost():
    cfg = json.loads(Path("experimental/backtest/config/tech_pie_pullback_v1.json").read_text())
    # Two runs: zero costs vs default costs
    df_raw = _make_flat_df(20)
    df, meta = load_ohlcv_from_dataframe(df_raw, FX)
    # Zero cost engine
    cfg_zero = cfg.copy()
    cfg_zero["costs"] = {"commission_bps": 0, "slippage_bps": 0, "fx_spread_bps": 0, "min_ticket_eur": 0}
    # Write temp config
    import tempfile, json as js, pathlib
    with tempfile.TemporaryDirectory() as td:
        p_zero = pathlib.Path(td) / "zero.json"
        p_default = pathlib.Path(td) / "default.json"
        p_zero.write_text(js.dumps(cfg_zero), encoding="utf-8")
        cfg_default = cfg.copy()
        cfg_default["costs"] = {"commission_bps": 10, "slippage_bps": 5, "fx_spread_bps": 10, "min_ticket_eur": 1}
        p_default.write_text(js.dumps(cfg_default), encoding="utf-8")
        eng_zero = BacktestEngine(p_zero, FX)
        eng_zero.strategy = AlwaysEnterStrategy(cfg_zero)
        eng_zero.costs = CostsConfig(0, 0, 0, 0)
        eng_zero.max_alloc_pct = 50
        eng_zero.time_stop_bars = 5
        res_zero = eng_zero.run(df, meta)
        eng_def = BacktestEngine(p_default, FX)
        eng_def.strategy = AlwaysEnterStrategy(cfg_default)
        eng_def.max_alloc_pct = 50
        eng_def.time_stop_bars = 5
        res_def = eng_def.run(df, meta)
        # Post-cost equity with costs should be < zero costs (since flat prices, costs make loss)
        # Both have entry; end equity differs by costs even if trade not yet closed, so compare fills/equity
        assert res_def["metrics"].end_equity < res_zero["metrics"].end_equity
        # Both should have produced a fill (entry) even if trade not closed, check fills
        assert len([f for f in res_zero["fills"] if f["side"] == "BUY"]) == 1
        assert len([f for f in res_def["fills"] if f["side"] == "BUY"]) == 1
        # After time_stop (5 bars), both should have closed trades
        assert res_zero["metrics"].trade_count == 1
        assert res_def["metrics"].trade_count == 1


def test_min_ticket_blocks_small():
    cfg = json.loads(Path("experimental/backtest/config/tech_pie_pullback_v1.json").read_text())
    cfg["costs"]["min_ticket_eur"] = 100000  # huge, no trade should go through
    import tempfile, json as js, pathlib
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "cfg.json"
        p.write_text(js.dumps(cfg), encoding="utf-8")
        df_raw = _make_flat_df(10)
        df, meta = load_ohlcv_from_dataframe(df_raw, FX)
        eng = BacktestEngine(p, FX)
        eng.strategy = AlwaysEnterStrategy(cfg)
        eng.costs.min_ticket_eur = 100000
        eng.max_alloc_pct = 10
        res = eng.run(df, meta)
        assert res["metrics"].trade_count == 0


def test_engine_produces_trades_and_equity():
    cfg = json.loads(Path("experimental/backtest/config/tech_pie_pullback_v1.json").read_text())
    import tempfile, json as js, pathlib
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "cfg.json"
        cfg["costs"] = {"commission_bps": 10, "slippage_bps": 5, "fx_spread_bps": 0, "min_ticket_eur": 1}
        p.write_text(js.dumps(cfg), encoding="utf-8")
        df_raw = _make_flat_df(25)
        # Make price go up after entry to have positive pnl
        # We'll modify close after entry to rise
        df_raw.loc[5:, "close"] = 110
        df_raw.loc[5:, "open"] = 110
        df_raw.loc[5:, "high"] = 111
        df_raw.loc[5:, "low"] = 109
        df, meta = load_ohlcv_from_dataframe(df_raw, FX)
        eng = BacktestEngine(p, FX)
        eng.strategy = AlwaysEnterStrategy(cfg)
        eng.costs = CostsConfig(10, 5, 0, 1)
        eng.max_alloc_pct = 20
        res = eng.run(df, meta)
        assert "equity_curve" in res
        assert len(res["equity_curve"]) == len(df["date"].unique())
        assert res["metrics"].trade_count >= 1
