# Experimental Backtest Module — Advisory Research Only

> **Warning: Passing a backtest does not prove future profitability. Past simulated performance is not indicative of future results. This module is for research and education only, not financial advice or automated trading.**

## Purpose and Scope

- Isolated Python-only experimental module under `experimental/backtest/`.
- **Does not** call Trading 212 APIs, does not use secrets (`api.env`), does not place orders, does not access live market data (`yfinance`), does not invoke LLMs.
- Consumes only local `CSV`/`parquet` OHLCV with explicit dated FX conversion.
- Implements deterministic technical analysis (SMA, EMA, Wilder RMA/RSI/ATR/ADX, MACD, Bollinger, Supertrend, OBV, VWAP) and a single strategy `tech_pie_pullback_v1` for walk-forward/out-of-sample evaluation vs buy-and-hold.
- Phase 2 adds reproducible comparison runner: equal-weight buy-and-hold vs periodic-rebalance vs strategy on identical dates/costs.

## Data Schema

Input CSV/parquet columns (required):
```
date,symbol,open,high,low,close,volume,currency,earnings_date
```
- `date`: ISO-8601 UTC (`2024-01-02T00:00:00Z`), normalized to UTC, sorted by `symbol,date`.
- `symbol`: upper-cased ticker (e.g., `AAPL`, `EGTL`).
- `open,high,low,close`: quoted in `currency` units (GBX is pence).
- `volume`: integer, `>=0`.
- `currency`: `EUR`, `USD`, `GBP`, `GBX`.
- `earnings_date`: ISO UTC or empty.

Output normalized DataFrame adds:
```
open_eur,high_eur,low_eur,close_eur
```
plus metadata `data_hash` (SHA256 of raw file bytes) and `fx_hash`.

Duplicate `symbol/date` rows, missing required values, or silent forward-filling are **rejected**.

## Currency Normalization

- All conversions happen at **input boundary** `experimental/backtest/io.py:load_ohlcv`.
- FX rates come from dated local fixture `experimental/backtest/config/fx_rates.json` (`as_of: 2026-08-27`, `rates: {EUR:1, USD:0.922, GBP:1.183}`).
- **GBX → GBP → EUR**: `price_gbp = price_gbx / 100` exactly once, then `price_eur = price_gbp * GBP_rate`. `GBP` and `USD` use their direct rate, `EUR` passthrough.
- No hidden static conversion; changing rates requires updating the dated JSON (hash tracked).
- Example: `EGTL 250 GBX → 2.50 GBP → 2.9575 EUR` at `GBP 1.183`.
- Persisted hashes: `data_hash` and `fx_hash` (and `config_hash` of strategy JSON) are stored in backtest result for reproducibility.

## No Look-Ahead Execution Convention

- Features/indicators are causal: `value[t]` uses only `data[<=t]` (no `.shift(-1)`).
- Signal generated at **close[t]** (`TechPiePullbackV1.generate_signals`) is executed at **open[t+1]** in `engine.py`.
- Engine explicitly shifts: `pending_entries[symbol] = close_date` → fill at next `open`. Weekly gate uses prior completed weekly bar (`gate_2w_shifted`).
- Tests `experimental/tests/test_no_lookahead.py` prove appending an extreme future bar does not change earlier signals, and `test_fill_on_next_open` asserts fill price is `open[t+1]` not `close[t]`.
- Benchmarks also respect next-open execution for rebalances.

## Configuration Format

Single JSON `experimental/backtest/config/tech_pie_pullback_v1.json`:
```json
{
  "start_equity_eur": 10000,
  "costs": {"commission_bps":10,"slippage_bps":5,"fx_spread_bps":10,"min_ticket_eur":1},
  "risk": {"max_alloc_per_instrument_pct":8,"max_gross_exposure_pct":80,"min_cash_reserve_pct":20,"max_positions":10},
  "weekly_trend_gate": {"sma50_period":50,"sma200_period":200,"rsi_min":45,"rsi_max":70,"adx_min":18,"require_weeks":2},
  "daily_entry": {"rsi_min":35,"rsi_max":50,"dist_sma20_min_pct":-8,"dist_sma20_max_pct":-2,"volume_ratio_min":0.8,"earnings_blackout_before_bdays":5,"earnings_blackout_after_bdays":2},
  "exits": {"hard_stop_pct":7,"time_stop_bars":20,"weekly_break_enabled":true}
}
```
- **Weekly gate**: `weekly close > SMA50_W && SMA50_W > SMA200_W && RSI14_W in [45,70] && ADX14_W>=18` for 2 consecutive completed weeks.
- **Daily entry**: `RSI14 in [35,50] && dist_to_SMA20 in [-8%,-2%] && close>SMA200 && volume_ratio>=0.8 && not in earnings blackout`.
- **Exits Phase 1 only**: hard stop 7% below fill, weekly trend break, time stop 20 trading days. No ATR trailing, no take-profit, no partials.
- All thresholds centralized; change via JSON, not code.

Phase 2 comparison config `experimental/backtest/config/phase2_comparison.json` drives reproducible runs:
```json
{
  "strategy_config_path": "experimental/backtest/config/tech_pie_pullback_v1.json",
  "data_input_path": "experimental/tests/fixtures/sample_ohlcv.csv",
  "fx_config_path": "experimental/backtest/config/fx_rates.json",
  "start_date": "2020-01-01", "end_date": "2024-12-31",
  "initial_equity": 10000,
  "universe": null,
  "benchmarks": {"buy_and_hold": {"enabled": true}, "periodic_rebalance": {"enabled": true, "frequency": "monthly"}},
  "costs": {"commission_bps":10,"slippage_bps":5,"fx_spread_bps":10,"min_ticket_eur":1},
  "minimum_history_required_per_symbol": 252,
  "minimum_eligible_symbols": 5
}
```

## How to Run Tests

From repo root `AsistantV5/`:

```bash
# Existing project tests
pytest tests/test_market_regime.py -v

# Experimental isolated tests (Phase 1 + Phase 2)
pytest experimental/tests/ -v

# Or both
pytest tests/test_market_regime.py experimental/tests/ -v

# Phase 2 benchmarks/compare only
pytest experimental/tests/test_benchmarks.py experimental/tests/test_compare.py -v
```

Experimental tests are independent and do not import `trading212_*`, `providers`, or `yfinance`.

## Metrics: In-Sample vs Out-of-Sample

- Current Phase 1 metrics are **computed on whatever data you feed** `engine.run()`. If you pass the full history, metrics are **in-sample** (optimistic, for debugging only).
- For **out-of-sample** evaluation, perform walk-forward externally (e.g., `walkforward.py` future phase): train on `t0..tn`, test on `tn+1..tm`, step forward, concatenate OOS equity; metrics reported only on OOS folds.
- Phase 2 comparison runner reports metrics on the same aligned period for strategy and benchmarks; no walk-forward yet – a single period is still in-sample for the strategy's fixed parameters. Future Phase will add expanding-window OOS.

## Metrics Computed (post-cost EUR)

From `experimental/backtest/metrics.py` (annualized with 252 trading days, `rf=0`):
- `start_equity`, `end_equity`, `total_return`, `CAGR`
- `max_drawdown`
- `annualized_volatility`
- `Sharpe` (rf=0) and `Sortino` (target=0)
- `Calmar` = CAGR / MaxDD
- `trade_count`, `win_rate`, `avg_holding_days`
- `turnover` (annualized total traded / avg equity / years)
- `avg_gross_exposure` (avg `market_value/equity`)

All from **post-cost** equity curve; no costs excluded.

## Reproducibility

- Pin `data_hash`, `fx_hash`, `config_hash` per run (engine and compare return them).
- Output files named by `run_id` (deterministic SHA256 of data+fx+strategy+comparison hashes); rerun with same inputs yields identical hashes and byte-identical outputs.
- Advisory-only: do not deploy as live trading.

## Minimum Recommended Dataset Length (Phase 2)

- **252 valid daily bars per eligible symbol** (1 trading year) is the default minimum; shorter series are excluded and reported.
- **5 eligible symbols** default minimum; comparison fails closed if fewer.
- **>20% excluded** of requested universe → fail (e.g., requesting 10 symbols but 3 excluded = 30% → reject; prevents survivorship bias).
- For weekly SMA200 / SMA50 gates, at least **200 weekly bars** (~4 years of weekly data ≈ 1000 daily bars) is ideal; with only 252 daily bars the weekly gate will rarely be true and strategy will have zero trades – check coverage warnings.

## Adjusted vs Unadjusted Price Data

- **Requirement**: provide **adjusted close** (splits and dividends applied) if you intend buy-and-hold comparison to reflect total return.
- **Why**: unadjusted `close` drops on ex-dividend/split dates (e.g., 2:1 split: price halves, quantity doubles). Without adjustment, buy-and-hold market value will show artificial drawdown.
- **Effect**: an unadjusted dataset will understate buy-and-hold CAGR vs a total-return ETF benchmark (e.g., `VWCE`, `EXI2`) that includes distributions.
- **Action**: document `data_input_path` source (adjusted vs unadjusted) in `phase2_comparison.json` notes; future tooling will verify `adjusted` flag.

## Dividends and Splits

- Dividends: if `close` is **unadjusted**, the close drop on ex-div is not offset by cash; buy-and-hold will appear worse than total-return benchmark that reinvests distributions. If `close` is **adjusted**, the price series already compensates and comparison is fair.
- Splits: unadjusted split creates gap >2 business days? No – but causes >50% price jump; validator will not catch it. Use adjusted data; otherwise manually split-adjust quantities before loading.
- This Phase 2 runner does **not** automatically detect splits; it forwards fills missing prices but does not adjust quantities.

## ETF/Stock Total-Return Benchmark Differences

- A **price-return** benchmark (using `close`) ≠ **total-return** (price + distributions). Example: `AAPL` paid ~0.5% quarterly dividend – price-return CAGR ≈ total-return CAGR − dividend yield.
- If you compare strategy (price-only) to an ETF's published total-return NAV, you are comparing different return definitions. For apples-to-apples, either use price-return for both or total-return for both (adjusted close).

## Transaction Costs, FX Rates and Rebalance Assumptions

- **Costs**: `commission_bps=10` (0.10%), `slippage_bps=5` (0.05%), `fx_spread_bps=10` applied per trade at fill (`open[t+1]` with slippage). Buy-and-hold pays entry once; periodic rebalance pays on every rebalance trade.
- **FX**: static dated fixture `fx_rates.json` (`as_of: 2026-08-27`); GBX divided by 100 exactly once before GBP→EUR. Not live FX – for research only.
- **Rebalance**: `monthly` = last trading day of each month, `quarterly` = Mar/Jun/Sep/Dec last trading day; execution at next open after rebalance signal close. Costs on full turnover each rebalance.
- **Cash**: explicit – target 20% reserve for strategy, benchmarks hold residual cash after rounding; gross exposure and % time in cash reported.

## In-Sample vs Out-of-Sample Evaluation

- **In-sample**: parameters chosen using the same period you evaluate (current single-period comparison). Optimistic; may overfit.
- **Out-of-sample (OOS)**: walk-forward – optimize on training window, evaluate on unseen test window, roll forward. Phase 2 currently reports **single-period (in-sample)** metrics; future Phase will add `walkforward.py` expanding window.
- **Warning**: do not optimize `rsi_min`, `adx_min`, `dist_sma20` etc. on the backtest period and then report that same period's returns as evidence. Always hold out ≥30% of data or use walk-forward. A passing backtest is necessary but not sufficient.

## Warning Against Optimizing Parameters on One Backtest Period

- Tuning on full history (`2020-2024`) and reporting that same history's Sharpe/CAGR is **data snooping**. Out-of-sample degradation of 30-70% is typical.
- Use separate validation period or nested cross-validation.
- This module is research-only; historical outperformance does not predict future results.

## Phase 2 Comparison Outputs

`python -m experimental.backtest.compare --config experimental/backtest/config/phase2_comparison.json`
- Reads local files only, no network/broker, never writes outside `experimental/reports/`.
- Prints `OK run_id=...` and exits 0, or `FAIL ...` and exits 1 on validation/coverage failure.
- Artifacts per run:
  `comparison_{run_id}.json`, `comparison_{run_id}_metrics.csv`, `comparison_{run_id}_equity_curves.csv`, `comparison_{run_id}_trades.csv`, `comparison_{run_id}_summary.md`
- JSON includes `run_id`, `generated_at`, `code_version`, hashes, validation report, coverage, metrics, relative metrics, costs, and disclaimer.

## File Map

```
experimental/backtest/
  io.py, validate.py, indicators.py, costs.py, metrics.py, engine.py, benchmarks.py, compare.py
  strategy/base.py, strategy/tech_pie_pullback_v1.py
  config/fx_rates.json, config/tech_pie_pullback_v1.json, config/phase2_comparison.json
  reports/  (generated, gitignored)
experimental/tests/
  test_indicators.py, test_validation.py, test_no_lookahead.py, test_currency_normalization.py, test_costs.py, test_tech_pie_pullback_v1.py
  test_benchmarks.py, test_compare.py
  fixtures/sample_ohlcv.csv
```
