"""Strict validation - fail closed.

No silent filling. Engine must raise ValidationError if ok==False.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List
import hashlib

import pandas as pd


@dataclass
class ValidationReport:
    ok: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    data_hash: str = ""
    row_count: int = 0
    symbols: List[str] = field(default_factory=list)


class ValidationError(Exception):
    """Raised by engine when validation fails."""

    def __init__(self, report: ValidationReport):
        self.report = report
        super().__init__(f"Validation failed: {report.errors}")


def _business_days_between(d1: pd.Timestamp, d2: pd.Timestamp) -> int:
    """Number of business days from d1 to d2 inclusive of d2 exclusive of d1.
    If d1 is Fri 2024-01-05 and d2 is Mon 2024-01-08, returns 1.
    """
    # Ensure UTC
    if d1.tzinfo is None:
        d1 = d1.tz_localize("UTC")
    if d2.tzinfo is None:
        d2 = d2.tz_localize("UTC")
    # Use bdate_range
    # Start from next calendar day after d1
    start = (d1 + pd.Timedelta(days=1)).normalize()
    end = d2.normalize()
    if end <= start:
        # Use hourly logic for same day edge
        # Count 0 or 1
        if d2.normalize() == d1.normalize():
            return 0
        # fallback to business range
        rng = pd.bdate_range(start, end, freq="B")
        return len(rng)
    rng = pd.bdate_range(start, end, freq="B")
    return len(rng)


def validate_dataframe(
    df: pd.DataFrame,
    data_hash: str = "",
    allow_gap_larger_than_two_bdays: bool = False,
    as_of: pd.Timestamp | str | None = None,
    stale_threshold_bdays: int = 3,
) -> ValidationReport:
    """
    Strict validation. Fail closed on any of:
      - high < low
      - close <= 0, open <= 0
      - negative volume
      - duplicate date per symbol
      - missing required data
      - gap larger than two business days (unless explicitly allowed)
      - stale last bar based on supplied as_of timestamp
    """
    errors: List[str] = []
    warnings: List[str] = []
    row_count = len(df) if df is not None else 0
    symbols: List[str] = []

    if df is None or df.empty:
        return ValidationReport(
            ok=False,
            errors=["missing required data: dataframe empty"],
            warnings=warnings,
            data_hash=data_hash,
            row_count=row_count,
            symbols=symbols,
        )

    # Check required columns
    required = {"date", "symbol", "open", "high", "low", "close", "volume", "currency", "open_eur", "high_eur", "low_eur", "close_eur"}
    missing_cols = required - set(df.columns)
    if missing_cols:
        errors.append(f"missing required columns: {sorted(missing_cols)}")
        return ValidationReport(ok=False, errors=errors, warnings=warnings, data_hash=data_hash, row_count=row_count, symbols=symbols)

    # Symbols
    try:
        symbols = sorted(df["symbol"].dropna().unique().tolist())
    except Exception:
        symbols = []

    # Missing required data: any NaN in required numeric columns
    for col in ["date", "symbol", "open", "high", "low", "close", "volume", "open_eur", "high_eur", "low_eur", "close_eur"]:
        if df[col].isna().any():
            # For date, also check
            n = int(df[col].isna().sum())
            if col == "date":
                errors.append(f"missing required data: {n} NaT in '{col}'")
            else:
                errors.append(f"missing required data: {n} NaN in '{col}'")

    # high < low (both raw and eur) - use eur for primary check but also raw
    try:
        mask_hl = df["high_eur"] < df["low_eur"]
        if mask_hl.any():
            idx = df.index[mask_hl].tolist()[:3]
            errors.append(f"high < low detected at rows {idx} (high_eur < low_eur)")
        # also raw check
        mask_raw = df["high"] < df["low"]
        if mask_raw.any() and not mask_hl.any():
            idx = df.index[mask_raw].tolist()[:3]
            errors.append(f"high < low detected at rows {idx} (high < low)")
    except Exception as e:
        errors.append(f"high/low check failed: {e}")

    # close <=0, open <=0 (eur columns are post-FX, so check eur)
    try:
        if (df["close_eur"] <= 0).any():
            n = int((df["close_eur"] <= 0).sum())
            errors.append(f"close <= 0 detected: {n} rows (close_eur <=0)")
        if (df["open_eur"] <= 0).any():
            n = int((df["open_eur"] <= 0).sum())
            errors.append(f"open <= 0 detected: {n} rows (open_eur <=0)")
        # also raw for completeness
        if (df["close"] <= 0).any():
            n = int((df["close"] <= 0).sum())
            # avoid duplicate if already eur
            if not (df["close_eur"] <= 0).any():
                errors.append(f"close <= 0 detected: {n} rows (close <=0)")
    except Exception as e:
        errors.append(f"close/open check failed: {e}")

    # negative volume
    try:
        if (df["volume"] < 0).any():
            n = int((df["volume"] < 0).sum())
            errors.append(f"negative volume detected: {n} rows")
        if df["volume"].isna().any():
            # already reported as missing
            pass
    except Exception as e:
        errors.append(f"volume check failed: {e}")

    # duplicate date per symbol
    try:
        dup_mask = df.duplicated(subset=["symbol", "date"], keep=False)
        if dup_mask.any():
            dups = df.loc[dup_mask, ["symbol", "date"]].head(3).to_dict("records")
            errors.append(f"duplicate date per symbol detected: {dups}")
    except Exception as e:
        errors.append(f"duplicate check failed: {e}")

    # gap larger than two business days per symbol
    if not allow_gap_larger_than_two_bdays:
        try:
            for sym in symbols:
                sub = df[df["symbol"] == sym].sort_values("date")
                dates = sub["date"].tolist()
                for i in range(1, len(dates)):
                    d1 = dates[i - 1]
                    d2 = dates[i]
                    # Ensure timestamps
                    if pd.isna(d1) or pd.isna(d2):
                        continue
                    gap_bdays = _business_days_between(d1, d2)
                    # gap_bdays is number of business days from d1 to d2.
                    # Consecutive business days => gap_bdays ==1
                    # Gap of 3 means 2 missing business days? Let's define threshold:
                    # Allow gap_bdays <=3 (which means up to 2 missing business days)
                    # Fail if gap_bdays >3
                    if gap_bdays > 3:
                        errors.append(
                            f"gap larger than two business days for {sym}: {d1.date()} -> {d2.date()} (gap_bdays={gap_bdays})"
                        )
                        break  # one error per symbol is enough
        except Exception as e:
            errors.append(f"gap check failed: {e}")
    else:
        warnings.append("gap check skipped: allow_gap_larger_than_two_bdays=true")

    # stale last bar based on supplied as_of timestamp
    if as_of is not None:
        try:
            as_of_ts = pd.to_datetime(as_of, utc=True)
            for sym in symbols:
                sub = df[df["symbol"] == sym]
                last_date = sub["date"].max()
                if pd.isna(last_date):
                    continue
                gap_bdays = _business_days_between(last_date, as_of_ts)
                # If last bar is more than stale_threshold_bdays business days before as_of, error
                if gap_bdays > stale_threshold_bdays:
                    errors.append(
                        f"stale last bar for {sym}: last={last_date.date()} as_of={as_of_ts.date()} gap_bdays={gap_bdays} threshold={stale_threshold_bdays}"
                    )
        except Exception as e:
            errors.append(f"stale check failed: {e}")

    # Also check currency supported (redundant with io but enforce)
    try:
        supported = {"EUR", "USD", "GBP", "GBX"}
        bad = set(df["currency"].dropna().unique()) - supported
        if bad:
            errors.append(f"unsupported currency: {bad}")
    except Exception:
        pass

    ok = len(errors) == 0
    return ValidationReport(
        ok=ok,
        errors=errors,
        warnings=warnings,
        data_hash=data_hash,
        row_count=row_count,
        symbols=symbols,
    )
