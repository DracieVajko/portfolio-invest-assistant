"""Canonical manual technical-indicator engine (Phase 3 policy A).

Production indicator policy: this manual engine is CANONICAL and runs on every
PC regardless of optional installs. The pandas-ta path in
``technical_analysis.py`` is validation/reference only (explicit opt-in).

Frozen formula policy (manual-v1 — do not change without a parity-spec update
plus cross-PC byte-identical verification):
- EMA/MACD use ``ewm(adjust=False)`` (NOT the pandas default ``adjust=True``).
- RSI is the SMA-rolling variant (NOT Wilder RMA); ``loss == 0 -> NaN``.
- ATR is ``SMA(TR, 14)`` (NOT Wilder); ADX is the simplified SMA variant.
- Supertrend is the static ``HL2 ± 3.0 * ATR`` approximation (NOT stateful).
- VWAP is the rolling-20 ``sum(TP*V)/sum(V)`` approximation (NOT session VWAP).
- Bollinger ``std`` uses pandas default ``ddof=1``; VWAP window reuses bb_period.

The experimental ``experimental/backtest/indicators.py`` (correct Wilder RMA /
stateful Supertrend / strict warmup) is the parity REFERENCE: production and
reference intentionally diverge (see tests/test_phase3_indicators.py tolerances).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

ENGINE_VERSION = "manual-v1"


@dataclass
class IndicatorConfig:
    """Configuration for technical indicators (frozen defaults)."""

    # Trend
    sma_periods: list[int] = None
    ema_periods: list[int] = None
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    adx_period: int = 14
    supertrend_period: int = 10
    supertrend_multiplier: float = 3.0

    # Momentum
    rsi_periods: list[int] = None
    stoch_k: int = 14
    stoch_d: int = 3
    cci_period: int = 20
    willr_period: int = 14

    # Volatility
    bb_period: int = 20
    bb_std: float = 2.0
    kc_period: int = 20
    kc_scalar: float = 1.5
    atr_period: int = 14
    donchian_period: int = 20

    # Volume
    obv: bool = True
    vwap: bool = True
    mfi_period: int = 14
    cmf_period: int = 20

    # Candlestick patterns (requires TA-Lib; reference path only)
    candlestick_patterns: list[str] = None

    def __post_init__(self):
        if self.sma_periods is None:
            self.sma_periods = [20, 50, 100, 200]
        if self.ema_periods is None:
            self.ema_periods = [9, 12, 21, 26, 50]
        if self.rsi_periods is None:
            self.rsi_periods = [7, 14]
        if self.candlestick_patterns is None:
            self.candlestick_patterns = [
                "doji", "hammer", "hanging_man", "engulfing", "harami",
                "morning_star", "evening_star", "three_white_soldiers",
                "three_black_crows", "piercing", "dark_cloud_cover",
                "shooting_star", "inverted_hammer", "marubozu",
            ]


# Columns produced by apply_manual_indicators (fixed contract for parity tests).
MANUAL_COLUMNS = (
    "SMA_20", "SMA_50", "SMA_100", "SMA_200",
    "EMA_9", "EMA_12", "EMA_21", "EMA_26", "EMA_50",
    "MACD", "MACD_SIGNAL", "MACD_HIST",
    "RSI_7", "RSI_14",
    "BB_MIDDLE", "BB_UPPER", "BB_LOWER", "BB_BANDWIDTH", "BB_PERCENT",
    "ATR_14", "ADX", "DMP", "DMN",
    "STOCH_K", "STOCH_D",
    "OBV", "VOL_SMA_20", "VOLUME_RATIO", "VWAP",
    "CCI_20", "WILLR_14",
    "DC_UPPER", "DC_LOWER", "DC_MIDDLE",
    "MFI_14", "CMF_20",
    "SUPERT_LONG", "SUPERT_SHORT", "SUPERT", "SUPERT_DIR",
)


def apply_manual_indicators(df: pd.DataFrame, config: IndicatorConfig | None = None) -> pd.DataFrame:
    """Apply the frozen manual indicator set. Pure function (no I/O, no network)."""
    config = config or IndicatorConfig()
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # SMAs
    for period in config.sma_periods:
        df[f"SMA_{period}"] = close.rolling(period).mean()

    # EMAs
    for period in config.ema_periods:
        df[f"EMA_{period}"] = close.ewm(span=period, adjust=False).mean()

    # MACD (manual)
    ema_fast = close.ewm(span=config.macd_fast, adjust=False).mean()
    ema_slow = close.ewm(span=config.macd_slow, adjust=False).mean()
    df["MACD"] = ema_fast - ema_slow
    df["MACD_SIGNAL"] = df["MACD"].ewm(span=config.macd_signal, adjust=False).mean()
    df["MACD_HIST"] = df["MACD"] - df["MACD_SIGNAL"]

    # RSI (manual, SMA variant)
    for period in config.rsi_periods:
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(period).mean()
        loss = -delta.where(delta < 0, 0).rolling(period).mean()
        rs = gain / loss.replace(0, np.nan)
        df[f"RSI_{period}"] = 100 - (100 / (1 + rs))

    # Bollinger Bands
    sma_bb = close.rolling(config.bb_period).mean()
    std_bb = close.rolling(config.bb_period).std()
    df["BB_MIDDLE"] = sma_bb
    df["BB_UPPER"] = sma_bb + config.bb_std * std_bb
    df["BB_LOWER"] = sma_bb - config.bb_std * std_bb
    df["BB_BANDWIDTH"] = (df["BB_UPPER"] - df["BB_LOWER"]) / df["BB_MIDDLE"]
    df["BB_PERCENT"] = (close - df["BB_LOWER"]) / (df["BB_UPPER"] - df["BB_LOWER"])

    # ATR (manual, SMA variant)
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df[f"ATR_{config.atr_period}"] = tr.rolling(config.atr_period).mean()

    # ADX (simplified manual)
    plus_dm = high.diff()
    minus_dm = low.diff()
    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm > 0] = 0
    minus_dm = minus_dm.abs()
    tr_smooth = tr.rolling(config.adx_period).mean()
    plus_di = 100 * (plus_dm.rolling(config.adx_period).mean() / tr_smooth)
    minus_di = 100 * (minus_dm.rolling(config.adx_period).mean() / tr_smooth)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df["ADX"] = dx.rolling(config.adx_period).mean()
    df["DMP"] = plus_di
    df["DMN"] = minus_di

    # Stochastic
    lowest_low = low.rolling(config.stoch_k).min()
    highest_high = high.rolling(config.stoch_k).max()
    df["STOCH_K"] = 100 * (close - lowest_low) / (highest_high - lowest_low).replace(0, np.nan)
    df["STOCH_D"] = df["STOCH_K"].rolling(config.stoch_d).mean()

    # Volume indicators
    df["OBV"] = (np.sign(close.diff()) * volume).fillna(0).cumsum()
    df["VOL_SMA_20"] = volume.rolling(20).mean()
    df["VOLUME_RATIO"] = volume / df["VOL_SMA_20"]

    # VWAP (session-based approximation)
    typical_price = (high + low + close) / 3
    df["VWAP"] = (typical_price * volume).rolling(config.bb_period).sum() / volume.rolling(config.bb_period).sum()

    # CCI
    tp = (high + low + close) / 3
    sma_tp = tp.rolling(config.cci_period).mean()
    mean_dev = tp.rolling(config.cci_period).apply(lambda x: np.mean(np.abs(x - x.mean())))
    df[f"CCI_{config.cci_period}"] = (tp - sma_tp) / (0.015 * mean_dev.replace(0, np.nan))

    # Williams %R
    highest_high_wr = high.rolling(config.willr_period).max()
    lowest_low_wr = low.rolling(config.willr_period).min()
    df[f"WILLR_{config.willr_period}"] = -100 * (highest_high_wr - close) / (highest_high_wr - lowest_low_wr).replace(0, np.nan)

    # Donchian Channels
    df["DC_UPPER"] = high.rolling(config.donchian_period).max()
    df["DC_LOWER"] = low.rolling(config.donchian_period).min()
    df["DC_MIDDLE"] = (df["DC_UPPER"] + df["DC_LOWER"]) / 2

    # MFI (simplified)
    typical_price = (high + low + close) / 3
    money_flow = typical_price * volume
    pos_flow = money_flow.where(typical_price > typical_price.shift(), 0).rolling(config.mfi_period).sum()
    neg_flow = money_flow.where(typical_price < typical_price.shift(), 0).rolling(config.mfi_period).sum()
    mfi_ratio = pos_flow / neg_flow.replace(0, np.nan)
    df[f"MFI_{config.mfi_period}"] = 100 - (100 / (1 + mfi_ratio))

    # CMF
    mf_multiplier = ((close - low) - (high - close)) / (high - low).replace(0, np.nan)
    mf_volume = mf_multiplier * volume
    df[f"CMF_{config.cmf_period}"] = mf_volume.rolling(config.cmf_period).sum() / volume.rolling(config.cmf_period).sum()

    # Supertrend (simplified, static bands)
    hl2 = (high + low) / 2
    atr = df[f"ATR_{config.atr_period}"]
    df["SUPERT_LONG"] = hl2 - config.supertrend_multiplier * atr
    df["SUPERT_SHORT"] = hl2 + config.supertrend_multiplier * atr
    df["SUPERT"] = df["SUPERT_LONG"]
    df["SUPERT_DIR"] = np.where(close > df["SUPERT_LONG"], 1, -1)

    return df


def resolve_engine(engine: str, has_pandas_ta: bool) -> str:
    """Resolve which engine activates. Pure function for manifest/tests.

    "manual" -> always "manual". "reference" -> "reference" or raise when
    pandas-ta is missing. "auto"/anything else -> "reference" if installed
    (legacy behavior), else "manual".
    """
    if engine == "manual":
        return "manual"
    if engine == "reference":
        if not has_pandas_ta:
            raise RuntimeError(
                "Reference (pandas-ta) engine requested but pandas-ta is not installed. "
                "Install it for validation runs, or use engine='manual'.")
        return "reference"
    return "reference" if has_pandas_ta else "manual"
