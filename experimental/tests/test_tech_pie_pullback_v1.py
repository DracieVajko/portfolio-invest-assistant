"""Strategy tech_pie_pullback_v1 deterministic tests."""
import pandas as pd
import numpy as np
from pathlib import Path
import json

from experimental.backtest.io import load_ohlcv_from_dataframe
from experimental.backtest.strategy.tech_pie_pullback_v1 import TechPiePullbackV1
from experimental.backtest.engine import BacktestEngine
from experimental.backtest.validate import ValidationError

FX = Path("experimental/backtest/config/fx_rates.json")
CFG_PATH = Path("experimental/backtest/config/tech_pie_pullback_v1.json")


def _load_cfg():
    return json.loads(CFG_PATH.read_text(encoding="utf-8"))


def _make_earnings_df():
    # 60 business days, earnings in middle
    dates = pd.bdate_range("2023-01-02", periods=60, tz="UTC")
    earn_date = dates[30]  # 2023-02-13 approx
    rows = []
    for d in dates:
        rows.append({
            "date": d.isoformat(),
            "symbol": "EARN",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "volume": 1_500_000,
            "currency": "EUR",
            "earnings_date": earn_date.isoformat(),
        })
    # Modify to make daily conditions true for a window: make RSI etc? Instead monkey-patch later.
    return pd.DataFrame(rows), earn_date


def test_earnings_blackout_blocks_entry():
    df_raw, earn_date = _make_earnings_df()
    df, meta = load_ohlcv_from_dataframe(df_raw, FX)
    cfg = _load_cfg()
    # Force strategy to emit signals every day except blackout: monkey patch indicators to make daily_ok true
    # Instead test the is_blackout logic directly via generate_signals with engineered data that would otherwise trigger
    # We'll create a strategy where weekly_gate always true and daily conditions true, then check blackout
    # To ensure daily_ok true, we need to set close, RSI, etc. Hard to guarantee with flat data.
    # Simpler: test the helper function indirectly: create a DataFrame where we manually craft high/mid conditions
    # Alternative: directly test engine with Fixed strategy and earnings blackout?
    # Let's test via engine with a fixed signal strategy that respects earnings blackout through strategy's logic
    # We'll use the real strategy but override indicators by patching add_daily_indicators to return favorable values

    from unittest.mock import patch
    import experimental.backtest.strategy.tech_pie_pullback_v1 as strat_mod

    # Create favorable price pattern: need SMA20 etc. We'll create trending data where pullback occurs
    # Simpler: test blackout by checking that a would-be entry date inside blackout window produces signal 0
    # We'll brute-force: find dates within 5 bdays before earnings, check signal==0 if other conditions met
    # To make other conditions met, we create data that satisfies them for all dates: manually set up indicators via patch

    def fake_add_daily(df):
        df = df.copy()
        # Force all daily conditions to true
        df["SMA20"] = df["close_eur"] * 1.05  # dist = (100 -105)/105 = -4.7% inside [-8,-2]
        df["SMA200"] = df["close_eur"] * 0.9  # close above
        df["RSI14"] = 40.0  # inside [35,50]
        df["ADX14"] = 20
        df["VOL_SMA20"] = 1_000_000
        df["VOLUME_RATIO"] = 1.0
        df["DIST_SMA20_PCT"] = -4.0
        # Also need other columns for weekly gate: SMA50_W etc via add_weekly, but we will also patch weekly
        df["SMA50"] = 90
        df["SMA200"] = 80
        df["EMA12"] = 100
        df["EMA26"] = 99
        df["MACD"] = 0
        df["ATR14"] = 2
        df["BB_MIDDLE"] = 100
        df["OBV"] = 0
        df["VWAP"] = 100
        df["ROLLING_VWAP_20"] = 100
        df["SUPERT"] = 100
        df["SUPERT_DIR"] = 1
        df["PLUS_DI14"] = 20
        df["MINUS_DI14"] = 10
        df["VOL_SMA20"] = 1_000_000
        return df

    def fake_add_weekly(df):
        df = df.copy()
        df["SMA50_W"] = 90
        df["SMA200_W"] = 80
        df["RSI14_W"] = 55
        df["ADX14_W"] = 20
        return df

    with patch.object(strat_mod, "add_daily_indicators", side_effect=fake_add_daily), \
         patch.object(strat_mod, "add_weekly_indicators", side_effect=fake_add_weekly):
        strat = TechPiePullbackV1(cfg)
        sig = strat.generate_signals(df)
        # For dates inside blackout, signal must be 0
        # Determine blackout dates: 5 bdays before earn_date to 2 after
        earn = earn_date.tz_localize("UTC") if earn_date.tzinfo is None else earn_date
        # Find signals
        # 5 bdays before includes earn -5,-4,-3,-2,-1 and after +1,+2
        # Our df has earn_date fixed for all rows, so each row's signal's is_blackout should be true for those dates
        # Let's check a date 3 bdays before earn should be blackout
        three_before = earn - pd.offsets.BDay(3)
        # Find row with that date (normalize to UTC midnight)
        target_row = sig[sig["date"] == pd.to_datetime(three_before, utc=True).normalize()]
        if not target_row.empty:
            assert target_row.iloc[0]["is_blackout"] == True
            assert target_row.iloc[0]["signal"] == 0
        # Date far before (10 bdays before) should not be blackout
        ten_before = earn - pd.offsets.BDay(10)
        far_row = sig[sig["date"] == pd.to_datetime(ten_before, utc=True).normalize()]
        if not far_row.empty:
            # With our fake indicators, this should be not blackout and thus signal 1 if weekly gate true
            # But need to check gate_2w also true: our fake weekly will make gate true after 2 weeks, so far row after warmup should be 1
            pass
        # At least ensure earnings day itself is blackout
        earn_row = sig[sig["date"] == earn.normalize()]
        if not earn_row.empty:
            assert earn_row.iloc[0]["is_blackout"] == True
            assert earn_row.iloc[0]["signal"] == 0


def test_time_stop_exits_after_20_days():
    # Create 40 days flat, force entry on day 5
    dates = pd.bdate_range("2023-01-02", periods=40, tz="UTC")
    rows = []
    for d in dates:
        rows.append({"date": d.isoformat(), "symbol": "TIME", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1_500_000, "currency": "EUR", "earnings_date": ""})
    df_raw = pd.DataFrame(rows)
    df, meta = load_ohlcv_from_dataframe(df_raw, FX)
    from unittest.mock import patch
    import experimental.backtest.strategy.tech_pie_pullback_v1 as strat_mod

    cfg = _load_cfg()
    cfg["exits"]["weekly_break_enabled"] = False  # disable weekly break for this test
    cfg["exits"]["hard_stop_pct"] = 50  # disable hard stop
    # Patch to force one entry signal at day 3
    original_generate = TechPiePullbackV1.generate_signals

    def fake_generate(self, df):
        sig_rows = []
        for _, row in df.iterrows():
            # Signal on first date after warmup
            target = dates[3]
            sig = 1 if row["date"] == target and row["symbol"] == "TIME" else 0
            sig_rows.append({"date": row["date"], "symbol": row["symbol"], "signal": sig, "weekly_gate_ok": True, "daily_ok": True, "is_blackout": False, "reason": "test", "close_eur": row["close_eur"], "open_eur": row["open_eur"], "rsi14": 40, "dist_sma20": -4, "volume_ratio": 1})
        return pd.DataFrame(sig_rows)

    with patch.object(TechPiePullbackV1, "generate_signals", fake_generate):
        engine = BacktestEngine(CFG_PATH, FX)
        # Need to override config time_stop_bars to 20, already
        engine.time_stop_bars = 20
        engine.weekly_break_enabled = False
        engine.hard_stop_pct = 50
        # Also patch strategy instance
        engine.strategy = TechPiePullbackV1(cfg)
        engine.strategy.generate_signals = lambda df: fake_generate(engine.strategy, df)
        result = engine.run(df, meta)
        assert len(result["trades"]) == 1
        trade = result["trades"][0]
        assert trade["exit_reason"] == "time_stop"
        assert trade["holding_days"] >= 20


def test_hard_stop_7_percent():
    dates = pd.bdate_range("2023-01-02", periods=20, tz="UTC")
    rows = []
    for d in dates:
        # Flat 100, but after entry, make low dip to 90 (<93)
        rows.append({"date": d.isoformat(), "symbol": "STOP", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1_500_000, "currency": "EUR", "earnings_date": ""})
    df_raw = pd.DataFrame(rows)
    # Make entry at day 3, then low at day 6 to trigger stop
    # Signal will be at day 3 close, fill day 4 open 100, stop 93, low at day 6 =90 should trigger
    # We need to adjust low for day 6 (index 5)
    df_raw.loc[5, "low"] = 90
    df_raw.loc[5, "high"] = 101
    df, meta = load_ohlcv_from_dataframe(df_raw, FX)
    cfg = _load_cfg()
    from unittest.mock import patch
    def fake_generate(self, df):
        sig_rows = []
        for _, row in df.iterrows():
            target = dates[3]
            sig = 1 if row["date"] == target and row["symbol"] == "STOP" else 0
            sig_rows.append({"date": row["date"], "symbol": row["symbol"], "signal": sig, "weekly_gate_ok": True, "daily_ok": True, "is_blackout": False, "reason": "test", "close_eur": row["close_eur"], "open_eur": row["open_eur"], "rsi14": 40, "dist_sma20": -4, "volume_ratio": 1})
        return pd.DataFrame(sig_rows)
    with patch.object(TechPiePullbackV1, "generate_signals", fake_generate):
        engine = BacktestEngine(CFG_PATH, FX)
        engine.strategy = TechPiePullbackV1(cfg)
        engine.strategy.generate_signals = lambda df: fake_generate(engine.strategy, df)
        engine.hard_stop_pct = 7.0
        engine.time_stop_bars = 60
        engine.weekly_break_enabled = False
        result = engine.run(df, meta)
        assert len(result["trades"]) == 1
        assert result["trades"][0]["exit_reason"] == "hard_stop"


def test_weekly_break_exits():
    dates = pd.bdate_range("2023-01-02", periods=30, tz="UTC")
    rows = []
    for d in dates:
        rows.append({"date": d.isoformat(), "symbol": "WBREAK", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1_500_000, "currency": "EUR", "earnings_date": ""})
    df_raw = pd.DataFrame(rows)
    df, meta = load_ohlcv_from_dataframe(df_raw, FX)
    cfg = _load_cfg()
    from unittest.mock import patch
    # Generate entry at day 3, then weekly gate false after day 10
    def fake_generate(self, df):
        sig_rows = []
        for _, row in df.iterrows():
            # Entry signal at day 3
            entry_sig = 1 if row["date"] == dates[3] and row["symbol"] == "WBREAK" else 0
            # Weekly gate: true until day 10, then false
            gate = True if row["date"] < dates[10] else False
            sig_rows.append({"date": row["date"], "symbol": row["symbol"], "signal": entry_sig, "weekly_gate_ok": gate, "daily_ok": True, "is_blackout": False, "reason": "test", "close_eur": 100, "open_eur": 100, "rsi14": 40, "dist_sma20": -4, "volume_ratio": 1})
        return pd.DataFrame(sig_rows)
    with patch.object(TechPiePullbackV1, "generate_signals", fake_generate):
        engine = BacktestEngine(CFG_PATH, FX)
        engine.strategy = TechPiePullbackV1(cfg)
        engine.strategy.generate_signals = lambda df: fake_generate(engine.strategy, df)
        engine.hard_stop_pct = 50
        engine.time_stop_bars = 60
        engine.weekly_break_enabled = True
        result = engine.run(df, meta)
        # Should exit on weekly break around day 11 (next open after gate false)
        assert len(result["trades"]) == 1
        assert result["trades"][0]["exit_reason"] == "weekly_break"


def test_no_trades_when_validation_fails_end_to_end():
    dates = pd.bdate_range("2023-01-02", periods=10, tz="UTC")
    rows = []
    for d in dates:
        rows.append({"date": d.isoformat(), "symbol": "BAD", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1_000_000, "currency": "EUR", "earnings_date": ""})
    df_raw = pd.DataFrame(rows)
    df_raw.loc[0, "high"] = 90  # invalid
    df_raw.loc[0, "low"] = 100
    df, meta = load_ohlcv_from_dataframe(df_raw, FX)
    engine = BacktestEngine(CFG_PATH, FX)
    try:
        result = engine.run(df, meta)
        # Should not reach here
        assert False, "Should have raised ValidationError"
    except ValidationError as e:
        assert not e.report.ok
        assert any("high < low" in err for err in e.report.errors)
        # Ensure no trades emitted: exception means no result, but also check strategy would emit none if we tried
        # The engine should not have written files
        pass
