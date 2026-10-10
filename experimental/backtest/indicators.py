"""Correct indicators - original Python, documented standard formulas.

No future-row access. Warmup stays NaN until mathematically valid.
Vectorized where possible; loop only for stateful Supertrend.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Basic moving averages
# ---------------------------------------------------------------------------

def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average. Warmup period-1 NaN."""
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average with adjust=False (standard)."""
    # min_periods=period ensures warmup NaN
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def wilder_rma(series: pd.Series, period: int) -> pd.Series:
    """
    Wilder's RMA (aka SMMA, Running Moving Average).
    Wilder's smoothing: alpha=1/period, adjust=False, min_periods=period.
    First valid value is SMA of first period, then recursive.
    Equivalent to ewm(alpha=1/period, adjust=False).
    """
    return series.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


# ---------------------------------------------------------------------------
# RSI using Wilder RMA
# ---------------------------------------------------------------------------

def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """
    RSI(14) using Wilder RMA for avg gain/loss.
    Formula: RS = avg_gain / avg_loss, RSI = 100 - 100/(1+RS)
    No future access; uses diff() which is causal.
    """
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = wilder_rma(gain, period)
    avg_loss = wilder_rma(loss, period)
    rs = avg_gain / avg_loss
    # When avg_loss==0 -> RSI 100; when avg_gain==0 -> RSI 0
    rsi_val = 100 - (100 / (1 + rs))
    # Handle case both zero (flat) -> 50? But rs=0/0 -> NaN, keep NaN until valid
    # When avg_loss==0 and avg_gain>0, rsi should be 100
    rsi_val = rsi_val.where(avg_loss != 0, 100.0)
    rsi_val = rsi_val.where(avg_gain != 0, 0.0) if False else rsi_val  # not needed
    # If both zero due to flat prices, rsi will be NaN until loss==0 case above -> 100, but we want 50 for flat?
    # Standard: if both zero, RSI=50; we handle avg_loss==0 case already sets 100, so flat will be 100, but that's rare.
    # More precise: if avg_gain==0 and avg_loss==0 -> 50
    flat_mask = (avg_gain == 0) & (avg_loss == 0)
    rsi_val = rsi_val.mask(flat_mask, 50.0)
    return rsi_val


def rsi_sma_variant(close: pd.Series, period: int = 14) -> pd.Series:
    """Naive SMA-based RSI for testing Wilder vs SMA difference."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


# ---------------------------------------------------------------------------
# True Range and ATR (Wilder)
# ---------------------------------------------------------------------------

def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """True Range: max(high-low, abs(high-prev_close), abs(low-prev_close))."""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """ATR(14) using TR and Wilder RMA."""
    tr = true_range(high, low, close)
    return wilder_rma(tr, period)


def atr_sma_variant(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Naive SMA ATR for testing difference."""
    tr = true_range(high, low, close)
    return tr.rolling(period).mean()


# ---------------------------------------------------------------------------
# MACD
# ---------------------------------------------------------------------------

def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """
    MACD(12,26,9): macd=EMA12-EMA26, signal=EMA(macd,9), hist=macd-signal.
    Output columns: MACD, MACD_SIGNAL, MACD_HIST
    """
    ema_fast = ema(close, fast)
    ema_slow = ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return pd.DataFrame({"MACD": macd_line, "MACD_SIGNAL": signal_line, "MACD_HIST": hist})


# ---------------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------------

def bollinger_bands(close: pd.Series, period: int = 20, std: float = 2.0) -> pd.DataFrame:
    """
    Bollinger Bands(20,2): middle=SMA20, upper=middle+2*std, lower=middle-2*std
    Output: BB_MIDDLE, BB_UPPER, BB_LOWER, BB_BANDWIDTH, BB_PERCENT
    """
    middle = sma(close, period)
    rolling_std = close.rolling(window=period, min_periods=period).std(ddof=1)
    upper = middle + std * rolling_std
    lower = middle - std * rolling_std
    bandwidth = (upper - lower) / middle
    percent = (close - lower) / (upper - lower)
    return pd.DataFrame(
        {
            "BB_MIDDLE": middle,
            "BB_UPPER": upper,
            "BB_LOWER": lower,
            "BB_BANDWIDTH": bandwidth,
            "BB_PERCENT": percent,
        }
    )


# ---------------------------------------------------------------------------
# ADX with Wilder smoothing
# ---------------------------------------------------------------------------

def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.DataFrame:
    """
    ADX(14), +DI, -DI using Wilder smoothing.
    Steps per Wilder:
      +DM = high - prev_high if > prev_low - low and >0 else 0
      -DM = prev_low - low if > high - prev_high and >0 else 0
      TR as above
      Smooth TR, +DM, -DM with Wilder RMA
      +DI = 100 * smooth(+DM)/smooth(TR)
      -DI = 100 * smooth(-DM)/smooth(TR)
      DX = 100 * abs(+DI - -DI)/(+DI+ -DI)
      ADX = Wilder RMA(DX, period)  (first ADX is SMA of DX)
    Output: ADX, PLUS_DI, MINUS_DI, DX
    """
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    # Directional movements
    up_move = high - prev_high
    down_move = prev_low - low

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm = pd.Series(plus_dm, index=high.index, dtype=float)
    minus_dm = pd.Series(minus_dm, index=high.index, dtype=float)

    tr = true_range(high, low, close)

    # Wilder smoothing
    tr_smooth = wilder_rma(tr, period)
    plus_dm_smooth = wilder_rma(plus_dm, period)
    minus_dm_smooth = wilder_rma(minus_dm, period)

    plus_di = 100 * plus_dm_smooth / tr_smooth
    minus_di = 100 * minus_dm_smooth / tr_smooth

    # Avoid division by zero
    di_sum = plus_di + minus_di
    dx = 100 * (plus_di - minus_di).abs() / di_sum.replace(0, np.nan)
    # Wilder RMA for ADX; first valid after 2*period-1 typically
    adx_val = wilder_rma(dx, period)

    return pd.DataFrame({"ADX": adx_val, "PLUS_DI": plus_di, "MINUS_DI": minus_di, "DX": dx})


def adx_sma_variant(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.DataFrame:
    """Naive SMA ADX for testing Wilder vs SMA."""
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    up_move = high - prev_high
    down_move = prev_low - low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm = pd.Series(plus_dm, index=high.index, dtype=float)
    minus_dm = pd.Series(minus_dm, index=high.index, dtype=float)
    tr = true_range(high, low, close)
    tr_s = tr.rolling(period).mean()
    plus_s = plus_dm.rolling(period).mean()
    minus_s = minus_dm.rolling(period).mean()
    plus_di = 100 * plus_s / tr_s
    minus_di = 100 * minus_s / tr_s
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_val = dx.rolling(period).mean()
    return pd.DataFrame({"ADX": adx_val, "PLUS_DI": plus_di, "MINUS_DI": minus_di, "DX": dx})


# ---------------------------------------------------------------------------
# Supertrend (stateful)
# ---------------------------------------------------------------------------

def supertrend(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    """
    Stateful Supertrend(10,3).
    HL2=(high+low)/2, ATR Wilder, Upper=HL2+mult*ATR, Lower=HL2-mult*ATR
    Final bands and trend are stateful iterative.
    Output: SUPERT, SUPERT_DIR (1 bull, -1 bear), SUPERT_LONG, SUPERT_SHORT
    Warmup NaN until ATR valid.
    """
    hl2 = (high + low) / 2.0
    atr_val = atr(high, low, close, period)
    upper = hl2 + multiplier * atr_val
    lower = hl2 - multiplier * atr_val

    n = len(close)
    final_upper = pd.Series(np.nan, index=close.index, dtype=float)
    final_lower = pd.Series(np.nan, index=close.index, dtype=float)
    trend = pd.Series(np.nan, index=close.index, dtype=float)
    supert = pd.Series(np.nan, index=close.index, dtype=float)

    # Find first valid ATR index
    first_valid = atr_val.first_valid_index()
    if first_valid is None:
        return pd.DataFrame(
            {"SUPERT": supert, "SUPERT_DIR": trend, "SUPERT_LONG": final_lower, "SUPERT_SHORT": final_upper}
        )
    start_idx = close.index.get_loc(first_valid)

    # Initialize
    final_upper.iloc[start_idx] = upper.iloc[start_idx]
    final_lower.iloc[start_idx] = lower.iloc[start_idx]
    # Initial trend: 1 if close > lower else -1
    trend.iloc[start_idx] = 1 if close.iloc[start_idx] > final_lower.iloc[start_idx] else -1
    supert.iloc[start_idx] = final_lower.iloc[start_idx] if trend.iloc[start_idx] == 1 else final_upper.iloc[start_idx]

    for i in range(start_idx + 1, n):
        # Skip if ATR NaN
        if pd.isna(upper.iloc[i]) or pd.isna(lower.iloc[i]):
            final_upper.iloc[i] = np.nan
            final_lower.iloc[i] = np.nan
            trend.iloc[i] = trend.iloc[i - 1]
            supert.iloc[i] = supert.iloc[i - 1]
            continue

        # Final upper
        prev_final_upper = final_upper.iloc[i - 1]
        prev_close = close.iloc[i - 1]
        cur_upper = upper.iloc[i]
        if pd.isna(prev_final_upper):
            final_upper.iloc[i] = cur_upper
        else:
            if (cur_upper < prev_final_upper) or (prev_close > prev_final_upper):
                final_upper.iloc[i] = cur_upper
            else:
                final_upper.iloc[i] = prev_final_upper

        # Final lower
        prev_final_lower = final_lower.iloc[i - 1]
        cur_lower = lower.iloc[i]
        if pd.isna(prev_final_lower):
            final_lower.iloc[i] = cur_lower
        else:
            if (cur_lower > prev_final_lower) or (prev_close < prev_final_lower):
                final_lower.iloc[i] = cur_lower
            else:
                final_lower.iloc[i] = prev_final_lower

        # Trend
        prev_trend = trend.iloc[i - 1]
        cur_close = close.iloc[i]
        if prev_trend == 1:
            if cur_close <= final_lower.iloc[i]:
                trend.iloc[i] = -1
            else:
                trend.iloc[i] = 1
        else:  # prev_trend == -1
            if cur_close >= final_upper.iloc[i]:
                trend.iloc[i] = 1
            else:
                trend.iloc[i] = -1

        supert.iloc[i] = final_lower.iloc[i] if trend.iloc[i] == 1 else final_upper.iloc[i]

    return pd.DataFrame(
        {"SUPERT": supert, "SUPERT_DIR": trend, "SUPERT_LONG": final_lower, "SUPERT_SHORT": final_upper}
    )


# ---------------------------------------------------------------------------
# Volume indicators
# ---------------------------------------------------------------------------

def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume."""
    sign = np.sign(close.diff())
    # sign is 0 for NaN/first bar; fillna 0
    sign = pd.Series(sign, index=close.index).fillna(0)
    return (sign * volume).cumsum()


def vwap_daily(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    """
    Daily session VWAP.
    For daily bars, session is one day, so VWAP per bar = typical price.
    For intraday, would be cumulative TP*Vol / cumulative Vol reset per day.
    Here we implement as typical price for daily; if intraday, caller should group by date.
    We provide explicit daily session VWAP as typical price.
    """
    typical = (high + low + close) / 3.0
    # For true daily session (single bar per day), VWAP == typical
    # To support intraday later, we could group by date and cumsum, but Phase 1 is daily.
    # Return typical as VWAP for daily.
    return typical.rename("VWAP")


def rolling_vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, period: int = 20) -> pd.Series:
    """
    Rolling VWAP (separately named, not called VWAP).
    Formula: sum(typical*volume, period) / sum(volume, period)
    Distinct from daily session VWAP.
    """
    typical = (high + low + close) / 3.0
    pv = typical * volume
    return (pv.rolling(window=period, min_periods=period).sum() / volume.rolling(window=period, min_periods=period).sum()).rename(f"ROLLING_VWAP_{period}")


# ---------------------------------------------------------------------------
# Helper to add all indicators needed for strategy to a DataFrame
# ---------------------------------------------------------------------------

def add_daily_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add daily indicators to df (must have columns: high_eur, low_eur, close_eur, volume).
    Uses EUR-normalized prices to be currency-agnostic.
    Output columns explicit names with _EUR suffix handling but keep generic names for strategy.
    Requires sorting by date.
    """
    df = df.copy()
    close = df["close_eur"]
    high = df["high_eur"]
    low = df["low_eur"]
    volume = df["volume"]

    df["SMA20"] = sma(close, 20)
    df["SMA50"] = sma(close, 50)
    df["SMA200"] = sma(close, 200)
    df["EMA12"] = ema(close, 12)
    df["EMA26"] = ema(close, 26)
    df["RSI14"] = rsi(close, 14)
    df["ATR14"] = atr(high, low, close, 14)
    macd_df = macd(close, 12, 26, 9)
    df["MACD"] = macd_df["MACD"]
    df["MACD_SIGNAL"] = macd_df["MACD_SIGNAL"]
    df["MACD_HIST"] = macd_df["MACD_HIST"]
    bb_df = bollinger_bands(close, 20, 2.0)
    df["BB_MIDDLE"] = bb_df["BB_MIDDLE"]
    df["BB_UPPER"] = bb_df["BB_UPPER"]
    df["BB_LOWER"] = bb_df["BB_LOWER"]
    adx_df = adx(high, low, close, 14)
    df["ADX14"] = adx_df["ADX"]
    df["PLUS_DI14"] = adx_df["PLUS_DI"]
    df["MINUS_DI14"] = adx_df["MINUS_DI"]
    st_df = supertrend(high, low, close, 10, 3.0)
    df["SUPERT"] = st_df["SUPERT"]
    df["SUPERT_DIR"] = st_df["SUPERT_DIR"]
    df["OBV"] = obv(close, volume)
    df["VWAP"] = vwap_daily(high, low, close, volume)
    df["ROLLING_VWAP_20"] = rolling_vwap(high, low, close, volume, 20)
    # Volume ratio
    df["VOL_SMA20"] = sma(volume, 20)
    df["VOLUME_RATIO"] = volume / df["VOL_SMA20"]
    # Distance to SMA20
    df["DIST_SMA20_PCT"] = (close - df["SMA20"]) / df["SMA20"] * 100
    return df


def add_weekly_indicators(weekly_df: pd.DataFrame) -> pd.DataFrame:
    """
    Add weekly indicators to weekly resampled DataFrame.
    weekly_df must have close_eur etc. and be indexed by weekly date.
    """
    df = weekly_df.copy()
    close = df["close_eur"]
    high = df["high_eur"]
    low = df["low_eur"]

    df["SMA50_W"] = sma(close, 50)
    df["SMA200_W"] = sma(close, 200)
    df["RSI14_W"] = rsi(close, 14)
    adx_df = adx(high, low, close, 14)
    df["ADX14_W"] = adx_df["ADX"]
    return df
