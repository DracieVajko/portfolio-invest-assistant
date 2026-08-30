"""Indicator correctness - Wilder vs SMA, warmup, no future access."""
import pandas as pd
import numpy as np
import pytest

from experimental.backtest.indicators import (
    sma, ema, wilder_rma, rsi, rsi_sma_variant,
    true_range, atr, atr_sma_variant,
    macd, bollinger_bands, adx, adx_sma_variant,
    supertrend, obv, vwap_daily, rolling_vwap,
)


def _make_trend_series(n=50, seed=42):
    np.random.seed(seed)
    base = 100 + np.cumsum(np.random.randn(n) * 0.5 + 0.1)
    dates = pd.date_range("2024-01-01", periods=n, freq="B", tz="UTC")
    close = pd.Series(base, index=dates)
    high = close * 1.01
    low = close * 0.99
    volume = pd.Series(np.random.randint(1_000_000, 5_000_000, n), index=dates)
    return close, high, low, volume, dates


def test_sma_warmup_nan():
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    out = sma(s, 3)
    assert pd.isna(out.iloc[0])
    assert pd.isna(out.iloc[1])
    assert out.iloc[2] == 2.0  # (1+2+3)/3
    assert out.iloc[4] == 4.0


def test_ema_warmup_nan():
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    out = ema(s, 3)
    assert pd.isna(out.iloc[0])
    assert pd.isna(out.iloc[1])
    # third valid
    assert not pd.isna(out.iloc[2])


def test_wilder_rma_warmup():
    s = pd.Series([1, 2, 3, 4, 5, 6], dtype=float)
    out = wilder_rma(s, 3)
    assert pd.isna(out.iloc[0])
    assert pd.isna(out.iloc[1])
    assert not pd.isna(out.iloc[2])


def test_rsi_wilder_differs_from_sma():
    close, _, _, _, _ = _make_trend_series(60)
    rsi_w = rsi(close, 14)
    rsi_s = rsi_sma_variant(close, 14)
    # Warmup both NaN for first 13, then values exist and differ
    valid = rsi_w.notna() & rsi_s.notna()
    assert valid.sum() > 20
    diff = (rsi_w[valid] - rsi_s[valid]).abs()
    # Wilder vs SMA must differ meaningfully (not identical)
    assert diff.mean() > 0.1
    # Also check Wilder is not same as SMA raw
    assert not np.allclose(rsi_w[valid].values, rsi_s[valid].values, atol=1e-6)
    # RSI bounds 0-100 where valid
    assert ((rsi_w[valid] >= 0) & (rsi_w[valid] <= 100)).all()


def test_atr_wilder_differs_from_sma():
    close, high, low, _, _ = _make_trend_series(60)
    atr_w = atr(high, low, close, 14)
    atr_s = atr_sma_variant(high, low, close, 14)
    valid = atr_w.notna() & atr_s.notna()
    assert valid.sum() > 20
    assert not np.allclose(atr_w[valid].values, atr_s[valid].values, atol=1e-6)
    # ATR positive
    assert (atr_w[valid] > 0).all()


def test_adx_wilder_differs_from_sma():
    close, high, low, _, _ = _make_trend_series(80)
    adx_w = adx(high, low, close, 14)
    adx_s = adx_sma_variant(high, low, close, 14)
    valid = adx_w["ADX"].notna() & adx_s["ADX"].notna()
    assert valid.sum() > 10
    # Must differ
    assert not np.allclose(adx_w.loc[valid, "ADX"].values, adx_s.loc[valid, "ADX"].values, atol=1e-6)
    # ADX 0-100
    assert ((adx_w.loc[valid, "ADX"] >= 0) & (adx_w.loc[valid, "ADX"] <= 100)).all()
    # +DI, -DI also
    assert "PLUS_DI" in adx_w.columns and "MINUS_DI" in adx_w.columns


def test_macd_no_future_access():
    close, _, _, _, _ = _make_trend_series(40)
    out = macd(close, 12, 26, 9)
    # Check warmup: first 25 at least NaN for slow EMA
    assert pd.isna(out["MACD"].iloc[0])
    assert not pd.isna(out["MACD"].iloc[30])
    # Appending future bar should not change earlier values
    close2 = pd.concat([close, pd.Series([close.iloc[-1] * 2], index=[close.index[-1] + pd.Timedelta(days=1)])])
    out2 = macd(close2, 12, 26, 9)
    # First 30 same (ignore freq)
    pd.testing.assert_series_equal(out["MACD"].iloc[:30], out2["MACD"].iloc[:30], check_names=False, check_freq=False)


def test_bollinger_bands():
    close, _, _, _, _ = _make_trend_series(40)
    bb = bollinger_bands(close, 20, 2.0)
    valid = bb["BB_MIDDLE"].notna()
    assert valid.sum() > 10
    # Upper > middle > lower
    assert (bb.loc[valid, "BB_UPPER"] > bb.loc[valid, "BB_MIDDLE"]).all()
    assert (bb.loc[valid, "BB_MIDDLE"] > bb.loc[valid, "BB_LOWER"]).all()


def test_supertrend_stateful():
    # Create uptrend then downtrend
    dates = pd.date_range("2024-01-01", periods=60, freq="B", tz="UTC")
    # Uptrend 30 then down 30
    prices = list(100 + np.arange(30) * 0.5) + list(115 - np.arange(30) * 0.8)
    close = pd.Series(prices, index=dates)
    high = close + 1
    low = close - 1
    st = supertrend(high, low, close, 10, 3.0)
    # Warmup NaN for first period
    assert pd.isna(st["SUPERT"].iloc[0])
    assert not pd.isna(st["SUPERT"].iloc[15])
    # DIR should be 1 and -1
    valid = st["SUPERT_DIR"].notna()
    assert set(st.loc[valid, "SUPERT_DIR"].unique()).issubset({1.0, -1.0})
    # Check stateful: direction flips at least once
    dirs = st.loc[valid, "SUPERT_DIR"].tolist()
    assert 1.0 in dirs and -1.0 in dirs


def test_obv_and_vwap():
    close, high, low, volume, dates = _make_trend_series(20)
    obv_s = obv(close, volume)
    assert len(obv_s) == len(close)
    assert not pd.isna(obv_s.iloc[-1])
    vw = vwap_daily(high, low, close, volume)
    # For daily, VWAP == typical
    typical = (high + low + close) / 3
    pd.testing.assert_series_equal(vw, typical.rename("VWAP"))
    # Rolling VWAP distinct
    rv = rolling_vwap(high, low, close, volume, 5)
    assert rv.name == "ROLLING_VWAP_5"
    assert pd.isna(rv.iloc[0])
    assert not pd.isna(rv.iloc[6])
    assert not (vw == rv).all()  # should differ


def test_no_future_access_indicators():
    # Ensure indicators don't use future rows: compare before/after appending extreme future bar
    close, high, low, volume, dates = _make_trend_series(30)
    rsi_before = rsi(close, 14)
    atr_before = atr(high, low, close, 14)
    # Append extreme - ensure business day
    new_date = dates[-1] + pd.offsets.BDay(1)
    close2 = pd.concat([close, pd.Series([1.0], index=[new_date])])
    high2 = pd.concat([high, pd.Series([2.0], index=[new_date])])
    low2 = pd.concat([low, pd.Series([0.5], index=[new_date])])
    rsi_after = rsi(close2, 14)
    atr_after = atr(high2, low2, close2, 14)
    # First len(before) values unchanged (ignore freq)
    pd.testing.assert_series_equal(rsi_before, rsi_after.iloc[:len(rsi_before)], check_names=False, check_freq=False)
    pd.testing.assert_series_equal(atr_before, atr_after.iloc[:len(atr_before)], check_names=False, check_freq=False)
