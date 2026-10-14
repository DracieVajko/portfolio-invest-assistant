"""T07 short history + T08 basis + T09 quality, eval-named. Offline."""
from __future__ import annotations

import numpy as np
import pandas as pd

from investment_engine.research.ta_snapshot import build_snapshot


def _frame(n: int = 400, seed: int = 33) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    close = 100 * np.cumprod(1 + rng.normal(0.0008, 0.012, n))
    idx = pd.date_range("2024-06-01", periods=n, freq="B")
    return pd.DataFrame({
        "open": np.concatenate([[100.0], close[:-1]]),
        "high": close * 1.004, "low": close * 0.996, "close": close,
        "volume": rng.randint(500_000, 5_000_000, n).astype(float),
    }, index=idx)


def test_t07_short_fund_gates():
    snap = build_snapshot(_frame(), symbol="SHORT")
    assert snap["daily"]["sma200"] is not None
    assert snap["daily"]["regime"] != ""
    assert snap["daily"]["ext_atr_pctile"] is None
    assert snap["distributions"]["rolling_3y"]["p50"] is None
    assert snap["distributions"]["rolling_5y"]["p50"] is None
    assert snap["relative"]["up_capture"] is None
    assert any("short_history" in f for f in snap["data"]["quality_flags"])
    assert snap["distributions"]["rolling_1y"]["p50"] is not None


def test_t08_basis_detection():
    idx = pd.date_range("2024-01-01", periods=300, freq="B")
    rng = np.random.RandomState(6)
    close = 100 * np.cumprod(1 + rng.normal(0.0008, 0.012, 300))
    base = {"open": close, "high": close * 1.004, "low": close * 0.996,
            "close": close, "volume": np.full(300, 1_000_000.0)}
    auto = pd.DataFrame({**base, "adj_close": close.copy()}, index=idx)
    snap = build_snapshot(auto, symbol="AUTO", ex_div_dates=["2024-06-01"])
    assert snap["data"]["price_basis"]["levels"] == "adjusted_only"
    assert snap["levels"] == {"support": [], "resistance": []}
    proper = pd.DataFrame({**base, "adj_close": close * 0.98}, index=idx)
    snap2 = build_snapshot(proper, symbol="OK", ex_div_dates=["2024-06-01"])
    assert snap2["data"]["price_basis"]["levels"] == "split_adjusted"
    assert snap2["levels"]["support"] != [] or snap2["levels"]["resistance"] != []


def _gap_frame(drop: float, seed: int = 9) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    n = 300
    close = 100 * np.cumprod(1 + rng.normal(0.0005, 0.008, n))
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    # Gap sits inside the last-60-bar evidence window (bar 260 of 300).
    close[260:] = close[260:] * (1 - drop)
    op = np.concatenate([[100.0], close[:-1]])
    op[260] = close[260]
    return pd.DataFrame({
        "open": op, "high": close * 1.004, "low": close * 0.996,
        "close": close,
        "volume": rng.randint(500_000, 5_000_000, n).astype(float),
    }, index=idx)


def test_t09a_unadjusted_split_refuses():
    snap = build_snapshot(_gap_frame(0.75), symbol="SPLIT")
    assert any("bad_data_possible_split" in f for f in snap["data"]["quality_flags"])
    assert snap["daily"]["rsi14"] is None and snap["daily"]["regime"] == ""


def test_t09b_split_entry_accepted():
    frame = _gap_frame(0.75)
    snap = build_snapshot(frame, symbol="SPLITOK",
                          splits={frame.index[260].date().isoformat(): 0.25})
    assert not any("bad_data_possible_split" in f for f in snap["data"]["quality_flags"])
    assert snap["daily"]["rsi14"] is not None


def test_t09c_earnings_gap_is_gap_not_bad_data():
    snap = build_snapshot(_gap_frame(0.18), symbol="EARN")
    assert not any("bad_data_possible_split" in f for f in snap["data"]["quality_flags"])
    assert len(snap["gaps"]) >= 1


def test_t09d_missing_weekdays_and_zero_run_and_duplicates():
    idx = list(pd.date_range("2024-01-08", periods=20, freq="B"))
    idx = [d for i, d in enumerate(idx) if i not in (3, 4)] + [idx[5]]
    n = len(idx)
    rng = np.random.RandomState(4)
    close = 100 * np.cumprod(1 + rng.normal(0.0005, 0.008, n))
    vol = rng.randint(500_000, 5_000_000, n).astype(float)
    vol[8:12] = 0
    frame = pd.DataFrame({"open": close, "high": close * 1.004,
                          "low": close * 0.996, "close": close, "volume": vol},
                         index=pd.DatetimeIndex(idx))
    snap = build_snapshot(frame, symbol="GAPPY")
    flags = " ".join(snap["data"]["quality_flags"])
    assert "missing 2 weekday" in flags
    assert "zero-volume run of 4" in flags
    assert "duplicate bars" in flags
