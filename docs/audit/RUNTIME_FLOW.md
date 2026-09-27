# Runtime Flow

> Read-only. Generated 2026-09-24. Traced from `portfolio_ai_assistant.py` + `investment_engine/main.py:run_engine` + `reporting/*` + providers/research docs. No live run performed; flows verified by code reads + passing offline tests.

## Normal portfolio run

```mermaid
flowchart TD
  A[py -3.12 portfolio_ai_assistant.py] --> B[setup_logging: logs/portfolio_<ts>.log + latest_run.log]
  B --> C[setup_debug_layer run_id: reports/debug/run_id/]
  C --> D[EngineSettings.from_mapping portfolio_config.json]
  D --> E[run_engine assets, portfolio_context{config_path,language,run_id}]
  E --> F[T212 fetch: create_integration api.env -> get_account_summary 20s timeout]
  F --> G[raw diag dump: dump_raw_t212_diagnostics -> reports/raw_endpoint_dump_runid.json]
  G --> H[recon guard: FAIL => suppress account P&L downstream]
  H --> I[news parallel 8+3 workers: Strict + Enhanced + macro + Slovak + Reddit + Trump + commodity + analyst -> news_context_.md]
  I --> J[earnings fast: 15 assets, display keys, timeout 3s each]
  J --> K[EXI2 regime: yfinance 5 TF -> TechnicalAnalyzer -> PeakValley -> FinViz -> news -> RegimeResult]
  K --> L[AI recs JSON: regime+T212+positions -> provider -> parse_ai_recommendations]
  L --> M[canonical signals: build_canonical_signals + HOLD defaults for all_positions]
  M --> N[LLM sections: crypto + tech + renewables + PIE eval + discovery WATCH + summary no-Priority-Actions]
  N --> O[unified rows: build_unified_portfolio_rows + earnings radar + snapshot + reconcile + trade safety + priority table + diagnostics CSV/MD]
  O --> P[FAIL sweep if recon FAIL: watchlist-only summary + monitoring REVIEW]
  P --> Q[regime parts: exi2/portfolio/ai/warnings/earnings -> brief_markdown + snapshot_markdown]
  Q --> R[AI context archive: ai_context_ts.md BROKER/EXTERNAL/WATCHLIST]
  R --> S[return result dict: portfolio_rows, monitoring_items, regime_result, decision_news, earnings_7d, ideas, portfolio_names, ...]
  S --> T[contract check 7 keys -> generate_human_brief -> write_human_brief summary/portfolio_brief.md]
  T --> U[render_failed_tickers_md -> write_reports atomic publish + run_manifest.json + archive copies]
  U --> V[save_ai_context_layer copy -> move_debug_outputs -> LM-Studio unload -> exit 0/1/2]
```

Result dict keys (`main.py:1325-1371`): `brief_markdown, snapshot_markdown, t212_data (sanitized), reconciliation, portfolio_rows, monitoring_items, regime_result, decision_news, earnings_7d, ideas, portfolio_names, ai_recommendations, model_info, brief_metadata, context_file, failed_tickers, news_context_path, ...`. Human-brief contract requires 7 of them (`portfolio_ai_assistant.py:168-177`).

## Trading 212 data path

```mermaid
flowchart LR
  E1[/equity/account/cash: total, free, pieCash, blocked, invested, ppl/] --> CASH[CashBalance free/pie/blocked/invested/total]
  E2[/equity/portfolio: ticker, qty, avgPrice, currentPrice, ppl, fxPpl, pieQty, isin/] --> PP[parse_position: GBX/100, currency catalog>override>heuristic legacy, FX live>implied>static]
  PP --> CAT2[instrument catalog t212_instruments.json + KNOWN_ISINS]
  CAT2 --> SNAP[build_account_snapshot: positions + FxAudit + dedupe ISIN-first]
  SNAP --> REC2[reconcile_snapshot: expected=equity-reported, delta=dedup-expected, tol=min 0.1%_2EUR]
  REC2 --> ROWS2[build_unified_portfolio_rows: all_positions authoritative, ticker dedupe, display/company/signal]
  ROWS2 --> REP[render_brief + render_snapshot + portfolio_analysis.json]
```

Critical: `all_positions` (all incl. pie constituents) feeds reconciliation; `positions` (non-pie view) only extends news universe. Pie endpoint totals never summed. `pnl_eur` prefers broker `ppl`. External prices never overwrite broker values.

## Report generation path

```mermaid
flowchart TD
  R1[unified rows + canonical map + technicals + earnings_status] --> R2[compute_reconciliation view]
  R2 --> R3[apply_trade_safety: BUY needs PASS+price+technicals+cap7%+no-avg-down; SELL via risk-rule survives FAIL]
  R3 --> R4[_build_priority_actions_table SELL/BUY first, HOLD only with trigger]
  R4 --> R5[_apply_fail_watchlist_sweep if FAIL: strip deployment + BUY]
  R5 --> R6[RegimeReportGenerator: exi2 + _t212_portfolio single table + _ai_recommendations + _earnings_calendar + _warnings]
  R6 --> R7[documents.render_brief Executive→Monitoring→Earnings→News→Watchlist]
  R6 --> R8[documents.render_snapshot Account + 17-col Holdings + Coverage]
  R6 --> R9[report_structure.generate_human_brief presentation filter -> summary/portfolio_brief.md]
  R6 --> R10[AIContextReportBuilder full archive BROKER/EXTERNAL/WATCHLIST -> ai_context_ts.md]
  R6 --> R11[NewsContextBuilder full corpus -> news_context_.md]
  R6 --> R12[diagnostics: reconciliation_diagnostic.csv/md + raw_endpoint_dump.json -> debug/run_id/]
```

Separation: `documents` (source-of-truth curated) vs `report_structure` human filter vs `AIContextReportBuilder` machine archive vs `debug/` internals. Known duplication: two stance functions + duplicated quality-flags collector.

## AI provider fallback path

```mermaid
flowchart TD
  P1[_model_for_stage: summary->writer_model else decision_model] --> P2[provider.generate prompt, stage, model/max_tokens]
  P2 --> P3{ChainedFallbackProvider loop}
  P3 --> L1[LM Studio 100.101.20.64:1234 + presets]
  L1 -->|error or error-string| L2[llama.cpp 100.125.47.31:11435]
  L2 -->|error| L3[Ollama 100.125.47.31:11434]
  L3 -->|error| G1[Gemini if key]
  G1 -->|429 rate-limit| G1W[skip until retry-in-Ns else rest-of-run]
  G1 -->|error| M1[Mistral if key]
  M1 -->|error| D1[deterministic fallback profile balanced/conservative/aggressive]
  L1 -->|ok| OK[section text]
  L2 -->|ok| OK
  L3 -->|ok| OK
  G1 -->|ok| OK
  M1 -->|ok| OK
  D1 --> OKN[profile text + provider-note one-liner]
  P2 --> CB[circuit breaker: LM/Ollama trip on any fail; Gemini on 429; others never]
  P2 --> PR[pre-probe LM/Ollama /models + /api/tags 2s -> provider-chain unreachable note]
```

Notes: explicit `provider=openrouter/mistral/gemini/opencode_zen/ollama/lmstudio` bypasses chain (single). OpenRouter/Zen never in auto. `context[model]` stripped in chain — locals use ctor models. Only LMStudio honors presets/stage sampling. Missing keys skip cloud links. All failures degrade to deterministic text, never abort the run (except contract/exception → exit 1).

## Technical indicator path

```mermaid
flowchart TD
  T1[yfinance OHLCV 3mo/1d len>=50] --> T2{HAS_PANDAS_TA?}
  T2 -->|yes if installed| T3[pandas-ta path: ta.sma/ema/macd/adx/supertrend/rsi/stoch/cci/willr/bbands/kc/atr/donchian/obv/vwap/mfi/cmf]
  T2 -->|no commented default| T4[manual path: SMA/EMA adjustFalse/MACD/SMA-RSI/BB/ATR-SMA/ADX-simplified/Stoch/OBV/rolling-VWAP/CCI/WillR/Donchian/MFI/CMF/static-Supertrend]
  T3 --> T5[FinVizEnrichment US-only + kill-switch]
  T4 --> T5
  T5 --> T6[compute_derived_metrics: DIST_SMA, CROSS, RSI_OB/OS, BB_SQUEEZE]
  T6 --> T7[market_data lightweight dict price/RSI/SMA/MACD/BB/S-R/ATR adjustTrue]
  T7 --> T8[unified rows technicals + monitoring triggers + PIE eval]
  T8 --> T9[EXI2 regime classify PEAK_HOLD/DECLINING/MUST_BUY/NEUTRAL + actions]
```

Divergences: `adjust True/False`, SMA vs Wilder RSI/ATR/ADX, static vs stateful Supertrend, rolling vs session VWAP, Keltner only in ta-path. Experimental `backtest/indicators` is the correct-reference implementation (Wilder/stateful/strict warmup) — do not compare numbers directly.

## News / research path

```mermaid
flowchart TD
  N1[assets + pie slices + T212 standalone + macro SPY/BTC/TRUMP] --> N2[StrictNewsFetcher Google RSS 48h tier/dedupe 6h-cache]
  N1 --> N3[EnhancedNewsFetcher Slovak/Reddit/Trump/commodity/analyst no-dedupe]
  N2 --> N4[Slovak RSS + Reddit JSON 5 assets + Trump 6cat + commodity Au/Ag/Li/U/BTC/ETH/SOL]
  N4 --> N5[news_by_symbol + all_items + trump + commodity + analyst + slovak + reddit]
  N5 --> N6[NewsContextBuilder full .md corpus]
  N5 --> N7[curated for prompts: headlines_block max3/sym tier-first + sentiment_line + filter_decision_news 72h/rel30]
  N5 --> N8[DiscoveryEngine validated ideas max20 + inline discovery max3 WATCH]
  N5 --> N9[headlines + earnings radar + regime news_sentiment]
```

Freshness: Strict 48h + decision-filter 72h (looser) + Enhanced HTML-`now()` bug + WebResearcher no-age (unwired) + `market_data` 2d/2-items. X/StockTwits/medical/copper-oil gaps documented in PROJECT_AUDIT.

## Experimental backtest path

```mermaid
flowchart TD
  B1[local CSV date,symbol,open,high,low,close,volume,currency,earnings_date] --> B2[validate_dataframe fail-closed: OHLC sign, positive, nonneg vol, dupes, NaN, gaps>2bdays, stale]
  B2 --> B3[load_ohlcv: GBX/100 once -> GBP_rate -> EUR + data_hash/fx_hash]
  B3 --> B4[indicators causal Wilder/stateful + warmup NaN]
  B4 --> B5[TechPiePullbackV1: weekly gate 2 completed weeks + daily RSI/dist/SMA200/vol/earnings-blackout -> pending close]
  B5 --> B6[engine: fill pending at next open + slippage + 10/5bps costs + 7% hard-stop + weekly-break + 20-bar time-stop]
  B6 --> B7[benchmarks: buy-hold + periodic rebalance same dates/costs]
  B7 --> B8[compare: coverage gates 252 bars/5 symbols/20% excluded + hashes + run_id + artifacts in experimental/reports/ only]
  B8 --> B9[metrics post-cost: CAGR/Sharpe/Sortino/Calmar/MDD/vol/turnover/exposure]
```

Guarantees: no look-ahead (future-bar tests), no network/broker/LLM (import + containment tests), deterministic hashes/byte-identical outputs, fail-closed validation/coverage. Single-period = in-sample; walk-forward future.
