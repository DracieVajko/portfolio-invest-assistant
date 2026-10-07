from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Try to import pandas-ta
try:
    import pandas_ta as ta
    HAS_PANDAS_TA = True
except ImportError:
    HAS_PANDAS_TA = False
    logger.warning("pandas-ta not installed, using manual indicators")

# TA-Lib is optional and unavailable on most Windows hosts. Candlestick
# patterns are attempted only when the native capability is present.
try:
    import talib  # noqa: F401
    HAS_TALIB = True
except ImportError:
    HAS_TALIB = False
    logger.info("TA-Lib not installed, candlestick patterns disabled")

# Finviz kill-switch: on 404/API-incompatibility the integration disables
# itself for the remainder of the run instead of retrying every stage.
_FINVIZ_DISABLED = False
_FINVIZ_API_MARKERS = ("404", "not found", "invalid", "unsupported", "incompatible", "no longer",
                       "has no attribute", "unexpected keyword")


def _finviz_api_broken(exc: Exception) -> bool:
    low = str(exc or "").lower()
    return any(m in low for m in _FINVIZ_API_MARKERS)


def disable_finviz(reason: str = "") -> None:
    """Disable Finviz for the remainder of the run (concise, no traceback)."""
    global _FINVIZ_DISABLED
    if not _FINVIZ_DISABLED:
        _FINVIZ_DISABLED = True
        logger.info("FinViz disabled for the remainder of the run%s", f": {reason}" if reason else "")


def finviz_enabled() -> bool:
    return HAS_FINVIZ and not _FINVIZ_DISABLED


def group_sector_breadth_from_table(df, sectors: list[str] | None = None) -> dict[str, Any]:
    """Parse a finviz GROUP sector table (1 row = 1 sector) into breadth data.

    Expected columns (live HTML headers): Name, Market Cap, P/E, Change,
    Volume, Stocks, ... ``Change`` arrives via finvizfinance's
    ``number_convert`` as a fraction (``"-2.12%"`` -> ``-0.0212``) or None;
    a defensive branch also accepts raw ``"x%"`` strings. ``count`` comes
    from the ``Stocks`` column. Per-ticker advancing/declining distribution
    is NOT in this table -> those keys stay None (declared, not hidden).
    Never raises: unusable tables yield {}.
    """
    results: dict[str, Any] = {}
    try:
        if df is None or getattr(df, "empty", True):
            return results
        records = df.to_dict("records") if hasattr(df, "to_dict") else []
        by_name: dict[str, dict] = {}
        for rec in records:
            if isinstance(rec, dict) and str(rec.get("Name", "")).strip():
                by_name[str(rec.get("Name")).strip()] = rec
        wanted = sectors or list(by_name.keys())
        for sector in wanted:
            rec = by_name.get(str(sector).strip())
            if not isinstance(rec, dict):
                continue
            change_raw = rec.get("Change", None)
            avg_change = None
            try:
                if isinstance(change_raw, str):
                    s = change_raw.strip()
                    if s and s != "-":
                        avg_change = float(s.rstrip("%")) / 100.0 if s.endswith("%") else float(s)
                        avg_change = round(avg_change * 100.0, 4)
                elif isinstance(change_raw, bool):
                    avg_change = None
                elif change_raw is not None:
                    avg_change = round(float(change_raw) * 100.0, 4)
            except (TypeError, ValueError):
                avg_change = None
            count = None
            try:
                if rec.get("Stocks", None) is not None:
                    count = int(float(rec.get("Stocks")))
            except (TypeError, ValueError):
                count = None
            results[str(sector).strip()] = {
                "count": count,
                "avg_change": avg_change,
                "advancing": None,
                "declining": None,
                "source": "finviz-group-table-v1",
            }
    except Exception:
        return {}
    return results


def should_use_finviz(yahoo_symbol: str | None) -> bool:
    """FinViz covers US listings only — gate BEFORE any HTTP call.

    Yahoo tickers (``AAPL``, ``BRK.B``) pass; dotted EU venues (``EXI2.DE``),
    crypto pairs (``BTC-USD``) and empty inputs are skipped so a dataless 404
    can never fire. US-venue proof beyond the ticker itself is unnecessary
    here: the kill-switch remains the backstop for API drift.
    """
    import re as _re_fv

    s = str(yahoo_symbol or "").strip()
    if not s:
        return False
    if s.upper().endswith(("-USD",)):
        return False
    return bool(_re_fv.match(r"^[A-Z]{1,5}(\.[AB])?$", s))

# Try to import finvizfinance
try:
    from finvizfinance.quote import finvizfinance
    from finvizfinance.screener.overview import Overview as FinvizOverview
    from finvizfinance.group.overview import Overview as FinvizGroupOverview
    from finvizfinance.news import News as FinvizNews
    from finvizfinance.insider import Insider as FinvizInsider
    HAS_FINVIZ = True
except ImportError:
    HAS_FINVIZ = False
    logger.warning("finvizfinance not installed, FinViz features disabled")


# Canonical manual engine lives in research/indicators.py (Phase 3 policy A).
# IndicatorConfig is re-exported here for backward compatibility.
from investment_engine.research.indicators import (
    ENGINE_VERSION as MANUAL_ENGINE_VERSION,
    IndicatorConfig,
    apply_manual_indicators,
    resolve_engine,
)


class TechnicalAnalyzer:
    """
    Comprehensive technical analysis engine using pandas-ta.
    Supports multi-timeframe analysis with 150+ indicators.
    """

    def __init__(self, config: IndicatorConfig | None = None, engine: str = "auto"):
        self.config = config or IndicatorConfig()
        # Policy A: "manual" forces the canonical engine; "reference" requires
        # pandas-ta (raises when missing); "auto" preserves legacy behavior.
        self.engine = engine
        self.resolved_engine = resolve_engine(engine, HAS_PANDAS_TA)
        self._validate_dependencies()

    def _validate_dependencies(self):
        if not HAS_PANDAS_TA:
            logger.warning("pandas-ta not available, using manual indicator calculations")
        if not HAS_FINVIZ:
            logger.info("finvizfinance not available, FinViz features disabled")

    def _validate_dataframe(self, df: pd.DataFrame, min_bars: int) -> bool:
        """Validate OHLCV data integrity."""
        if df.empty or len(df) < min_bars:
            return False
        if df.isnull().any().any():
            return False
        if (df["high"] < df["low"]).any():
            return False
        if (df["close"] <= 0).any():
            return False
        return True

    def analyze(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Apply all indicators to OHLCV DataFrame.
        Returns DataFrame with all indicator columns added.
        """
        if df.empty:
            return df

        df = df.copy()
        df.columns = [c.lower() for c in df.columns]

        required = {"open", "high", "low", "close", "volume"}
        if not required.issubset(df.columns):
            missing = required - set(df.columns)
            raise ValueError(f"Missing required columns: {missing}")

        # Policy A: canonical manual engine unless reference explicitly resolved.
        if self.resolved_engine == "manual":
            df = apply_manual_indicators(df, self.config)
        else:
            df = self._apply_trend_indicators(df)
            df = self._apply_momentum_indicators(df)
            df = self._apply_volatility_indicators(df)
            df = self._apply_volume_indicators(df)
            df = self._apply_candlestick_patterns(df)

        return df

    def _apply_manual_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Manual indicator calculations (delegates to the canonical engine)."""
        return apply_manual_indicators(df, self.config)

    def _apply_trend_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        # SMAs
        for period in self.config.sma_periods:
            df[f"SMA_{period}"] = ta.sma(df["close"], length=period)

        # EMAs
        for period in self.config.ema_periods:
            df[f"EMA_{period}"] = ta.ema(df["close"], length=period)

        # MACD
        macd = ta.macd(
            df["close"],
            fast=self.config.macd_fast,
            slow=self.config.macd_slow,
            signal=self.config.macd_signal,
        )
        if macd is not None:
            df = pd.concat([df, macd], axis=1)
            # Standardize column names
            df.rename(columns={
                f"MACD_{self.config.macd_fast}_{self.config.macd_slow}_{self.config.macd_signal}": "MACD",
                f"MACDh_{self.config.macd_fast}_{self.config.macd_slow}_{self.config.macd_signal}": "MACD_HIST",
                f"MACDs_{self.config.macd_fast}_{self.config.macd_slow}_{self.config.macd_signal}": "MACD_SIGNAL",
            }, inplace=True)

        # ADX
        adx = ta.adx(df["high"], df["low"], df["close"], length=self.config.adx_period)
        if adx is not None:
            df = pd.concat([df, adx], axis=1)
            df.rename(columns={
                f"ADX_{self.config.adx_period}": "ADX",
                f"DMP_{self.config.adx_period}": "DMP",
                f"DMN_{self.config.adx_period}": "DMN",
            }, inplace=True)

        # Supertrend
        st = ta.supertrend(
            df["high"], df["low"], df["close"],
            length=self.config.supertrend_period,
            multiplier=self.config.supertrend_multiplier,
        )
        if st is not None:
            df = pd.concat([df, st], axis=1)
            df.rename(columns={
                f"SUPERT_{self.config.supertrend_period}_{self.config.supertrend_multiplier}": "SUPERT",
                f"SUPERTd_{self.config.supertrend_period}_{self.config.supertrend_multiplier}": "SUPERT_DIR",
                f"SUPERTl_{self.config.supertrend_period}_{self.config.supertrend_multiplier}": "SUPERT_LONG",
                f"SUPERTs_{self.config.supertrend_period}_{self.config.supertrend_multiplier}": "SUPERT_SHORT",
            }, inplace=True)

        return df

    def _apply_momentum_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        # RSI
        for period in self.config.rsi_periods:
            df[f"RSI_{period}"] = ta.rsi(df["close"], length=period)

        # Stochastic
        stoch = ta.stoch(
            df["high"], df["low"], df["close"],
            k=self.config.stoch_k, d=self.config.stoch_d,
        )
        if stoch is not None:
            df = pd.concat([df, stoch], axis=1)
            df.rename(columns={
                f"STOCHk_{self.config.stoch_k}_{self.config.stoch_d}": "STOCH_K",
                f"STOCHd_{self.config.stoch_k}_{self.config.stoch_d}": "STOCH_D",
            }, inplace=True)

        # CCI
        df[f"CCI_{self.config.cci_period}"] = ta.cci(
            df["high"], df["low"], df["close"], length=self.config.cci_period,
        )

        # Williams %R
        df[f"WILLR_{self.config.willr_period}"] = ta.willr(
            df["high"], df["low"], df["close"], length=self.config.willr_period,
        )

        return df

    def _apply_volatility_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        # Bollinger Bands
        bb = ta.bbands(df["close"], length=self.config.bb_period, std=self.config.bb_std)
        if bb is not None:
            df = pd.concat([df, bb], axis=1)
            df.rename(columns={
                f"BBL_{self.config.bb_period}_{self.config.bb_std}": "BB_LOWER",
                f"BBM_{self.config.bb_period}_{self.config.bb_std}": "BB_MIDDLE",
                f"BBU_{self.config.bb_period}_{self.config.bb_std}": "BB_UPPER",
                f"BBB_{self.config.bb_period}_{self.config.bb_std}": "BB_BANDWIDTH",
                f"BBP_{self.config.bb_period}_{self.config.bb_std}": "BB_PERCENT",
            }, inplace=True)

        # Keltner Channels
        kc = ta.kc(
            df["high"], df["low"], df["close"],
            length=self.config.kc_period, scalar=self.config.kc_scalar,
        )
        if kc is not None:
            df = pd.concat([df, kc], axis=1)
            df.rename(columns={
                f"KCLe_{self.config.kc_period}_{self.config.kc_scalar}": "KC_LOWER",
                f"KCMe_{self.config.kc_period}_{self.config.kc_scalar}": "KC_MIDDLE",
                f"KCUp_{self.config.kc_period}_{self.config.kc_scalar}": "KC_UPPER",
            }, inplace=True)

        # ATR
        df[f"ATR_{self.config.atr_period}"] = ta.atr(
            df["high"], df["low"], df["close"], length=self.config.atr_period,
        )

        # Donchian Channels
        dc = ta.donchian(df["high"], df["low"], length=self.config.donchian_period)
        if dc is not None:
            df = pd.concat([df, dc], axis=1)
            df.rename(columns={
                f"DCL_{self.config.donchian_period}": "DC_LOWER",
                f"DCM_{self.config.donchian_period}": "DC_MIDDLE",
                f"DCU_{self.config.donchian_period}": "DC_UPPER",
            }, inplace=True)

        return df

    def _apply_volume_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        if self.config.obv:
            df["OBV"] = ta.obv(df["close"], df["volume"])

        if self.config.vwap:
            df["VWAP"] = ta.vwap(df["high"], df["low"], df["close"], df["volume"])

        if self.config.mfi_period:
            df[f"MFI_{self.config.mfi_period}"] = ta.mfi(
                df["high"], df["low"], df["close"], df["volume"],
                length=self.config.mfi_period,
            )

        if self.config.cmf_period:
            df[f"CMF_{self.config.cmf_period}"] = ta.cmf(
                df["high"], df["low"], df["close"], df["volume"],
                length=self.config.cmf_period,
            )

        # Volume ratio (current vs average)
        df["VOL_SMA_20"] = ta.sma(df["volume"], length=20)
        df["VOLUME_RATIO"] = df["volume"] / df["VOL_SMA_20"]

        return df

    def _apply_candlestick_patterns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply candlestick patterns (requires TA-Lib native capability)."""
        if not HAS_TALIB:
            logger.debug("Skipping candlestick patterns: TA-Lib capability absent")
            return df
        try:
            for pattern in self.config.candlestick_patterns:
                result = ta.cdl_pattern(df["open"], df["high"], df["low"], df["close"], name=pattern)
                if result is not None:
                    df[f"CDL_{pattern.upper()}"] = result
        except Exception as e:
            logger.debug("Candlestick patterns not available: %s", e)
        return df

    def get_latest_values(self, df: pd.DataFrame) -> dict[str, Any]:
        """Extract latest indicator values as a flat dict."""
        if df.empty:
            return {}

        last = df.iloc[-1]
        result = {}

        # Key indicators to extract
        key_indicators = [
            # Trend
            *[f"SMA_{p}" for p in self.config.sma_periods],
            *[f"EMA_{p}" for p in self.config.ema_periods],
            "MACD", "MACD_HIST", "MACD_SIGNAL",
            "ADX", "DMP", "DMN",
            "SUPERT", "SUPERT_DIR", "SUPERT_LONG", "SUPERT_SHORT",
            # Momentum
            *[f"RSI_{p}" for p in self.config.rsi_periods],
            "STOCH_K", "STOCH_D",
            f"CCI_{self.config.cci_period}",
            f"WILLR_{self.config.willr_period}",
            # Volatility
            "BB_LOWER", "BB_MIDDLE", "BB_UPPER", "BB_BANDWIDTH", "BB_PERCENT",
            "KC_LOWER", "KC_MIDDLE", "KC_UPPER",
            f"ATR_{self.config.atr_period}",
            "DC_LOWER", "DC_MIDDLE", "DC_UPPER",
            # Volume
            "OBV", "VWAP",
            f"MFI_{self.config.mfi_period}",
            f"CMF_{self.config.cmf_period}",
            "VOLUME_RATIO",
        ]

        for key in key_indicators:
            if key in df.columns:
                val = last[key]
                if pd.notna(val):
                    result[key] = float(val)

        # Add price data
        result["CLOSE"] = float(last["close"])
        result["HIGH"] = float(last["high"])
        result["LOW"] = float(last["low"])
        result["OPEN"] = float(last["open"])
        result["VOLUME"] = float(last["volume"])

        return result

    def get_multi_timeframe_analysis(self, data: dict[str, pd.DataFrame]) -> dict[str, dict]:
        """Analyze multiple timeframes and return consolidated results."""
        results = {}
        for tf_name, df in data.items():
            if df is not None and not df.empty:
                analyzed = self.analyze(df)
                results[tf_name] = {
                    "indicators": self.get_latest_values(analyzed),
                    "trend": self._determine_trend(analyzed),
                    "momentum": self._assess_momentum(analyzed),
                    "volatility": self._assess_volatility(analyzed),
                }
        return results

    def _determine_trend(self, df: pd.DataFrame) -> str:
        """Determine trend from latest indicators."""
        last = df.iloc[-1]
        close = last["close"]

        # Check multiple SMAs - handle NaN values
        sma_20 = last.get("SMA_20")
        sma_50 = last.get("SMA_50")
        sma_200 = last.get("SMA_200")

        # Convert NaN to 0 for comparison
        import numpy as np
        sma_20 = 0 if (sma_20 is None or (isinstance(sma_20, float) and np.isnan(sma_20))) else sma_20
        sma_50 = 0 if (sma_50 is None or (isinstance(sma_50, float) and np.isnan(sma_50))) else sma_50
        sma_200 = 0 if (sma_200 is None or (isinstance(sma_200, float) and np.isnan(sma_200))) else sma_200

        if close > sma_20 > sma_50 > sma_200:
            return "STRONG_UP"
        elif close > sma_20 > sma_50:
            return "UP"
        elif close < sma_20 < sma_50 < sma_200:
            return "STRONG_DOWN"
        elif close < sma_20 < sma_50:
            return "DOWN"
        elif abs(close - sma_20) / close < 0.02:
            return "CONSOLIDATING"
        return "NEUTRAL"

    def _assess_momentum(self, df: pd.DataFrame) -> str:
        """Assess momentum from RSI and MACD."""
        last = df.iloc[-1]
        rsi = last.get("RSI_14", 50)
        macd_hist = last.get("MACD_HIST", 0)

        if rsi > 70 and macd_hist > 0:
            return "OVERBOUGHT"
        elif rsi > 60 and macd_hist > 0:
            return "BULLISH"
        elif rsi < 30 and macd_hist < 0:
            return "OVERSOLD"
        elif rsi < 40 and macd_hist < 0:
            return "BEARISH"
        return "NEUTRAL"

    def _assess_volatility(self, df: pd.DataFrame) -> str:
        """Assess volatility from Bollinger Band width."""
        last = df.iloc[-1]
        bb_width = last.get("BB_BANDWIDTH", 0)
        close = last["close"]

        if close > 0:
            rel_width = bb_width / close * 100
            if rel_width > 10:
                return "HIGH"
            elif rel_width < 3:
                return "LOW"
        return "NORMAL"


class FinVizEnrichment:
    """Enrich analysis with FinViz fundamental and news data."""

    def __init__(self):
        if not HAS_FINVIZ:
            raise ImportError("finvizfinance required: pip install finvizfinance")

    def get_quote_data(self, symbol: str) -> dict[str, Any]:
        """Get comprehensive quote data from FinViz."""
        if not finviz_enabled():
            return {}
        if not should_use_finviz(symbol):
            logger.debug("FinViz quote skipped for non-US symbol: %s", symbol)
            return {}
        try:
            stock = finvizfinance(symbol)
            return {
                "fundamentals": stock.ticker_fundament(),
                "description": stock.ticker_description(),
                "news": stock.ticker_news().head(10).to_dict("records"),
                "insider": stock.ticker_inside_trader().head(5).to_dict("records"),
                "peers": stock.ticker_peer(),
                "etf_holders": stock.ticker_etf_holders(),
                "outer_ratings": stock.ticker_outer_ratings().to_dict("records") if hasattr(stock, 'ticker_outer_ratings') else [],
            }
        except Exception as e:
            if _finviz_api_broken(e):
                disable_finviz(str(e)[:120])
            else:
                logger.warning("FinViz quote fetch failed for %s: %s", symbol, e)
            return {}

    def get_sector_breadth(self, sectors: list[str] | None = None) -> dict[str, Any]:
        """Get sector performance breadth from FinViz Groups.

        Uses ONE ``group.overview`` call (``screener_view(group="Sector")``):
        the group endpoint has no ``set_filter`` (calling it raised
        ``AttributeError`` and silently killed sector data in run 524fffa0).
        The table is sector-aggregate (1 row = 1 sector), so ``avg_change``
        is the sector's own Change and ``advancing``/``declining`` stay None.
        """
        if sectors is None:
            sectors = [
                "Technology", "Healthcare", "Financial", "Consumer Cyclical",
                "Industrial", "Energy", "Utilities", "Real Estate",
                "Basic Materials", "Consumer Defensive", "Communication Services",
            ]

        results: dict[str, Any] = {}
        if not finviz_enabled():
            return results
        try:
            go = FinvizGroupOverview()
            df = go.screener_view(group="Sector", order="Name")
            return group_sector_breadth_from_table(df, sectors)
        except Exception as e:
            if _finviz_api_broken(e):
                disable_finviz(str(e)[:120])
            else:
                logger.warning("FinViz group overview failed: %s", e)
            return results

    def get_market_news(self) -> dict[str, list]:
        """Get market news from FinViz."""
        if not finviz_enabled():
            return {"news": [], "blogs": []}
        try:
            fnews = FinvizNews()
            all_news = fnews.get_news()
            return {
                "news": all_news.get("news", pd.DataFrame()).head(20).to_dict("records"),
                "blogs": all_news.get("blogs", pd.DataFrame()).head(10).to_dict("records"),
            }
        except Exception as e:
            if _finviz_api_broken(e):
                disable_finviz(str(e)[:120])
            else:
                logger.warning("FinViz news fetch failed: %s", e)
            return {"news": [], "blogs": []}

    def get_insider_activity(self, option: str = "latest") -> list[dict]:
        """Get insider trading activity."""
        if not finviz_enabled():
            return []
        try:
            finsider = FinvizInsider(option=option)
            return finsider.get_insider().head(20).to_dict("records")
        except Exception as e:
            if _finviz_api_broken(e):
                disable_finviz(str(e)[:120])
            else:
                logger.warning("FinViz insider fetch failed: %s", e)
            return []

    def screen_stocks(self, filters: dict[str, str]) -> pd.DataFrame:
        """Screen stocks using FinViz filters."""
        if not finviz_enabled():
            return pd.DataFrame()
        try:
            fo = FinvizOverview()
            fo.set_filter(filters_dict=filters)
            return fo.screener_view()
        except Exception as e:
            if _finviz_api_broken(e):
                disable_finviz(str(e)[:120])
            else:
                logger.warning("FinViz screener failed: %s", e)
            return pd.DataFrame()


def compute_derived_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Compute additional derived metrics from indicators."""
    df = df.copy()

    # Price vs MA distances
    for period in [20, 50, 100, 200]:
        sma_col = f"SMA_{period}"
        if sma_col in df.columns:
            df[f"DIST_SMA_{period}"] = (df["close"] - df[sma_col]) / df[sma_col] * 100

    # MA crossovers
    if "SMA_20" in df.columns and "SMA_50" in df.columns:
        df["SMA_20_50_CROSS"] = np.where(
            df["SMA_20"] > df["SMA_50"], 1, -1
        )
        df["SMA_20_50_CROSS_CHG"] = df["SMA_20_50_CROSS"].diff()

    # MACD signal cross
    if "MACD" in df.columns and "MACD_SIGNAL" in df.columns:
        df["MACD_CROSS"] = np.where(df["MACD"] > df["MACD_SIGNAL"], 1, -1)
        df["MACD_CROSS_CHG"] = df["MACD_CROSS"].diff()

    # RSI levels
    if "RSI_14" in df.columns:
        df["RSI_OB"] = df["RSI_14"] > 70
        df["RSI_OS"] = df["RSI_14"] < 30

    # BB squeeze
    if "BB_BANDWIDTH" in df.columns:
        bb_width_ma = df["BB_BANDWIDTH"].rolling(20).mean()
        df["BB_SQUEEZE"] = df["BB_BANDWIDTH"] < bb_width_ma * 0.8

    return df