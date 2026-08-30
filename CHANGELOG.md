# Changelog

All notable changes to this project are documented in this file.

The format is based on Keep a Changelog and adheres to Semantic Versioning for tagged releases.

## [Unreleased]

- Active work in progress on the modular line (`main`, `v1.0.0-beta.1`). Breaking changes, configuration and report schema evolution are possible. No stable API is promised.

## [1.0.0-beta.1] - Beta / WIP

Experimental modular beta. Not production-ready. For development and research only; the latest stable monolithic release is [`v0.6.0`](../../tree/v0.6.0).

### Added
- Modular `investment_engine/` package: `config/settings.py`, `main.py` (`run_engine`), `providers/` (factory/fallback for Ollama / LM Studio / OpenRouter), `research/` (market data, market regime, news engine, peak/valley, technical analysis), `portfolio/` (exposure, pie metadata, sidecar), `reporting/`, `schemas/`, `scoring/`, `pipeline/`, `prompts/` and `memory/`.
- Multi-timeframe market-regime analysis for EXI2.DE and other assets: SMA/EMA/MACD/ADX/Supertrend/RSI/CCI/ATR/OBV/VWAP, Fibonacci levels, peak/valley detection, daily/weekly/monthly/15m/1h via `yfinance`.
- Pie-level exposure aggregation (`portfolio/exposure.py`, `PIEs/config/*.json`, `PIEs/example_*.csv` — real `PIEs/*.csv` remain gitignored).
- Isolated experimental backtest (`experimental/backtest/`): deterministic indicators, `tech_pie_pullback_v1` strategy, next-open execution, dated FX configs (`experimental/backtest/config/*.json`), `data_hash`/`fx_hash`/`config_hash` tracking. No Trading 212, LLM or network calls in the backtest core.
- Tests for the new domains: `tests/test_market_regime.py`, `tests/test_pie_exposure.py`, `tests/test_pie_hardening.py` and `experimental/tests/` (7 backtest tests, including `test_no_lookahead.py`).

### Changed
- Monolithic `portfolio_ai_assistant.py` reduced to a thin wrapper delegating to `investment_engine`.

### Limitations
- Beta / WIP: may contain bugs, incomplete features, incorrect calculations, data-provider edge cases, configuration incompatibilities and breaking changes.
- Experimental backtest is research only and does not imply future profitability; requires independent validation for look-ahead bias and costs.

See [v1.0.0-beta.1](docs/releases/v1.0.0-beta.1.md) for the full beta note.

## [0.6.0] - Current stable monolithic release

Stable monolithic workflow used for regular local portfolio monitoring. This is the latest stable release; `main` (`v1.0.0-beta.1`) is the beta rewrite.

- Maintenance-oriented continuation of the monolithic line, sanitized for staging/public use (based on `Zalohy/portfolio_ai_assistant_V4_5`).
- Deterministic `portfolio_ai_assistant.py` with Trading 212 read-only import, multi-source news (`yfinance`, `DDGS`), and Markdown/JSON reporting.
- Sanitized `api.env.example` and `portfolio_config.example.json` with localhost placeholders and demo assets (`AAPL`, `MSFT`, `BTC-USD`).
- `PIEs/example_*.csv` and `PIEs/README.md`; real `PIEs/*.csv` and `investment_engine/memory/memory.json` remain gitignored.
- `.gitignore` covering secrets, runtime output (`data/`, `logs/`, `reports/`), caches (`__pycache__/`, `.pytest_cache/`, `*.db`) and temporary patches (`fix_*.py`).
- No full automated test suite; outputs must be independently validated (prices, signals, LLM tidy-ups). Live providers may be unavailable or rate-limited.

See [v0.6.0](docs/releases/v0.6.0.md) for the full release note.

## [0.5.0] - Historical

Historical feature-rich monolithic milestone with multi-source research and portfolio auto-discovery. Preserved snapshot; not the current stable release.

- Monolithic `portfolio_ai_assistant.py` with multi-source research (`yfinance`, `DDGS`, `finvizfinance`) and portfolio auto-discovery.
- Trading 212 read-only integration (`GET` only, no order placement).
- Same safe template pattern: `api.env.example`, `portfolio_config.example.json`, gitignored local data.

Reference only. See [v0.5.0](docs/releases/v0.5.0.md).

## [0.1.0] - Historical

Initial historical monolithic portfolio tracker baseline. Earliest preserved snapshot.

- Single-script portfolio tracker with basic Trading 212 read-only import and report generation.
- Safe templates: `api.env.example`, `portfolio_config.example.json`; local `api.env` / `portfolio_config.json` gitignored.
- No claim of completeness or production readiness; reference baseline only.

See [v0.1.0](docs/releases/v0.1.0.md).
