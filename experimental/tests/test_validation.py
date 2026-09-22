"""Strict validation fail-closed tests."""
import numpy as np
import pandas as pd
import pytest

from experimental.backtest.io import load_ohlcv_from_dataframe
from experimental.backtest.validate import validate_dataframe, ValidationError
from experimental.backtest.engine import BacktestEngine
import json
from pathlib import Path


FX = Path("experimental/backtest/config/fx_rates.json")


def _base_df(n=10, currency="EUR"):
    dates = pd.date_range("2024-01-01", periods=n, freq="B", tz="UTC")
    rows = []
    for i, d in enumerate(dates):
        rows.append({
            "date": d.isoformat(),
            "symbol": "TEST",
            "open": 100 + i,
            "high": 101 + i,
            "low": 99 + i,
            "close": 100.5 + i,
            "volume": 1000000,
            "currency": currency,
            "earnings_date": "",
        })
    return pd.DataFrame(rows)


def test_high_low_blocks():
    df_raw = _base_df(5)
    df_raw.loc[2, "high"] = 90  # high < low
    df_raw.loc[2, "low"] = 95
    df, meta = load_ohlcv_from_dataframe(df_raw, FX)
    report = validate_dataframe(df, data_hash=meta["data_hash"])
    assert not report.ok
    assert any("high < low" in e for e in report.errors)
    # Engine must raise
    engine = BacktestEngine("experimental/backtest/config/tech_pie_pullback_v1.json", FX)
    with pytest.raises(ValidationError):
        engine.run(df, meta)


def test_close_le_zero_blocks():
    df_raw = _base_df(5)
    df_raw.loc[1, "close"] = 0
    df, meta = load_ohlcv_from_dataframe(df_raw, FX)
    report = validate_dataframe(df, data_hash=meta["data_hash"])
    assert not report.ok
    assert any("close <= 0" in e for e in report.errors)
    engine = BacktestEngine("experimental/backtest/config/tech_pie_pullback_v1.json", FX)
    with pytest.raises(ValidationError):
        engine.run(df, meta)


def test_open_le_zero_blocks():
    df_raw = _base_df(5)
    df_raw.loc[0, "open"] = -1
    df, meta = load_ohlcv_from_dataframe(df_raw, FX)
    report = validate_dataframe(df, data_hash=meta["data_hash"])
    assert not report.ok
    assert any("open <= 0" in e for e in report.errors)


def test_negative_volume_blocks():
    df_raw = _base_df(5)
    df_raw.loc[3, "volume"] = -100
    df, meta = load_ohlcv_from_dataframe(df_raw, FX)
    report = validate_dataframe(df, data_hash=meta["data_hash"])
    assert not report.ok
    assert any("negative volume" in e for e in report.errors)


def test_duplicate_date_blocks():
    df_raw = _base_df(5)
    # duplicate first row
    dup = df_raw.iloc[0].copy()
    df_raw = pd.concat([df_raw, dup.to_frame().T], ignore_index=True)
    df, meta = load_ohlcv_from_dataframe(df_raw, FX)
    report = validate_dataframe(df, data_hash=meta["data_hash"])
    assert not report.ok
    assert any("duplicate" in e for e in report.errors)
    engine = BacktestEngine("experimental/backtest/config/tech_pie_pullback_v1.json", FX)
    with pytest.raises(ValidationError) as exc:
        engine.run(df, meta)
    # strategy must emit no trades -> check exception report has no trades
    assert exc.value.report.ok is False


def test_missing_required_data():
    df_raw = _base_df(5)
    df_raw.loc[2, "close"] = np.nan  # will become NaN
    df, meta = load_ohlcv_from_dataframe(df_raw, FX)
    report = validate_dataframe(df, data_hash=meta["data_hash"])
    assert not report.ok
    assert any("missing required data" in e for e in report.errors)


def test_gap_larger_than_two_bdays():
    # Create gap of 5 business days (Mon -> next Tue would be 6? Let's do 7 days gap)
    dates = [
        "2024-01-01T00:00:00Z",  # Mon
        "2024-01-02T00:00:00Z",  # Tue
        "2024-01-10T00:00:00Z",  # Wed next week -> gap of 5 bdays
    ]
    rows = []
    for d in dates:
        rows.append({"date": d, "symbol": "GAP", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1_000_000, "currency": "EUR", "earnings_date": ""})
    df_raw = pd.DataFrame(rows)
    df, meta = load_ohlcv_from_dataframe(df_raw, FX)
    report = validate_dataframe(df, data_hash=meta["data_hash"], allow_gap_larger_than_two_bdays=False)
    assert not report.ok
    assert any("gap larger" in e for e in report.errors)
    # Allow gap true -> ok
    report2 = validate_dataframe(df, data_hash=meta["data_hash"], allow_gap_larger_than_two_bdays=True)
    assert report2.ok
    assert any("gap check skipped" in w for w in report2.warnings)


def test_gap_two_bdays_allowed():
    # Fri -> Tue is 2 missing bdays? Fri 2024-01-05 to Tue 2024-01-09 gap_bdays=2? Actually Fri->Mon=1, Fri->Tue=2, Fri->Wed=3 -> should be ok for <=2 missing (gap_bdays<=3)
    # Our threshold is gap_bdays>3 fails, so Fri->Tue (gap 2) ok, Fri->Wed (gap 3) ok, Fri->Thu (gap 4) fails
    # Test Fri->Wed should still pass
    dates_ok = ["2024-01-05T00:00:00Z", "2024-01-10T00:00:00Z"]  # Fri -> Wed (3 bdays after Fri) -> actually Fri 5th to Wed 10th: Mon 8th, Tue 9th, Wed 10th =3
    rows = [{"date": d, "symbol": "OK", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1_000_000, "currency": "EUR", "earnings_date": ""} for d in dates_ok]
    df_raw = pd.DataFrame(rows)
    df, meta = load_ohlcv_from_dataframe(df_raw, FX)
    report = validate_dataframe(df, data_hash=meta["data_hash"])
    assert report.ok, report.errors


def test_stale_last_bar():
    df_raw = _base_df(5)  # last is 2024-01-05 Fri? Actually 2024-01-01 Mon +5 bdays = Fri 05
    df, meta = load_ohlcv_from_dataframe(df_raw, FX)
    # as_of 10 days later should be stale with threshold 3
    report = validate_dataframe(df, data_hash=meta["data_hash"], as_of="2024-01-15T00:00:00Z", stale_threshold_bdays=3)
    assert not report.ok
    assert any("stale" in e for e in report.errors)
    # recent as_of not stale
    report2 = validate_dataframe(df, data_hash=meta["data_hash"], as_of="2024-01-08T00:00:00Z", stale_threshold_bdays=3)
    assert report2.ok


def test_no_trades_when_validation_fails():
    # Engine must not write metrics/files when validation fails
    df_raw = _base_df(5)
    df_raw.loc[0, "high"] = 1
    df_raw.loc[0, "low"] = 100  # high<low
    df, meta = load_ohlcv_from_dataframe(df_raw, FX)
    engine = BacktestEngine("experimental/backtest/config/tech_pie_pullback_v1.json", FX)
    with pytest.raises(ValidationError):
        engine.run_and_write(df, meta, output_dir="experimental/tests/fixtures/tmp_should_not_exist")
    # Ensure no files written
    import pathlib
    tmp = pathlib.Path("experimental/tests/fixtures/tmp_should_not_exist")
    if tmp.exists():
        # Should not contain equity files
        assert len(list(tmp.glob("*.csv"))) == 0
        assert len(list(tmp.glob("*.json"))) == 0
