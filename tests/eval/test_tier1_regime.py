"""T06 regime scenarios on deterministic synthetic series. Offline."""
from __future__ import annotations

import numpy as np
import pandas as pd

from investment_engine.research.ta_snapshot import build_snapshot, classify_regime


def _frame(close: np.ndarray) -> pd.DataFrame:
    n = len(close)
    idx = pd.date_range("2022-01-01", periods=n, freq="B")
    rng = np.random.RandomState(0)
    return pd.DataFrame({
        "open": np.concatenate([[close[0]], close[:-1]]),
        "high": close * 1.002, "low": close * 0.998, "close": close,
        "volume": rng.randint(500_000, 5_000_000, n).astype(float),
    }, index=idx)


def _sma(values: np.ndarray, n: int) -> np.ndarray:
    out = np.full_like(values, np.nan, dtype=float)
    out[n - 1:] = np.convolve(values, np.ones(n) / n, mode="valid")
    return out


def _label(close: np.ndarray) -> str:
    s50 = _sma(close, 50)
    s200 = _sma(close, 200)
    slope = s200[-1] / s200[-21] - 1.0
    return classify_regime(close[-1], s50[-1], s200[-1], slope)


def test_uptrend_and_downtrend():
    t = np.arange(600)
    up = 100 * 1.0005 ** t * (1 + 0.004 * np.sin(t / 7))
    assert _label(up) == "STRONG_UPTREND"
    down = 100 * 0.9995 ** t * (1 + 0.004 * np.sin(t / 7))
    assert _label(down) == "DOWNTREND"


def test_pullback_and_bear_rally_with_preconditions():
    t = np.arange(520)
    base = 100 * 1.0005 ** t
    pb = np.concatenate([base, base[-1] * (1 - np.linspace(0.001, 0.03, 15))])
    s50, s200 = _sma(pb, 50), _sma(pb, 200)
    slope = s200[-1] / s200[-21] - 1.0
    assert pb[-1] <= s50[-1] and pb[-1] > s200[-1] and slope > 0
    assert _label(pb) == "UPTREND_PULLBACK"
    db = 100 * 0.9995 ** np.arange(520)
    br = np.concatenate([db, db[-1] * (1 + np.linspace(0.001, 0.10, 15))])
    s50b, s200b = _sma(br, 50), _sma(br, 200)
    slopeb = s200b[-1] / s200b[-21] - 1.0
    assert s50b[-1] < s200b[-1] and slopeb <= 0 and br[-1] > s50b[-1]
    assert _label(br) == "BEAR_RALLY"


def test_range_is_transition():
    # Flat market: s50 == s200 exactly, so no trend branch can match.
    flat = np.full(600, 100.0)
    assert _label(flat) == "TRANSITION_OR_RANGE"


def test_exactly_one_label_per_bar_and_weekly_monthly():
    rng = np.random.RandomState(2)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.015, 600))
    s50_full = _sma(close, 50)
    s200_full = _sma(close, 200)
    labels = set()
    for i in range(220, 600):
        s = s200_full[i - 20] if i >= 220 else float("nan")
        slope = s200_full[i] / s200_full[i - 20] - 1.0
        labels.add(classify_regime(close[i], s50_full[i], s200_full[i], slope))
    assert labels <= {"STRONG_UPTREND", "UPTREND_PULLBACK", "TRANSITION_OR_RANGE",
                      "DOWNTREND", "BEAR_RALLY"}
    assert len(labels) >= 2  # noise visits several regimes, each exactly one
    snap = build_snapshot(_frame(close), symbol="R")
    assert snap["weekly"]["regime"] in {"STRONG_UPTREND", "UPTREND_PULLBACK",
                                        "TRANSITION_OR_RANGE", "DOWNTREND",
                                        "BEAR_RALLY", ""}
    assert snap["monthly"]["state"] in {"ABOVE_RISING", "ABOVE_FALLING",
                                        "BELOW_RISING", "BELOW_FALLING", ""}


def test_extended_on_parabolic_not_on_range():
    t = np.arange(600)
    parab = 100 * 1.01 ** t
    snap = build_snapshot(_frame(parab), symbol="P")
    assert "EXTENDED" in snap["daily"]["modifiers"]
    rng = 100 + 3 * np.sin(2 * np.pi * np.arange(600) / 60)
    snap2 = build_snapshot(_frame(rng), symbol="R")
    assert "EXTENDED" not in snap2["daily"]["modifiers"]
