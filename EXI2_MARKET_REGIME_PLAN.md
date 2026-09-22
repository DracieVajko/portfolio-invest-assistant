# EXI2 Market Regime Detection & Investment Timing Plan

## Overview
Implement a systematic approach to track **EXI2** (iShares Dow Jones Global Titans 50 UCITS ETF) as a **market regime indicator** for timing tech/market investments.

**Core Logic:**
- **EXI2 at peak + holding** → AVOID new tech investments (market overheated)
- **EXI2 declining** → PREPARE to invest in tech & broad market
- **EXI2 at "must buy" levels** → AGGRESSIVE accumulation in tech/broad market

---

## 1. Architecture Components

### 1.1 Data Layer (`investment_engine/research/market_regime.py`)
```python
# New module for EXI2 regime detection
class EXI2RegimeDetector:
    - fetch_exi2_price_history()          # Yahoo Finance / TradingView / FinViz
    - calculate_technical_indicators()    # MA, RSI, MACD, Bollinger Bands
    - detect_peak_holding_pattern()       # Price up → consolidation
    - detect_decline_phase()              # Breakdown from consolidation
    - identify_must_buy_zones()           # Support levels, oversold conditions
    - get_regime_signal()                 # Enum: PEAK_HOLD | DECLINING | MUST_BUY | NEUTRAL
```

### 1.2 Signal Types
| Signal | Condition | Action |
|--------|-----------|--------|
| `PEAK_HOLD` | EXI2 > 20D MA, RSI > 65, price flat 5-10 days | **AVOID** new tech positions |
| `DECLINING` | EXI2 < 20D MA, MACD bearish cross, lower highs | **PREPARE** watchlists, raise cash |
| `MUST_BUY` | EXI2 at key support, RSI < 35, volume spike | **AGGRESSIVE BUY** tech/broad market |
| `NEUTRAL` | None of above | Continue DCA per plan |

### 1.3 Integration Points
- **Portfolio Config**: Add `market_regime` section with EXI2 settings
- **Main Engine** (`main.py`): Inject regime signal into decision prompts
- **Pipeline** (`pipeline/engine.py`): Use regime as portfolio-level filter
- **Report Output**: Add "Market Regime" section to markdown report

---

## 2. Technical Implementation

### 2.1 Dependencies
```bash
# Add to requirements.txt
finvizfinance>=1.0.0          # From Quant Science thread - FinViz data
yfinance>=0.2.0               # Already used - EXI2.DE price data
pandas-ta>=0.3.14b0           # Technical indicators (optional, can use manual)
ta-lib                        # Alternative for production (requires compile)
```

### 2.2 EXI2 Data Sources (Priority Order)
| Source | Symbol | Pros | Cons |
|--------|--------|------|------|
| **Yahoo Finance** | `EXI2.DE` | Free, reliable, Python native | Delayed 15min |
| **FinViz (finvizfinance)** | `EXI2` | Fundamentals, news, insider | No intraday, rate limited |
| **TradingView** | `XETR:EXI2` | Charts, alerts | No official API |
| **iShares Official** | DE0005933956 | Authoritative NAV | No API, manual |

### 2.3 Peak Detection Algorithm
```python
def detect_peak_holding(prices: pd.Series, window: int = 20) -> bool:
    """
    EXI2 PEAK = Price rose >5% over 20D, now consolidating ±2% for 5+ days
    """
    # 1. Uptrend confirmation
    pct_change_20d = (prices[-1] / prices[-window] - 1) * 100
    if pct_change_20d < 5: return False
    
    # 2. Consolidation detection (last 5-10 days)
    recent = prices[-10:]
    consolidation_range = (recent.max() - recent.min()) / recent.mean() * 100
    if consolidation_range > 2.5: return False  # Too volatile
    
    # 3. Above key MAs
    if prices[-1] < prices.rolling(20).mean().iloc[-1]: return False
    if prices[-1] < prices.rolling(50).mean().iloc[-1]: return False
    
    # 4. RSI in "greed" zone but not extreme
    rsi = calculate_rsi(prices, 14)
    if not (60 <= rsi <= 80): return False
    
    return True
```

### 2.4 Decline Detection Algorithm
```python
def detect_decline_phase(prices: pd.Series) -> bool:
    """
    EXI2 DECLINE = Break below 20D MA + lower high + MACD bearish
    """
    ma20 = prices.rolling(20).mean().iloc[-1]
    ma50 = prices.rolling(50).mean().iloc[-1]
    
    # Price below 20D MA
    if prices[-1] > ma20: return False
    
    # Lower high pattern (last 3 peaks)
    peaks = find_peaks(prices[-30:])
    if len(peaks) >= 2 and peaks[-1] < peaks[-2]: pass  # Confirmed
    
    # MACD bearish crossover
    macd, signal = calculate_macd(prices)
    if macd[-1] > signal[-1]: return False  # Still bullish
    
    # Volume confirmation (optional via FinViz)
    return True
```

### 2.5 Must-Buy Zone Detection
```python
def identify_must_buy_zones(prices: pd.Series) -> Dict:
    """
    MUST BUY = EXI2 at major support + oversold + volume spike
    """
    # Key support levels (Fibonacci, prior lows, 200D MA)
    support_200 = prices.rolling(200).mean().iloc[-1]
    support_52w_low = prices[-252:].min()
    
    # Current position
    current = prices[-1]
    dist_to_200 = (current - support_200) / support_200 * 100
    dist_to_52w = (current - support_52w_low) / support_52w_low * 100
    
    # Oversold
    rsi = calculate_rsi(prices, 14)
    
    signals = {}
    if dist_to_200 <= 2 and rsi < 35:
        signals["200DMA_TOUCH"] = True
    if dist_to_52w <= 5 and rsi < 30:
        signals["52W_LOW_TEST"] = True
    if rsi < 25:
        signals["EXTREME_OVERSOLD"] = True
    
    return signals
```

---

## 3. Integration with FinVizFinance (Quant Science Approach)

### 3.1 Install & Setup
```bash
pip install finvizfinance
```

### 3.2 Usage for EXI2
```python
from finvizfinance.quote import finvizfinance
from finvizfinance.screener.overview import Overview

# EXI2 fundamentals & technicals
exi2 = finvizfinance('EXI2')  # May need US ticker equivalent
fundamentals = exi2.ticker_fundament()
technicals = exi2.ticker_charts()  # Save chart image
news = exi2.ticker_news()
insider = exi2.ticker_inside_trader()

# Screener: Find similar global titan ETFs
overview = Overview()
overview.set_filter({'Index': 'Global', 'Asset Class': 'ETF'})
global_etfs = overview.screener_view()
```

### 3.3 FinViz Enhancement for Regime Detection
- **Group Performance**: Compare EXI2 vs sector groups (Technology, Healthcare, etc.)
- **Insider Activity**: Unusual selling in top holdings = warning
- **News Sentiment**: Negative headlines on mega-caps = regime shift signal

---

## 4. Portfolio Config Extensions

### 4.1 Add to `portfolio_config.json`
```json
{
  "settings": {
    "market_regime": {
      "enabled": true,
      "exi2_symbol": "EXI2.DE",
      "lookback_days": 252,
      "peak_threshold_pct": 5.0,
      "consolidation_days": 5,
      "consolidation_range_pct": 2.5,
      "decline_ma_period": 20,
      "oversold_rsi": 35,
      "extreme_oversold_rsi": 25,
      "support_200dma_tolerance_pct": 2.0,
      "update_frequency_hours": 4
    }
  }
}
```

### 4.2 Asset Classification for Regime Actions
```json
{
  "regime_actions": {
    "PEAK_HOLD": {
      "tech_pie": "REDUCE_NEW_ENTRY",
      "crypto": "REDUCE_NEW_ENTRY",
      "long_run_dca": "CONTINUE",
      "cash_target_pct": 15
    },
    "DECLINING": {
      "tech_pie": "PREPARE_WATCHLIST",
      "crypto": "PREPARE_WATCHLIST",
      "long_run_dca": "CONTINUE",
      "cash_target_pct": 25
    },
    "MUST_BUY": {
      "tech_pie": "AGGRESSIVE_ACCUMULATE",
      "crypto": "SELECTIVE_BUY",
      "long_run_dca": "ACCELERATE",
      "cash_target_pct": 5
    },
    "NEUTRAL": {
      "tech_pie": "NORMAL_DCA",
      "crypto": "NORMAL_DCA",
      "long_run_dca": "NORMAL_DCA",
      "cash_target_pct": 10
    }
  }
}
```

---

## 5. Main Engine Integration (`main.py`)

### 5.1 Add Regime Detection to Report Generation
```python
# In run_engine() - add after line 85 (provider creation)
from investment_engine.research.market_regime import EXI2RegimeDetector

regime_detector = EXI2RegimeDetector(settings)
regime_signal = regime_detector.get_regime_signal()
regime_details = regime_detector.get_detailed_analysis()

# Inject into decision prompts
for title, positions in (("Crypto — decision only", crypto), ...):
    # Add regime context to prompt
    regime_context = f"""
MARKET REGIME (EXI2): {regime_signal.value}
- EXI2 Price: {regime_details['current_price']:.2f}
- 20D MA: {regime_details['ma20']:.2f} | 50D MA: {regime_details['ma50']:.2f} | 200D MA: {regime_details['ma200']:.2f}
- RSI(14): {regime_details['rsi']:.1f}
- Regime Action: {regime_details['action']}
"""
    # Append to _decision_prompt()
```

### 5.2 Add Regime Section to Markdown Report
```python
# After summary_section (line 133)
regime_md = f"""## Market Regime (EXI2 Global Titans 50)
**Signal:** {regime_signal.value} | **Price:** €{regime_details['current_price']:.2f} | **RSI:** {regime_details['rsi']:.1f}

**Interpretation:** {regime_details['interpretation']}
**Portfolio Action:** {regime_details['action']}
**Key Levels:** Support €{regime_details['support']:.2f} | Resistance €{regime_details['resistance']:.2f}
"""
markdown = "\n\n".join([summary, regime_md, *decision_sections, ...])
```

---

## 6. Advanced: TensorTrade Integration (From Quant Science Thread 1)

### 6.1 Concept
The first Twitter thread introduces **TensorTrade** - RL framework for trading. We can use it to:
- Train an agent on EXI2 regime transitions
- Learn optimal entry/exit timing for tech pie
- Backtest regime-based strategies

### 6.2 Implementation Path (Future Enhancement)
```python
# Phase 2: RL-based regime optimization
import tensortrade as tt
from tensortrade.environments import TradingEnvironment
from tensortrade.agents import DQNAgent

# Environment: EXI2 price + tech pie components
# State: [EXI2_price, EXI2_RSI, EXI2_MA_ratio, tech_pie_momentum, vix, ...]
# Actions: [HOLD, REDUCE_TECH, ACCUMULATE_TECH, AGGRESSIVE_BUY_TECH]
# Reward: Portfolio return vs benchmark with regime-aware penalties
```

### 6.3 Quick Win: Rule-Based First, RL Later
Start with deterministic rules (above), collect labeled data, then train RL agent.

---

## 7. Implementation Phases

### Phase 1: Core Detection (Week 1-2)
- [ ] Create `investment_engine/research/market_regime.py`
- [ ] Implement EXI2 data fetching (yfinance + finvizfinance)
- [ ] Implement 4 detection algorithms
- [ ] Add config section to `portfolio_config.json`
- [ ] Unit tests with historical data

### Phase 2: Integration (Week 2-3)
- [ ] Hook into `main.py` run_engine()
- [ ] Inject regime signal into all decision prompts
- [ ] Add regime section to markdown report
- [ ] Add regime-based portfolio action rules

### Phase 3: FinVizFinance Enhancement (Week 3)
- [ ] Integrate finvizfinance for EXI2 fundamentals
- [ ] Add group/sector comparison (EXI2 vs Tech sector)
- [ ] Add insider trading alerts for top 10 holdings
- [ ] Add news sentiment scoring

### Phase 4: Advanced Features (Week 4+)
- [ ] Multi-timeframe confirmation (weekly + daily)
- [ ] VIX correlation filter
- [ ] Backtesting framework
- [ ] TensorTrade RL agent (experimental)
- [ ] Alert system (Telegram/Email on regime change)

---

## 8. Testing Strategy

### 8.1 Historical Backtest Scenarios
| Period | EXI2 Behavior | Expected Signal | Tech Performance |
|--------|---------------|-----------------|------------------|
| Nov 2021 - Jan 2022 | Peak + hold | PEAK_HOLD | -25% (avoided) |
| Jan 2022 - Oct 2022 | Steady decline | DECLINING → MUST_BUY | -30% then +40% |
| Oct 2022 - Jul 2023 | Bottom + rally | MUST_BUY → NEUTRAL | +50% (captured) |
| Jul 2023 - Mar 2024 | Peak + hold | PEAK_HOLD | +20% (missed some) |

### 8.2 Paper Trading Validation
- Run detection daily for 30 days without acting
- Compare signals vs actual subsequent returns
- Tune thresholds based on false positive/negative rates

---

## 9. Risk Management

### 9.1 False Signal Mitigation
- **Require 2+ confirmations** (e.g., MA break + RSI + volume)
- **Minimum holding period** for regime: 3 days before acting
- **Whitelist override**: If individual stock has catalyst (earnings, FDA, etc.)

### 9.2 Position Sizing by Regime
| Regime | Max New Tech Allocation | DCA Multiplier |
|--------|------------------------|----------------|
| PEAK_HOLD | 0% | 0.5x |
| DECLINING | 50% | 1.0x |
| MUST_BUY | 100% | 2.0x |
| NEUTRAL | 100% | 1.0x |

---

## 10. File Changes Summary

### New Files
```
investment_engine/research/market_regime.py      # Core detection logic
investment_engine/research/__init__.py           # Export (if needed)
tests/test_market_regime.py                      # Unit tests
```

### Modified Files
```
portfolio_config.json                            # Add market_regime settings
investment_engine/main.py                        # Integrate regime detection
investment_engine/pipeline/engine.py             # Optional: regime-aware ranking
requirements.txt                                 # Add finvizfinance, pandas-ta
```

---

## 11. Quick Start Commands

```bash
# 1. Install dependencies
pip install finvizfinance pandas-ta yfinance

# 2. Test EXI2 data fetch
python -c "
import yfinance as yf
exi2 = yf.Ticker('EXI2.DE')
hist = exi2.history(period='1y')
print(f'Current: {hist[\"Close\"].iloc[-1]:.2f}')
print(f'20D MA: {hist[\"Close\"].rolling(20).mean().iloc[-1]:.2f}')
print(f'RSI: {calculate_rsi(hist[\"Close\"]).iloc[-1]:.1f}')
"

# 3. Test finvizfinance
python -c "
from finvizfinance.quote import finvizfinance
stock = finvizfinance('EXI2')
print(stock.ticker_fundament())
"
```

---

## 12. Success Metrics

| Metric | Target |
|--------|--------|
| Regime detection accuracy (backtest) | > 70% |
| False PEAK_HOLD signals (missed upside) | < 20% |
| False MUST_BUY signals (catching falling knife) | < 15% |
| Portfolio CAGR improvement vs static DCA | +2-5% annually |
| Max drawdown reduction | > 15% vs buy-and-hold |

---

## 13. References & Inspiration

1. **Quant Science Thread 1** (TensorTrade): https://x.com/quantscience_/status/2087932975446900821
   - RL framework for algorithmic trading
   - Reward-optimized agents

2. **Quant Science Thread 2** (FinVizFinance): https://x.com/quantscience_/status/2088962256243503110
   - Free FinViz data in Python
   - Stock quotes, news, insider, screener

3. **FinVizFinance GitHub**: https://github.com/lit26/finvizfinance
   - 1.6k stars, active maintenance
   - Quote, Screener, Group, News, Insider, Forex, Crypto

4. **EXI2 ETF**: iShares Dow Jones Global Titans 50 UCITS ETF (DE)
   - Tracks 50 global mega-caps
   - Heavy tech weighting (AAPL, MSFT, NVDA, etc.)
   - Excellent market breadth proxy