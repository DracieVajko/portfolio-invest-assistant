"""Local data loading - CSV/parquet OHLCV with explicit EUR normalization.

No yfinance, no live downloads, no broker calls, no secrets.
Uses dated local FX fixture; never hidden static conversion.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Tuple

import pandas as pd


REQUIRED_COLUMNS = {"date", "symbol", "open", "high", "low", "close", "volume", "currency"}

SUPPORTED_CURRENCIES = {"EUR", "USD", "GBP", "GBX"}


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_file(path: Path) -> str:
    return _hash_bytes(path.read_bytes())


def _hash_json(path: Path) -> str:
    return _hash_bytes(path.read_bytes())


def load_fx_config(fx_config_path: str | Path) -> Tuple[dict, str]:
    """Load dated FX fixture and return (config_dict, sha256_hex)."""
    p = Path(fx_config_path)
    if not p.is_file():
        raise FileNotFoundError(f"FX config not found: {p}")
    raw = p.read_bytes()
    fx_hash = _hash_bytes(raw)
    cfg = json.loads(raw.decode("utf-8"))
    if "rates" not in cfg:
        raise ValueError("FX config missing 'rates' key")
    if "EUR" not in cfg["rates"]:
        raise ValueError("FX config rates must contain EUR")
    # gbx_divisor must be 100 exactly
    if cfg.get("gbx_divisor", 100) != 100:
        raise ValueError("gbx_divisor must be 100")
    return cfg, fx_hash


def _normalize_currency_to_eur(
    price: float, currency: str, fx_rates: dict, gbx_divisor: int = 100
) -> float:
    """Convert price to EUR using explicit FX rates. GBX divided by 100 exactly once."""
    ccy = currency.strip().upper()
    if ccy == "GBX":
        # GBX -> GBP -> EUR
        gbp = price / gbx_divisor
        rate = fx_rates.get("GBP")
        if rate is None:
            raise ValueError("Missing GBP rate for GBX conversion")
        return gbp * rate
    if ccy == "GBP":
        rate = fx_rates.get("GBP")
        if rate is None:
            raise ValueError("Missing GBP rate")
        return price * rate
    if ccy == "USD":
        rate = fx_rates.get("USD")
        if rate is None:
            raise ValueError("Missing USD rate")
        return price * rate
    if ccy == "EUR":
        return price
    raise ValueError(f"Unsupported currency: {currency}")


def load_ohlcv(
    csv_path: str | Path,
    fx_config_path: str | Path,
    parquet_path: str | Path | None = None,
) -> Tuple[pd.DataFrame, dict]:
    """
    Load OHLCV CSV (or parquet if provided) and return normalized DataFrame with EUR columns.

    Returns:
        (df, meta) where df has columns:
            date (UTC Timestamp), symbol, open, high, low, close, volume, currency,
            earnings_date (UTC Timestamp or NaT),
            open_eur, high_eur, low_eur, close_eur,
            data_hash, fx_hash (in meta)
        meta: {data_hash, fx_hash, fx_as_of, row_count, symbols}
    """
    # Use parquet if explicitly passed, otherwise csv_path
    if parquet_path is not None:
        p = Path(parquet_path)
        if not p.is_file():
            raise FileNotFoundError(f"Parquet not found: {p}")
        data_hash = _hash_file(p)
        df_raw = pd.read_parquet(p)
    else:
        p = Path(csv_path)
        if not p.is_file():
            raise FileNotFoundError(f"CSV not found: {p}")
        data_hash = _hash_file(p)
        # Read as strings first to preserve validation
        df_raw = pd.read_csv(p, dtype=str)

    # Normalize column names to lower
    df_raw.columns = [c.strip().lower() for c in df_raw.columns]

    missing = REQUIRED_COLUMNS - set(df_raw.columns)
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    # earnings_date is optional but if present, parse it; if missing, create NaT
    has_earnings = "earnings_date" in df_raw.columns

    # Parse dates to UTC
    # date column required
    try:
        df_raw["date"] = pd.to_datetime(df_raw["date"], utc=True, errors="raise")
    except Exception as e:
        raise ValueError(f"Invalid date column (must be ISO UTC): {e}") from e

    if has_earnings:
        # empty strings -> NaT
        df_raw["earnings_date"] = df_raw["earnings_date"].replace("", pd.NA)
        df_raw["earnings_date"] = pd.to_datetime(df_raw["earnings_date"], utc=True, errors="coerce")
    else:
        df_raw["earnings_date"] = pd.NaT

    # Normalize symbol, currency
    df_raw["symbol"] = df_raw["symbol"].astype(str).str.strip().str.upper()
    df_raw["currency"] = df_raw["currency"].astype(str).str.strip().str.upper()

    # Validate supported currencies early (also validated later)
    unsupported = set(df_raw["currency"].unique()) - SUPPORTED_CURRENCIES
    if unsupported:
        raise ValueError(f"Unsupported currencies found: {unsupported}. Supported: {SUPPORTED_CURRENCIES}")

    # Parse numeric columns - do not fill missing silently; keep NaN for validation
    for col in ["open", "high", "low", "close", "volume"]:
        df_raw[col] = pd.to_numeric(df_raw[col], errors="coerce")

    # Sort by symbol and date (required)
    df_raw = df_raw.sort_values(["symbol", "date"]).reset_index(drop=True)

    # Load FX
    fx_cfg, fx_hash = load_fx_config(fx_config_path)
    fx_rates = fx_cfg["rates"]
    gbx_divisor = fx_cfg.get("gbx_divisor", 100)
    fx_as_of = fx_cfg.get("as_of", "")

    # Create EUR normalized columns - vectorized per currency group
    # We do exact GBX/100 once before GBP->EUR
    df_raw["open_eur"] = pd.NA
    df_raw["high_eur"] = pd.NA
    df_raw["low_eur"] = pd.NA
    df_raw["close_eur"] = pd.NA

    for ccy in SUPPORTED_CURRENCIES:
        mask = df_raw["currency"] == ccy
        if not mask.any():
            continue
        if ccy == "GBX":
            rate_gbp = fx_rates.get("GBP")
            if rate_gbp is None:
                raise ValueError("FX rates missing GBP for GBX")
            for src, dst in [("open", "open_eur"), ("high", "high_eur"), ("low", "low_eur"), ("close", "close_eur")]:
                df_raw.loc[mask, dst] = df_raw.loc[mask, src] / gbx_divisor * rate_gbp
        elif ccy == "GBP":
            rate = fx_rates["GBP"]
            for src, dst in [("open", "open_eur"), ("high", "high_eur"), ("low", "low_eur"), ("close", "close_eur")]:
                df_raw.loc[mask, dst] = df_raw.loc[mask, src] * rate
        elif ccy == "USD":
            rate = fx_rates["USD"]
            for src, dst in [("open", "open_eur"), ("high", "high_eur"), ("low", "low_eur"), ("close", "close_eur")]:
                df_raw.loc[mask, dst] = df_raw.loc[mask, src] * rate
        elif ccy == "EUR":
            for src, dst in [("open", "open_eur"), ("high", "high_eur"), ("low", "low_eur"), ("close", "close_eur")]:
                df_raw.loc[mask, dst] = df_raw.loc[mask, src]

    # Ensure EUR columns are float
    for col in ["open_eur", "high_eur", "low_eur", "close_eur"]:
        df_raw[col] = pd.to_numeric(df_raw[col], errors="coerce")

    # Keep only relevant columns in order
    out_cols = ["date", "symbol", "open", "high", "low", "close", "volume", "currency", "earnings_date", "open_eur", "high_eur", "low_eur", "close_eur"]
    df_out = df_raw[out_cols].copy()

    # Do NOT reject duplicates here - let validate.py fail closed and report.
    # Do NOT fill missing values.

    meta = {
        "data_hash": data_hash,
        "fx_hash": fx_hash,
        "fx_as_of": fx_as_of,
        "fx_config_path": str(Path(fx_config_path).resolve()),
        "data_path": str(Path(csv_path if parquet_path is None else parquet_path).resolve()),
        "row_count": len(df_out),
        "symbols": sorted(df_out["symbol"].unique().tolist()),
    }
    return df_out, meta


def load_ohlcv_from_dataframe(
    df_input: pd.DataFrame,
    fx_config_path: str | Path,
) -> Tuple[pd.DataFrame, dict]:
    """Helper for tests: normalize a DataFrame already in memory using FX fixture."""
    # Create temp CSV bytes hash for data_hash determinism
    csv_bytes = df_input.to_csv(index=False).encode("utf-8")
    data_hash = _hash_bytes(csv_bytes)

    df_raw = df_input.copy()
    df_raw.columns = [c.strip().lower() for c in df_raw.columns]
    # similar normalization as above but without file IO
    missing = REQUIRED_COLUMNS - set(df_raw.columns)
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    has_earnings = "earnings_date" in df_raw.columns
    df_raw["date"] = pd.to_datetime(df_raw["date"], utc=True, errors="raise")
    if has_earnings:
        df_raw["earnings_date"] = df_raw["earnings_date"].replace("", pd.NA)
        df_raw["earnings_date"] = pd.to_datetime(df_raw["earnings_date"], utc=True, errors="coerce")
    else:
        df_raw["earnings_date"] = pd.NaT
    df_raw["symbol"] = df_raw["symbol"].astype(str).str.strip().str.upper()
    df_raw["currency"] = df_raw["currency"].astype(str).str.strip().str.upper()
    for col in ["open", "high", "low", "close", "volume"]:
        df_raw[col] = pd.to_numeric(df_raw[col], errors="coerce")
    df_raw = df_raw.sort_values(["symbol", "date"]).reset_index(drop=True)
    fx_cfg, fx_hash = load_fx_config(fx_config_path)
    fx_rates = fx_cfg["rates"]
    gbx_divisor = fx_cfg.get("gbx_divisor", 100)
    fx_as_of = fx_cfg.get("as_of", "")
    df_raw["open_eur"] = pd.NA
    df_raw["high_eur"] = pd.NA
    df_raw["low_eur"] = pd.NA
    df_raw["close_eur"] = pd.NA
    for ccy in SUPPORTED_CURRENCIES:
        mask = df_raw["currency"] == ccy
        if not mask.any():
            continue
        if ccy == "GBX":
            rate_gbp = fx_rates.get("GBP")
            for src, dst in [("open", "open_eur"), ("high", "high_eur"), ("low", "low_eur"), ("close", "close_eur")]:
                df_raw.loc[mask, dst] = df_raw.loc[mask, src] / gbx_divisor * rate_gbp
        elif ccy == "GBP":
            rate = fx_rates["GBP"]
            for src, dst in [("open", "open_eur"), ("high", "high_eur"), ("low", "low_eur"), ("close", "close_eur")]:
                df_raw.loc[mask, dst] = df_raw.loc[mask, src] * rate
        elif ccy == "USD":
            rate = fx_rates["USD"]
            for src, dst in [("open", "open_eur"), ("high", "high_eur"), ("low", "low_eur"), ("close", "close_eur")]:
                df_raw.loc[mask, dst] = df_raw.loc[mask, src] * rate
        elif ccy == "EUR":
            for src, dst in [("open", "open_eur"), ("high", "high_eur"), ("low", "low_eur"), ("close", "close_eur")]:
                df_raw.loc[mask, dst] = df_raw.loc[mask, src]
    for col in ["open_eur", "high_eur", "low_eur", "close_eur"]:
        df_raw[col] = pd.to_numeric(df_raw[col], errors="coerce")
    out_cols = ["date", "symbol", "open", "high", "low", "close", "volume", "currency", "earnings_date", "open_eur", "high_eur", "low_eur", "close_eur"]
    df_out = df_raw[out_cols].copy()
    meta = {
        "data_hash": data_hash,
        "fx_hash": fx_hash,
        "fx_as_of": fx_as_of,
        "fx_config_path": str(Path(fx_config_path).resolve()),
        "row_count": len(df_out),
        "symbols": sorted(df_out["symbol"].unique().tolist()),
    }
    return df_out, meta
