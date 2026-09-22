"""Currency normalization at input/output boundaries."""
import pandas as pd
from pathlib import Path

from experimental.backtest.io import load_ohlcv_from_dataframe, load_ohlcv

FX = Path("experimental/backtest/config/fx_rates.json")


def test_gbx_250_becomes_gbp_2_5_before_eur():
    # GBX 250 -> GBP 2.50 -> EUR with rate 1.183 => 2.9575
    rows = [
        {"date": "2024-01-02T00:00:00Z", "symbol": "EGTL", "open": 250, "high": 260, "low": 240, "close": 250, "volume": 1_000_000, "currency": "GBX", "earnings_date": ""},
    ]
    df_raw = pd.DataFrame(rows)
    df, meta = load_ohlcv_from_dataframe(df_raw, FX)
    # Check EUR conversion: 250 GBX =2.50 GBP *1.183
    expected = 2.50 * 1.183
    assert abs(df.loc[0, "close_eur"] - expected) < 1e-9
    assert abs(df.loc[0, "open_eur"] - expected) < 1e-9
    # Ensure not double divided
    assert df.loc[0, "close_eur"] > 2.0  # not 0.025
    # Also test that GBX 100 -> 1 GBP -> 1.183 EUR
    rows2 = [{"date": "2024-01-02T00:00:00Z", "symbol": "X", "open": 100, "high": 100, "low": 100, "close": 100, "volume": 1_000_000, "currency": "GBX", "earnings_date": ""}]
    df2, _ = load_ohlcv_from_dataframe(pd.DataFrame(rows2), FX)
    assert abs(df2.loc[0, "close_eur"] - 1.183) < 1e-9


def test_usd_and_eur_handling():
    rows = [
        {"date": "2024-01-02T00:00:00Z", "symbol": "AAPL", "open": 150, "high": 151, "low": 149, "close": 150, "volume": 1_000_000, "currency": "USD", "earnings_date": ""},
        {"date": "2024-01-02T00:00:00Z", "symbol": "IBE", "open": 12, "high": 12.5, "low": 11.5, "close": 12, "volume": 1_000_000, "currency": "EUR", "earnings_date": ""},
    ]
    df_raw = pd.DataFrame(rows)
    df, meta = load_ohlcv_from_dataframe(df_raw, FX)
    aapl = df[df["symbol"] == "AAPL"].iloc[0]
    ibe = df[df["symbol"] == "IBE"].iloc[0]
    assert abs(aapl["close_eur"] - 150 * 0.922) < 1e-9
    assert abs(ibe["close_eur"] - 12) < 1e-9


def test_gbp_direct():
    rows = [{"date": "2024-01-02T00:00:00Z", "symbol": "GBPSTOCK", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1_000_000, "currency": "GBP", "earnings_date": ""}]
    df, _ = load_ohlcv_from_dataframe(pd.DataFrame(rows), FX)
    assert abs(df.loc[0, "close_eur"] - 10 * 1.183) < 1e-9


def test_gbx_not_double_divided_when_already_gbp():
    # Ensure GBP not divided
    rows = [{"date": "2024-01-02T00:00:00Z", "symbol": "Y", "open": 250, "high": 250, "low": 250, "close": 250, "volume": 1_000_000, "currency": "GBP", "earnings_date": ""}]
    df, _ = load_ohlcv_from_dataframe(pd.DataFrame(rows), FX)
    # GBP 250 -> EUR 295.75, not 2.95
    assert abs(df.loc[0, "close_eur"] - 250 * 1.183) < 1e-6


def test_csv_file_hash_and_fx_hash_persisted():
    # Use load_ohlcv file path to ensure hashes
    import tempfile, os, json
    rows = [{"date": "2024-01-02T00:00:00Z", "symbol": "HASH", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1_000_000, "currency": "EUR", "earnings_date": ""}]
    df_raw = pd.DataFrame(rows)
    with tempfile.TemporaryDirectory() as td:
        csv_path = Path(td) / "test.csv"
        df_raw.to_csv(csv_path, index=False)
        df, meta = load_ohlcv(csv_path, FX)
        assert "data_hash" in meta
        assert "fx_hash" in meta
        assert len(meta["data_hash"]) == 64
        assert len(meta["fx_hash"]) == 64
        assert meta["fx_as_of"] == "2026-08-27"
        # Data hash changes if file changes
        df_raw2 = df_raw.copy()
        df_raw2.loc[0, "close"] = 101
        csv_path2 = Path(td) / "test2.csv"
        df_raw2.to_csv(csv_path2, index=False)
        _, meta2 = load_ohlcv(csv_path2, FX)
        assert meta2["data_hash"] != meta["data_hash"]


def test_mixed_currencies_in_same_file():
    rows = [
        {"date": "2024-01-02T00:00:00Z", "symbol": "A", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1_000_000, "currency": "EUR", "earnings_date": ""},
        {"date": "2024-01-02T00:00:00Z", "symbol": "B", "open": 200, "high": 201, "low": 199, "close": 200, "volume": 1_000_000, "currency": "GBX", "earnings_date": ""},
        {"date": "2024-01-02T00:00:00Z", "symbol": "C", "open": 300, "high": 301, "low": 299, "close": 300, "volume": 1_000_000, "currency": "USD", "earnings_date": ""},
    ]
    df, _ = load_ohlcv_from_dataframe(pd.DataFrame(rows), FX)
    a = df[df["symbol"] == "A"].iloc[0]
    b = df[df["symbol"] == "B"].iloc[0]
    c = df[df["symbol"] == "C"].iloc[0]
    assert abs(a["close_eur"] - 100) < 1e-9
    assert abs(b["close_eur"] - 2.00 * 1.183) < 1e-9
    assert abs(c["close_eur"] - 300 * 0.922) < 1e-9
