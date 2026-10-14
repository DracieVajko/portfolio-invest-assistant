"""Appendix B8 sanity tests + schema contract + reference comparison.

Offline (synthetic frames), except the CLI which needs network.
pandas-ta comparison runs only when installed (importorskip).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from investment_engine.research.ta_snapshot import (
    _adx_wilder,
    _atr_wilder,
    _bollinger,
    _cagr,
    _drawdown_series,
    _ema,
    _macd,
    _rsi_wilder,
    _sma,
    build_snapshot,
    classify_regime,
    last_complete_bar,
)


def _frame(n: int = 900, seed: int = 7) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    close = 100 * np.cumprod(1 + rng.normal(0.0008, 0.012, n))
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "open": np.concatenate([[100.0], close[:-1]]),
        "high": close * 1.004, "low": close * 0.996,
        "close": close,
        "adj_close": close,
        "volume": rng.randint(500_000, 5_000_000, n).astype(float),
    }, index=idx)


def test_sma_exact():
    assert float(_sma(np.arange(1.0, 101.0), 100)[-1]) == pytest.approx(50.5)


def test_rsi_bounds_and_constants():
    up = np.arange(1.0, 301.0)
    assert _rsi_wilder(up)[-1] == pytest.approx(100.0)
    down = np.arange(300.0, 0.0, -1.0)
    assert _rsi_wilder(down)[-1] == pytest.approx(0.0)
    flat = np.full(300, 42.0)
    assert _rsi_wilder(flat)[-1] == pytest.approx(50.0)
    noisy = 100 + np.random.RandomState(1).normal(0, 1, 500)
    rsi = _rsi_wilder(noisy)
    assert bool(np.all((rsi[14:] >= 0) & (rsi[14:] <= 100)))


def test_atr_nonnegative_and_flat_zero():
    frame = _frame()
    atr = _atr_wilder(frame["high"].to_numpy(), frame["low"].to_numpy(),
                      frame["close"].to_numpy())
    assert bool(np.all(atr[14:] >= 0))
    flat = np.full(100, 10.0)
    assert _atr_wilder(flat, flat, flat)[-1] == pytest.approx(0.0)


def test_bollinger_ordering_and_pct_b():
    close = _frame()["close"].to_numpy()
    upper, lower, pct_b, _, mid = _bollinger(close)
    assert bool(np.all(upper[19:] >= mid[19:])) and bool(np.all(mid[19:] >= lower[19:]))
    touch = np.where(np.abs(close - upper) < 1e-9)[0]
    if touch.size:
        assert pct_b[touch[-1]] == pytest.approx(1.0)


def test_drawdown_invariants():
    adj = _frame()["close"].to_numpy()
    dd = _drawdown_series(adj)
    assert bool(np.all(dd[~np.isnan(dd)] <= 0))
    peak_idx = np.argmax(np.maximum.accumulate(adj))
    assert dd[peak_idx] == pytest.approx(0.0)
    snap = build_snapshot(_frame(), symbol="TST")
    assert snap["drawdown"]["max"] is not None and snap["drawdown"]["max"] <= 0


def test_beta_corr_self_is_one():
    frame = _frame(n=900)
    snap = build_snapshot(frame, symbol="TST", benchmark=frame.copy(),
                          benchmark_meta={"currency": "USD"}, currency="USD")
    assert snap["relative"]["beta_weekly"] == pytest.approx(1.0)
    assert snap["relative"]["corr_weekly"] == pytest.approx(1.0)
    assert snap["relative"]["n_weeks"] >= 52


def test_cagr_doubling():
    from investment_engine.research.ta_snapshot import _cagr as cagr

    assert cagr(100.0, 200.0, 365.25) == pytest.approx(1.0)
    assert _cagr(100.0, 200.0, 100.0) is None  # sub-year: not annualized


def test_weekly_resample_invariants():
    frame = _frame()
    from investment_engine.research.ta_snapshot import _resample_weekly

    weekly = _resample_weekly(frame)
    day = frame.loc["2020-01-06":"2020-01-10"]
    week = weekly.loc["2020-01-10"]
    assert week["high"] >= day["high"].max() - 1e-9
    assert week["volume"] == pytest.approx(day["volume"].sum())


def test_regime_synthetic_trends():
    up = 100.0 + np.arange(500.0)
    assert classify_regime(up[-1], np.mean(up[-50:]), np.mean(up[-200:]),
                           np.mean(up[-200:]) / np.mean(up[-220:-20]) - 1) == "STRONG_UPTREND"
    down = 10000.0 - np.arange(500.0)
    assert classify_regime(down[-1], np.mean(down[-50:]), np.mean(down[-200:]),
                           np.mean(down[-200:]) / np.mean(down[-220:-20]) - 1) == "DOWNTREND"
    assert classify_regime(float("nan"), 1.0, 1.0, 0.0) == "TRANSITION_OR_RANGE"


def test_regime_exactly_one():
    rng = np.random.RandomState(3)
    for trial in range(25):
        close = 100 * np.cumprod(1 + rng.normal(0, 0.02, 400))
        s50 = np.mean(close[-50:])
        s200 = np.mean(close[-200:])
        slope = s200 / np.mean(close[-220:-20]) - 1
        assert classify_regime(close[-1], s50, s200, slope) in {
            "STRONG_UPTREND", "UPTREND_PULLBACK", "TRANSITION_OR_RANGE",
            "DOWNTREND", "BEAR_RALLY"}


def test_schema_complete():
    snap = build_snapshot(_frame(), symbol="TST", isin="XX1",
                          benchmark=_frame(), benchmark_meta={"ticker": "B"})
    for key in ("schema_version", "generated_utc", "asset", "data", "daily",
                "weekly", "monthly", "returns", "relative", "drawdown",
                "distributions", "levels", "gaps", "events", "stress_windows",
                "attribution", "tests", "files"):
        assert key in snap, key
    for key in ("close", "sma50", "sma200", "rsi14", "macd", "atr14", "adx14",
                "bollinger", "regime", "modifiers", "high52", "ath"):
        assert key in snap["daily"], key
    assert snap["schema_version"] == 1
    assert snap["daily"]["regime"] in {"STRONG_UPTREND", "UPTREND_PULLBACK",
                                       "TRANSITION_OR_RANGE", "DOWNTREND", "BEAR_RALLY"}


def test_empty_and_short_frames_never_raise():
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    snap = build_snapshot(empty, symbol="X")
    assert snap["data"]["n_bars"] == 0
    short = _frame(n=30)
    snap = build_snapshot(short, symbol="X")
    assert snap["data"]["n_bars"] == 30
    assert snap["daily"]["sma200"] is None
    assert any("percentile" in n for n in snap["data"]["notes"])


def test_last_complete_bar_rule():
    monday_morning = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)  # 05:00 ET
    assert last_complete_bar(monday_morning) == date(2026, 10, 9)
    monday_evening = datetime(2026, 10, 12, 22, 0, tzinfo=timezone.utc)  # 18:00 ET
    assert last_complete_bar(monday_evening) == date(2026, 10, 12)
    saturday = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)  # 08:00 ET Sat
    assert last_complete_bar(saturday) == date(2026, 10, 9)


def _short_frame(n: int = 400, seed: int = 21) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    close = 100 * np.cumprod(1 + rng.normal(0.0008, 0.012, n))
    idx = pd.date_range("2024-06-01", periods=n, freq="B")
    return pd.DataFrame({
        "open": np.concatenate([[100.0], close[:-1]]),
        "high": close * 1.004, "low": close * 0.996, "close": close,
        "volume": rng.randint(500_000, 5_000_000, n).astype(float),
    }, index=idx)


def test_short_history_gates():
    """T07: 400 bars -> regime ok, percentiles/3y-5y/capture null, flagged."""
    snap = build_snapshot(_short_frame(), symbol="SHORT")
    assert snap["daily"]["sma200"] is not None
    assert snap["daily"]["regime"] in {"STRONG_UPTREND", "UPTREND_PULLBACK",
                                       "TRANSITION_OR_RANGE", "DOWNTREND", "BEAR_RALLY"}
    assert snap["daily"]["ext_atr_pctile"] is None
    assert snap["distributions"]["rolling_3y"]["p50"] is None
    assert snap["distributions"]["rolling_5y"]["p50"] is None
    assert snap["relative"]["up_capture"] is None
    assert any("756" in n for n in snap["data"]["notes"])
    assert snap["distributions"]["rolling_1y"]["p50"] is not None


def test_short_history_no_regime_under_1y():
    snap = build_snapshot(_short_frame(n=100), symbol="TINY")
    assert snap["daily"]["regime"] == ""
    assert any("252" in n for n in snap["data"]["notes"])


def test_price_basis_autoadjusted_vs_proper():
    """T08: close==adj before a known ex-div -> adjusted_only, no zones."""
    from investment_engine.research.ta_snapshot import build_snapshot as build

    idx = pd.date_range("2024-01-01", periods=300, freq="B")
    rng = np.random.RandomState(5)
    close = 100 * np.cumprod(1 + rng.normal(0.0008, 0.012, 300))
    auto = pd.DataFrame({"open": close, "high": close * 1.004,
                         "low": close * 0.996, "close": close,
                         "adj_close": close.copy(),
                         "volume": np.full(300, 1_000_000.0)}, index=idx)
    snap = build(auto, symbol="AUTO", ex_div_dates=["2024-06-01"])
    assert snap["data"]["price_basis"]["levels"] == "adjusted_only"
    assert snap["levels"] == {"support": [], "resistance": []}
    assert any("auto-adjusted" in f for f in snap["data"]["quality_flags"])
    proper = auto.copy()
    proper["adj_close"] = proper["close"] * 0.98
    snap2 = build(proper, symbol="OK", ex_div_dates=["2024-06-01"])
    assert snap2["data"]["price_basis"]["levels"] == "split_adjusted"
    assert snap2["levels"]["support"] != [] or snap2["levels"]["resistance"] != []


def _gap_frame(drop: float = 0.75, seed: int = 9) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    n = 300
    close = 100 * np.cumprod(1 + rng.normal(0.0005, 0.008, n))
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    close[200:] = close[200:] * (1 - drop)
    # Opens track closes, except bar 200 whose open prints at the post-drop
    # level: the ONLY overnight gap (-75%) sits exactly on bar 200.
    op = np.concatenate([[100.0], close[:-1]])
    op[200] = close[200]
    return pd.DataFrame({
        "open": op,
        "high": close * 1.004, "low": close * 0.996, "close": close,
        "volume": rng.randint(500_000, 5_000_000, n).astype(float),
    }, index=idx)


def test_unadjusted_split_refuses_indicators():
    """T09a: 75% overnight drop, no corporate action -> refusal."""
    snap = build_snapshot(_gap_frame(), symbol="SPLIT")
    assert any("bad_data_possible_split" in f for f in snap["data"]["quality_flags"])
    assert snap["daily"]["rsi14"] is None
    assert snap["daily"]["regime"] == ""
    assert snap["levels"] == {"support": [], "resistance": []}
    assert snap["drawdown"]["max"] is not None  # evidence stays


def test_split_with_entry_accepted():
    """T09b: same drop with a split entry -> adjusted, indicators present."""
    frame = _gap_frame()
    split_day = frame.index[200].date().isoformat()
    snap = build_snapshot(frame, symbol="SPLITOK", splits={split_day: 0.25})
    assert not any("bad_data_possible_split" in f for f in snap["data"]["quality_flags"])
    assert snap["daily"]["rsi14"] is not None
    assert any("split-adjusted" in n for n in snap["data"]["notes"])


def test_quality_flag_counts():
    """T09d-f: missing weekdays, zero-volume run, duplicate dates."""
    idx = list(pd.date_range("2024-01-08", periods=20, freq="B"))
    idx = [d for i, d in enumerate(idx) if i not in (3, 4)]  # drop 2 sessions
    idx = idx + [idx[5]]  # duplicate one date
    n = len(idx)
    rng = np.random.RandomState(4)
    close = 100 * np.cumprod(1 + rng.normal(0.0005, 0.008, n))
    vol = rng.randint(500_000, 5_000_000, n).astype(float)
    vol[8:12] = 0  # 4-bar zero run
    frame = pd.DataFrame({"open": close, "high": close * 1.004,
                          "low": close * 0.996, "close": close, "volume": vol},
                         index=pd.DatetimeIndex(idx))
    snap = build_snapshot(frame, symbol="GAPPY")
    flags = " ".join(snap["data"]["quality_flags"])
    assert "missing 2 weekday" in flags
    assert "zero-volume run of 4" in flags
    assert "duplicate bars" in flags


def test_selftests_recorded_in_snapshot():
    from investment_engine.research.ta_snapshot import run_snapshot_selftests

    assert run_snapshot_selftests() == {"passed": True, "failed": []}
    snap = build_snapshot(_short_frame(), symbol="X")
    assert snap["tests"]["passed"] is True
    assert snap["tests"]["failed"] == []


def test_reference_library_comparison():
    """B8: SMA/EMA <1e-6 post-warmup; Wilder indicators small diffs."""
    pd_ta = pytest.importorskip("pandas_ta")
    import pandas_ta as ta

    close = _frame(n=900)["close"]
    got_sma = _sma(close.to_numpy(), 50)
    ref_sma = ta.sma(close, length=50).to_numpy()
    assert float(np.nanmax(np.abs(got_sma[150:] - ref_sma[150:]))) < 1e-6
    got_ema = _ema(close.to_numpy(), 50)
    ref_ema = ta.ema(close, length=50).to_numpy()
    assert float(np.nanmax(np.abs(got_ema[150:] - ref_ema[150:]))) < 1e-6
    frame = _frame(n=900)
    s = frame["close"]
    assert float(np.nanmax(np.abs(_rsi_wilder(s.to_numpy())[150:]
                                  - ta.rsi(s, length=14).to_numpy()[150:]))) < 0.5
    h, l = frame["high"], frame["low"]
    got_atr = _atr_wilder(h.to_numpy(), l.to_numpy(), s.to_numpy())
    ref_atr = ta.atr(h, l, s, length=14).to_numpy()
    assert float(np.nanmax(np.abs(got_atr[150:] - ref_atr[150:])
                           / np.maximum(got_atr[150:], 1e-9))) < 0.01
