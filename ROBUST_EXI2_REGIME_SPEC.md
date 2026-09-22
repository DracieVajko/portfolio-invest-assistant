# ROBUST EXI2 MARKET REGIME DETECTION - COMPLETE IMPLEMENTATION SPEC

## Executive Summary
Build a production-grade, multi-timeframe technical analysis engine for EXI2 (iShares Dow Jones Global Titans 50 UCITS ETF) that detects market regime shifts with high confidence, integrates news sentiment with strict temporal filtering, and generates comprehensive Markdown reports.

---

## 1. MULTI-TIMEFRAME DATA ARCHITECTURE

### 1.1 Timeframe Definitions (Configurable)
```python
TIMEFRAMES = {
    "intraday":    {"interval": "15m",  "period": "1d",    "lookback_bars": 96},   # 1 day of 15m bars
    "intraday_1h": {"interval": "1h",   "period": "5d",    "lookback_bars": 120},  # 5 days of 1h bars
    "daily":       {"interval": "1d",   "period": "3mo",   "lookback_bars": 63},   # ~3 months daily
    "weekly":      {"interval": "1wk",  "period": "2y",    "lookback_bars": 104},  # 2 years weekly
    "monthly":     {"interval": "1mo",  "period": "max",   "lookback_bars": 120},  # 10 years monthly
    "quarterly":   {"interval": "3mo",  "period": "max",   "lookback_bars": 40},   # 10 years quarterly
}
```

### 1.2 Data Fetching Strategy (yfinance)
```python
def fetch_multi_timeframe(symbol: str, timeframes: dict) -> dict[str, pd.DataFrame]:
    """
    Fetch OHLCV for all timeframes in parallel.
    Returns dict: {timeframe_name: DataFrame[Open, High, Low, Close, Volume]}
    """
    results = {}
    for tf_name, config in timeframes.items():
        try:
            df = yf.download(
                symbol,
                period=config["period"],
                interval=config["interval"],
                progress=False,
                auto_adjust=True,      # Split/dividend adjusted
                actions=True,          # Include dividends/splits
                threads=True,
            )
            if not df.empty:
                df = df.dropna()
                df.columns = [c.lower() for c in df.columns]  # normalize
                results[tf_name] = df.tail(config["lookback_bars"])
        except Exception as e:
            logger.warning(f"Failed {tf_name} for {symbol}: {e}")
    return results
```

### 1.3 Data Quality Gates
```python
def validate_dataframe(df: pd.DataFrame, min_bars: int = 20) -> tuple[bool, str]:
    """Validate OHLCV data integrity."""
    if df.empty or len(df) < min_bars:
        return False, f"Insufficient bars: {len(df)}"
    if df.isnull().any().any():
        return False, "NaN values present"
    if (df["high"] < df["low"]).any():
        return False, "High < Low detected"
    if (df["close"] <= 0).any():
        return False, "Non-positive prices"
    # Check for stale data (last bar > 2 intervals old)
    last_ts = df.index[-1]
    if (pd.Timestamp.now(tz="UTC") - last_ts) > pd.Timedelta(hours=48):
        return False, f"Stale data: last bar {last_ts}"
    return True, "OK"
```

---

## 2. TECHNICAL INDICATOR ENGINE (pandas-ta)

### 2.1 Core Indicators per Timeframe
```python
INDICATOR_CONFIG = {
    "trend": {
        "sma": [20, 50, 100, 200],
        "ema": [12, 26, 50],
        "macd": {"fast": 12, "slow": 26, "signal": 9},
        "adx": 14,
        "supertrend": {"period": 10, "multiplier": 3},
    },
    "momentum": {
        "rsi": [14, 7],
        "stoch": {"k": 14, "d": 3},
        "cci": 20,
        "willr": 14,
    },
    "volatility": {
        "bbands": {"length": 20, "std": 2},
        "kc": {"length": 20, "scalar": 1.5},  # Keltner Channels
        "atr": 14,
        "donchian": 20,
    },
    "volume": {
        "obv": None,
        "vwap": None,
        "mfi": 14,
        "cmf": 20,
    },
    "candlesticks": {
        # 60+ patterns available when TA-Lib installed
        "patterns": [
            "doji", "hammer", "hanging_man", "engulfing", "harami",
            "morning_star", "evening_star", "three_white_soldiers",
            "three_black_crows", "piercing", "dark_cloud_cover",
            "shooting_star", "inverted_hammer", "marubozu",
        ]
    }
}
```

### 2.2 Indicator Application
```python
def apply_all_indicators(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Apply all indicators using pandas-ta strategy."""
    import pandas_ta as ta
    
    # Create custom strategy
    strategy = ta.Strategy(
        name="MultiTF_Regime",
        description="Full indicator suite for regime detection",
        ta=[
            # Trend
            {"kind": "sma", "length": 20}, {"kind": "sma", "length": 50},
            {"kind": "sma", "length": 100}, {"kind": "sma", "length": 200},
            {"kind": "ema", "length": 12}, {"kind": "ema", "length": 26},
            {"kind": "ema", "length": 50},
            {"kind": "macd", "fast": 12, "slow": 26, "signal": 9},
            {"kind": "adx", "length": 14},
            {"kind": "supertrend", "length": 10, "multiplier": 3},
            # Momentum
            {"kind": "rsi", "length": 14}, {"kind": "rsi", "length": 7},
            {"kind": "stoch", "k": 14, "d": 3},
            {"kind": "cci", "length": 20},
            {"kind": "willr", "length": 14},
            # Volatility
            {"kind": "bbands", "length": 20, "std": 2},
            {"kind": "kc", "length": 20, "scalar": 1.5},
            {"kind": "atr", "length": 14},
            {"kind": "donchian", "length": 20},
            # Volume
            {"kind": "obv"}, {"kind": "vwap"}, {"kind": "mfi", "length": 14},
            {"kind": "cmf", "length": 20},
        ]
    )
    df.ta.strategy(strategy)
    
    # Candlestick patterns (requires TA-Lib)
    try:
        for pattern in config["candlesticks"]["patterns"]:
            df.ta.cdl_pattern(name=pattern)
    except Exception:
        logger.warning("TA-Lib not available, skipping candlestick patterns")
    
    return df
```

---

## 3. PEAK/VALLEY DETECTION ALGORITHMS

### 3.1 Multi-Timeframe Peak Detection
```python
from scipy.signal import find_peaks, argrelextrema
import numpy as np

class PeakValleyDetector:
    """
    Detects significant peaks and valleys across timeframes.
    Uses prominence and distance filters to avoid noise.
    """
    
    def __init__(self, prominence_pct: float = 2.0, min_distance: int = 5):
        self.prominence_pct = prominence_pct
        self.min_distance = min_distance
    
    def detect(self, close: pd.Series) -> dict:
        prices = close.values
        prominence = prices.std() * (self.prominence_pct / 100)
        
        # Peaks (highs)
        peak_idx, peak_props = find_peaks(
            prices, 
            prominence=prominence,
            distance=self.min_distance,
            width=2
        )
        
        # Valleys (lows) - invert prices
        valley_idx, valley_props = find_peaks(
            -prices,
            prominence=prominence,
            distance=self.min_distance,
            width=2
        )
        
        peaks = self._format_extrema(peak_idx, peak_props, close, "peak")
        valleys = self._format_extrema(valley_idx, valley_props, close, "valley")
        
        return {
            "peaks": peaks,
            "valleys": valleys,
            "current_trend": self._determine_trend(peaks, valleys, close),
            "key_levels": self._identify_key_levels(peaks, valleys, close),
        }
    
    def _format_extrema(self, indices, props, close: pd.Series, typ: str) -> list:
        return [
            {
                "date": close.index[i].isoformat(),
                "price": float(close.iloc[i]),
                "prominence": float(props["prominences"][j]),
                "width": int(props["widths"][j]),
                "type": typ,
            }
            for j, i in enumerate(indices)
        ]
    
    def _determine_trend(self, peaks, valleys, close) -> str:
        """Classify current trend: UP, DOWN, CONSOLIDATING, REVERSING_UP, REVERSING_DOWN"""
        if not peaks or not valleys:
            return "UNKNOWN"
        
        last_peak = peaks[-1]["price"] if peaks else 0
        last_valley = valleys[-1]["price"] if valleys else float("inf")
        current = close.iloc[-1]
        
        # Higher highs + higher lows = UP
        if len(peaks) >= 2 and len(valleys) >= 2:
            if peaks[-1]["price"] > peaks[-2]["price"] and valleys[-1]["price"] > valleys[-2]["price"]:
                return "UP"
            if peaks[-1]["price"] < peaks[-2]["price"] and valleys[-1]["price"] < valleys[-2]["price"]:
                return "DOWN"
        
        # Near recent peak = CONSOLIDATING_AT_TOP
        if current >= last_peak * 0.98:
            return "CONSOLIDATING_AT_TOP"
        # Near recent valley = CONSOLIDATING_AT_BOTTOM
        if current <= last_valley * 1.02:
            return "CONSOLIDATING_AT_BOTTOM"
        
        return "CONSOLIDATING"
    
    def _identify_key_levels(self, peaks, valleys, close) -> dict:
        """Identify support/resistance levels from extrema clustering."""
        all_levels = [p["price"] for p in peaks] + [v["price"] for v in valleys]
        if not all_levels:
            return {"support": [], "resistance": []}
        
        # Cluster nearby levels (within 1%)
        clusters = self._cluster_levels(all_levels, threshold_pct=1.0)
        current = close.iloc[-1]
        
        support = [c for c in clusters if c < current]
        resistance = [c for c in clusters if c > current]
        
        return {
            "support": sorted(support, reverse=True)[:5],
            "resistance": sorted(resistance)[:5],
            "nearest_support": max(support) if support else None,
            "nearest_resistance": min(resistance) if resistance else None,
        }
    
    def _cluster_levels(self, levels: list, threshold_pct: float = 1.0) -> list:
        """Group price levels within threshold% of each other."""
        if not levels:
            return []
        levels = sorted(levels)
        clusters = []
        current_cluster = [levels[0]]
        
        for level in levels[1:]:
            if (level - current_cluster[-1]) / current_cluster[-1] * 100 <= threshold_pct:
                current_cluster.append(level)
            else:
                clusters.append(np.mean(current_cluster))
                current_cluster = [level]
        clusters.append(np.mean(current_cluster))
        return clusters
```

### 3.2 Regime Classification Matrix
```python
REGIME_RULES = {
    "PEAK_HOLD": {
        "conditions": [
            "trend == 'CONSOLIDATING_AT_TOP' on daily AND weekly",
            "rsi_daily > 60 AND rsi_weekly > 55",
            "price > sma_20_daily AND price > sma_50_weekly",
            "macd_daily histogram > 0 but declining",
            "volume declining on up-days",
        ],
        "min_timeframes_agree": 2,
        "confidence_threshold": 0.75,
    },
    "DECLINING": {
        "conditions": [
            "trend == 'DOWN' on daily",
            "price < sma_20_daily AND price < sma_50_daily",
            "macd_daily bearish crossover (macd < signal)",
            "rsi_daily < 50",
            "adx_daily > 25 (trend strength)",
            "lower highs + lower lows confirmed",
        ],
        "min_timeframes_agree": 2,
        "confidence_threshold": 0.70,
    },
    "MUST_BUY": {
        "conditions": [
            "price <= 1.02 * sma_200_daily (touching 200DMA)",
            "OR price <= 1.05 * 52_week_low",
            "rsi_daily < 35 AND rsi_weekly < 40",
            "macd_weekly bullish divergence OR crossover",
            "volume spike on down-days (capitulation)",
            "supertrend_daily flips bullish",
        ],
        "min_timeframes_agree": 2,
        "confidence_threshold": 0.80,
    },
    "NEUTRAL": {
        "conditions": ["none of above"],
        "confidence_threshold": 0.50,
    }
}
```

---

## 4. NEWS SENTIMENT WITH STRICT TEMPORAL FILTERING

### 4.1 Fixed News Fetcher (Solves 48h Issue)
```python
# investment_engine/research/news_engine.py

import feedparser
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any
import logging

logger = logging.getLogger(__name__)

class StrictNewsFetcher:
    """
    Fetches news with STRICT temporal validation.
    - Parses actual publication date (not feed date)
    - Deduplicates by content hash
    - Filters by relevance score
    - Returns only verified recent items
    """
    
    def __init__(self, max_age_hours: int = 48, min_relevance: int = 60):
        self.max_age = timedelta(hours=max_age_hours)
        self.min_relevance = min_relevance
        self.seen_hashes = set()
    
    def fetch_for_symbol(self, symbol: str, name: str, limit: int = 5) -> list[dict]:
        """Fetch and strictly filter news for a symbol."""
        queries = [
            f'"{name}" {symbol} stock earnings',
            f'"{name}" {symbol} guidance',
            f'{symbol} stock price target analyst',
        ]
        
        all_items = []
        for query in queries:
            items = self._fetch_google_news(query)
            all_items.extend(items)
        
        # Deduplicate by content hash
        unique_items = self._deduplicate(all_items)
        
        # Strict temporal filter
        cutoff = datetime.now(timezone.utc) - self.max_age
        recent_items = [
            item for item in unique_items
            if item["published_dt"] >= cutoff
        ]
        
        # Relevance scoring
        scored = self._score_relevance(recent_items, symbol, name)
        filtered = [item for item in scored if item["relevance_score"] >= self.min_relevance]
        
        # Sort by relevance * recency
        filtered.sort(key=lambda x: (x["relevance_score"], x["published_dt"]), reverse=True)
        
        return filtered[:limit]
    
    def _fetch_google_news(self, query: str) -> list[dict]:
        """Fetch from Google News RSS with robust parsing."""
        from urllib.parse import quote_plus
        import requests
        
        url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
        try:
            resp = requests.get(url, timeout=15, headers={"User-Agent": "PortfolioAI/2.0"})
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
        except Exception as e:
            logger.warning(f"News fetch failed for '{query}': {e}")
            return []
        
        items = []
        for entry in feed.entries:
            try:
                # CRITICAL: Parse actual publication date
                pub_dt = self._parse_date(entry)
                if not pub_dt:
                    continue
                
                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()
                source = entry.get("source", {}).get("title", "unknown").strip()
                
                if title and link:
                    items.append({
                        "title": title,
                        "url": link,
                        "source": source,
                        "published_dt": pub_dt,
                        "published_str": pub_dt.date().isoformat(),
                        "raw_entry": entry,
                    })
            except Exception as e:
                logger.debug(f"Failed parsing news entry: {e}")
                continue
        return items
    
    def _parse_date(self, entry) -> datetime | None:
        """Parse publication date from multiple possible fields."""
        for field in ["published_parsed", "updated_parsed", "created_parsed"]:
            if hasattr(entry, field) and getattr(entry, field):
                try:
                    return datetime(*getattr(entry, field)[:6], tzinfo=timezone.utc)
                except Exception:
                    continue
        # Fallback: try string parsing
        for field in ["published", "updated", "created"]:
            val = entry.get(field, "")
            if val:
                try:
                    from email.utils import parsedate_to_datetime
                    return parsedate_to_datetime(val).astimezone(timezone.utc)
                except Exception:
                    continue
        return None
    
    def _deduplicate(self, items: list[dict]) -> list[dict]:
        """Remove duplicates by content hash."""
        unique = []
        for item in items:
            content_hash = hashlib.md5(
                f"{item['title']}{item['url']}".encode()
            ).hexdigest()[:16]
            if content_hash not in self.seen_hashes:
                self.seen_hashes.add(content_hash)
                item["content_hash"] = content_hash
                unique.append(item)
        return unique
    
    def _score_relevance(self, items: list[dict], symbol: str, name: str) -> list[dict]:
        """Score relevance based on keyword matching."""
        keywords_high = [symbol.upper(), name.upper(), "earnings", "guidance", "upgrade", "downgrade", "target"]
        keywords_med = ["revenue", "profit", "margin", "dividend", "buyback", "acquisition", "merger"]
        keywords_low = ["stock", "shares", "market", "trading", "investor"]
        
        for item in items:
            text = f"{item['title']} {item['source']}".upper()
            score = 0
            for kw in keywords_high:
                if kw in text: score += 30
            for kw in keywords_med:
                if kw in text: score += 15
            for kw in keywords_low:
                if kw in text: score += 5
            # Penalize generic sources
            if item["source"].upper() in ["STOCKTWITS", "REDDIT", "YAHOO FINANCE"]:
                score *= 0.8
            item["relevance_score"] = min(score, 100)
        return items
```

### 4.2 News Sentiment Analysis
```python
def analyze_news_sentiment(news_items: list[dict]) -> dict:
    """Aggregate sentiment from news headlines."""
    if not news_items:
        return {"sentiment": "NEUTRAL", "score": 50, "count": 0, "key_topics": []}
    
    # Simple keyword-based sentiment (can be replaced with FinBERT)
    positive_kw = ["beat", "raise", "upgrade", "strong", "growth", "record", "bullish", "outperform", "buy"]
    negative_kw = ["miss", "cut", "downgrade", "weak", "decline", "loss", "bearish", "underperform", "sell", "risk"]
    
    pos_count = neg_count = 0
    for item in news_items:
        title = item["title"].lower()
        pos_count += sum(1 for kw in positive_kw if kw in title)
        neg_count += sum(1 for kw in negative_kw if kw in title)
    
    total = pos_count + neg_count
    if total == 0:
        sentiment_score = 50
    else:
        sentiment_score = 50 + (pos_count - neg_count) / total * 50
    
    return {
        "sentiment": "POSITIVE" if sentiment_score > 60 else "NEGATIVE" if sentiment_score < 40 else "NEUTRAL",
        "score": round(sentiment_score, 1),
        "count": len(news_items),
        "positive_signals": pos_count,
        "negative_signals": neg_count,
        "key_topics": extract_key_topics(news_items),
        "latest_headline": news_items[0]["title"] if news_items else None,
    }
```

---

## 5. COMPREHENSIVE REPORT GENERATOR

### 5.1 Report Structure
```python
# investment_engine/reporting/regime_report.py

from dataclasses import dataclass, asdict
from typing import Any
from datetime import datetime

@dataclass
class RegimeReport:
    symbol: str
    generated_at: str
    regime: str
    confidence: float
    timeframes: dict  # per-timeframe analysis
    price_analysis: dict  # peaks, valleys, levels
    indicators: dict  # key indicator values
    news_sentiment: dict
    regime_implications: dict  # portfolio actions
    risk_warnings: list[str]
    
    def to_markdown(self) -> str:
        return f"""# EXI2 Market Regime Analysis Report
**Generated:** {self.generated_at} UTC  
**Symbol:** {self.symbol}  
**Regime:** **{self.regime}** (Confidence: {self.confidence:.0%})

---

## 📊 Multi-Timeframe Trend Analysis

| Timeframe | Trend | Close | SMA20 | SMA50 | SMA200 | RSI(14) | MACD | Volume Trend |
|-----------|-------|-------|-------|-------|--------|---------|------|--------------|
{self._format_tf_table()}

---

## 📈 Price Structure (Peaks & Valleys)

### Recent Peaks (Resistance)
{self._format_peaks()}

### Recent Valleys (Support)
{self._format_valleys()}

### Key Levels
- **Nearest Support:** €{self.price_analysis.get('key_levels', {}).get('nearest_support', 'N/A'):.2f}
- **Nearest Resistance:** €{self.price_analysis.get('key_levels', {}).get('nearest_resistance', 'N/A'):.2f}
- **200-Day MA:** €{self.indicators.get('daily', {}).get('SMA_200', 'N/A'):.2f}
- **52-Week High/Low:** €{self.indicators.get('daily', {}).get('52W_HIGH', 'N/A'):.2f} / €{self.indicators.get('daily', {}).get('52W_LOW', 'N/A'):.2f}

---

## 📰 News Sentiment (Last 48h)
**Overall:** {self.news_sentiment.get('sentiment', 'N/A')} (Score: {self.news_sentiment.get('score', 0)}/100)  
**Articles Analyzed:** {self.news_sentiment.get('count', 0)}  
**Key Topics:** {', '.join(self.news_sentiment.get('key_topics', [])) or 'None'}  
**Latest:** {self.news_sentiment.get('latest_headline', 'N/A')}

---

## ⚡ Regime Implications for Portfolio

| Asset Group | Action | Position Size | Notes |
|-------------|--------|---------------|-------|
{self._format_implications_table()}

---

## ⚠️ Risk Warnings
{self._format_warnings()}

---

## 🔍 Technical Details
```json
{self._to_json()}
```
"""
```

---

## 6. INTEGRATION INTO MAIN ENGINE

### 6.1 Modified `main.py` Integration
```python
# In run_engine() - add after provider creation (line ~121)

from investment_engine.research.market_regime import EXI2RegimeAnalyzer
from investment_engine.reporting.regime_report import RegimeReport

# Initialize analyzer with settings
regime_analyzer = EXI2RegimeAnalyzer(settings)

# Run full analysis
regime_result = regime_analyzer.analyze()

# Create report object
report = RegimeReport(
    symbol="EXI2.DE",
    generated_at=datetime.now(timezone.utc).isoformat(),
    regime=regime_result["regime"],
    confidence=regime_result["confidence"],
    timeframes=regime_result["timeframes"],
    price_analysis=regime_result["price_analysis"],
    indicators=regime_result["indicators"],
    news_sentiment=regime_result["news_sentiment"],
    regime_implications=regime_result["implications"],
    risk_warnings=regime_result["warnings"],
)

# Inject into markdown report (after summary_section)
regime_md = report.to_markdown()
markdown = "\n\n".join([summary, regime_md, *decision_sections, ...])

# Also inject regime context into AI prompts
regime_context = f"""
MARKET REGIME CONTEXT (EXI2):
- Current Regime: {regime_result['regime']} ({regime_result['confidence']:.0%} confidence)
- Key Signal: {regime_result['primary_signal']}
- Portfolio Action: {regime_result['implications']['portfolio_action']}
- Cash Target: {regime_result['implications']['cash_target_pct']}%
"""
# Append to each _decision_prompt() call
```

---

## 7. CONFIGURATION EXTENSIONS

### 7.1 Full `portfolio_config.json` Additions
```json
{
  "settings": {
    "market_regime": {
      "enabled": true,
      "symbol": "EXI2.DE",
      "timeframes": {
        "intraday": {"enabled": true, "interval": "15m", "period": "1d"},
        "intraday_1h": {"enabled": true, "interval": "1h", "period": "5d"},
        "daily": {"enabled": true, "interval": "1d", "period": "3mo"},
        "weekly": {"enabled": true, "interval": "1wk", "period": "2y"},
        "monthly": {"enabled": true, "interval": "1mo", "period": "max"}
      },
      "peak_detection": {
        "prominence_pct": 2.0,
        "min_distance_bars": 5,
        "cluster_threshold_pct": 1.0
      },
      "regime_thresholds": {
        "peak_hold": {
          "rsi_daily_min": 60,
          "rsi_weekly_min": 55,
          "price_above_sma20_daily": true,
          "price_above_sma50_weekly": true,
          "macd_histogram_declining": true
        },
        "declining": {
          "price_below_sma20_daily": true,
          "price_below_sma50_daily": true,
          "macd_bearish_cross": true,
          "rsi_daily_max": 50,
          "adx_min": 25
        },
        "must_buy": {
          "near_200dma_tolerance_pct": 2.0,
          "near_52w_low_tolerance_pct": 5.0,
          "rsi_daily_max": 35,
          "rsi_weekly_max": 40,
          "volume_spike_threshold": 2.0
        }
      },
      "news": {
        "max_age_hours": 48,
        "min_relevance_score": 60,
        "max_articles_per_symbol": 5,
        "sentiment_model": "keyword"  // or "finbert" when available
      },
      "reporting": {
        "include_in_main_report": true,
        "include_charts": false,
        "save_json": true
      }
    }
  }
}
```

---

## 8. FILE STRUCTURE - NEW FILES TO CREATE

```
investment_engine/
├── research/
│   ├── __init__.py
│   ├── market_data.py          # EXISTING - enhance with strict news
│   ├── market_regime.py        # NEW - core analyzer
│   ├── news_engine.py          # NEW - strict news fetcher
│   ├── technical_analysis.py   # NEW - indicator engine
│   └── peak_valley.py          # NEW - extrema detection
├── reporting/
│   ├── __init__.py
│   └── regime_report.py        # NEW - markdown report generator
├── config/
│   └── settings.py             # EXISTING - add MarketRegimeSettings
```

---

## 9. DEPENDENCIES (requirements.txt additions)

```text
# Core
yfinance>=1.6.0
pandas>=2.0.0
pandas-ta>=0.4.71b0
numpy>=1.24.0
scipy>=1.10.0

# News
feedparser>=6.0.10
requests>=2.31.0

# Optional: TA-Lib for 60+ candlestick patterns
# pip install ta-lib  # Requires system lib: brew install ta-lib / apt-get install libta-lib-dev

# Optional: FinBERT for better sentiment
# transformers>=4.30.0
# torch>=2.0.0
```

---

## 10. TESTING STRATEGY

### 10.1 Unit Tests (`tests/test_market_regime.py`)
```python
import pytest
import pandas as pd
import numpy as np
from investment_engine.research.market_regime import EXI2RegimeAnalyzer
from investment_engine.research.peak_valley import PeakValleyDetector

class TestPeakValleyDetector:
    def test_detects_clear_peaks(self):
        # Create synthetic price series with known peaks
        dates = pd.date_range("2024-01-01", periods=100, freq="D")
        prices = pd.Series(100 + np.sin(np.arange(100) * 0.2) * 10 + np.random.randn(100) * 0.5, index=dates)
        
        detector = PeakValleyDetector(prominence_pct=1.5)
        result = detector.detect(prices)
        
        assert len(result["peaks"]) >= 3
        assert len(result["valleys"]) >= 3
        assert result["current_trend"] in ["UP", "DOWN", "CONSOLIDATING", "CONSOLIDATING_AT_TOP", "CONSOLIDATING_AT_BOTTOM"]
    
    def test_key_levels_identified(self):
        dates = pd.date_range("2024-01-01", periods=50, freq="D")
        prices = pd.Series([100]*10 + [110]*10 + [105]*10 + [115]*10 + [112]*10, index=dates)
        
        detector = PeakValleyDetector()
        result = detector.detect(prices)
        
        assert "support" in result["key_levels"]
        assert "resistance" in result["key_levels"]
        assert len(result["key_levels"]["support"]) > 0

class TestRegimeClassification:
    def test_peak_hold_detection(self):
        # Mock data simulating peak hold
        pass
    
    def test_must_buy_detection(self):
        # Mock data at 200DMA with low RSI
        pass
```

### 10.2 Historical Backtest
```python
# scripts/backtest_regime.py
"""
Backtest regime signals against forward returns.
"""
def run_backtest():
    # Fetch 5 years EXI2 data
    # Run regime detection on expanding window
    # Measure: 
    #   - Regime accuracy (did signal predict next 20d return?)
    #   - False positive rate
    #   - Max drawdown vs buy-and-hold
    pass
```

---

## 11. IMPLEMENTATION ORDER (Priority)

| Phase | Files | Description | Effort |
|-------|-------|-------------|--------|
| **1** | `news_engine.py` | Fix 48h news bug, strict filtering | 2h |
| **2** | `technical_analysis.py` | pandas-ta indicator engine | 3h |
| **3** | `peak_valley.py` | Scipy-based extrema detection | 2h |
| **4** | `market_regime.py` | Multi-TF regime classifier | 4h |
| **5** | `regime_report.py` | Comprehensive markdown generator | 2h |
| **6** | `settings.py` + `config.json` | Configuration integration | 1h |
| **7** | `main.py` | Integration + prompt injection | 2h |
| **8** | Tests + Backtest | Validation | 3h |

**Total: ~19 hours**

---

## 12. ROBUSTNESS CHECKLIST

- [ ] **Data Validation**: Every DataFrame validated before processing
- [ ] **Error Handling**: Graceful degradation (missing TF → skip, not crash)
- [ ] **Caching**: Cache raw data 15min, indicators 1h, regime 4h
- [ ] **Logging**: Structured logs with regime changes flagged
- [ ] **Timezone**: All timestamps UTC, display in user TZ
- [ ] **Rate Limits**: yfinance retry with exponential backoff
- [ ] **News Dedupe**: Content-hash based across all symbols
- [ ] **Config-Driven**: All thresholds in JSON, no magic numbers
- [ ] **Test Coverage**: >80% on detection logic
- [ ] **Backtested**: 3+ years historical validation

---

## 13. QUICK START FOR LOCAL AGENT

```bash
# 1. Install deps
pip install yfinance pandas-ta scipy feedparser

# 2. Create module files (in order)
#    investment_engine/research/news_engine.py
#    investment_engine/research/technical_analysis.py
#    investment_engine/research/peak_valley.py
#    investment_engine/research/market_regime.py
#    investment_engine/reporting/regime_report.py

# 3. Update settings.py + portfolio_config.json

# 4. Hook into main.py

# 5. Run test
python -m pytest tests/test_market_regime.py -v

# 6. Generate sample report
python -c "
from investment_engine.research.market_regime import EXI2RegimeAnalyzer
from investment_engine.config.settings import EngineSettings
s = EngineSettings.from_defaults()
a = EXI2RegimeAnalyzer(s)
result = a.analyze()
print(result['regime'], result['confidence'])
"
```

---

## 14. ADVANCED: FINVIZFINANCE INTEGRATION (Quant Science)

### 14.1 Enhanced Fundamentals for EXI2
```python
# In market_regime.py - augment with FinViz
def get_finviz_context(symbol: str = "EXI2") -> dict:
    from finvizfinance.quote import finvizfinance
    from finvizfinance.screener.overview import Overview
    
    try:
        stock = finvizfinance(symbol)
        return {
            "fundamentals": stock.ticker_fundament(),
            "description": stock.ticker_description(),
            "news": stock.ticker_news().head(10).to_dict("records"),
            "insider": stock.ticker_inside_trader().head(5).to_dict("records"),
            "peer_etfs": stock.ticker_peer(),
            "etf_holders": stock.ticker_etf_holders(),
        }
    except Exception as e:
        logger.warning(f"FinViz fetch failed: {e}")
        return {}
```

### 14.2 Sector/Group Comparison
```python
def get_sector_breadth() -> dict:
    """Compare EXI2 vs sector groups via FinViz Group."""
    from finvizfinance.group.overview import Overview as GroupOverview
    
    go = GroupOverview()
    sectors = ["Technology", "Healthcare", "Financial", "Consumer Cyclical", 
               "Industrial", "Energy", "Utilities", "Real Estate"]
    
    results = {}
    for sector in sectors:
        try:
            go.set_filter(filters_dict={"Sector": sector})
            df = go.screener_view()
            results[sector] = {
                "count": len(df),
                "avg_change": df["Change"].astype(float).mean() if "Change" in df else None,
            }
        except Exception:
            pass
    return results
```

---

## 15. TENSORTRADE INTEGRATION (Future - Phase 2)

```python
# research/rl_regime_agent.py - For later implementation
"""
RL Agent trained on regime transitions.
State: [EXI2_multi_tf_features, VIX, DXY, sector_breadth, news_sentiment]
Action: {CASH_TARGET_PCT, TECH_ALLOCATION_MULTIPLIER}
Reward: Portfolio return - lambda * max_drawdown
"""
```

---

## 16. MONITORING & ALERTING

```python
# Add to regime_analyzer.analyze() output
{
    "alerts": [
        {"level": "WARNING", "message": "EXI2 broke below 20DMA on daily"},
        {"level": "INFO", "message": "RSI divergence detected on weekly"},
    ],
    "regime_change": {
        "previous": "NEUTRAL",
        "current": "DECLINING",
        "bars_since_change": 3,
    }
}
```

---

This specification provides everything needed for a local agent to implement a **production-robust, multi-timeframe, news-aware, report-generating EXI2 regime detection system** that solves the 48h news bug, detects peaks/valleys across 6 timeframes, and outputs actionable portfolio guidance.