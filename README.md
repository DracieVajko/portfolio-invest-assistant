> [!WARNING]
> **Current `main` is a beta / work-in-progress modular rewrite.**
> The latest stable monolithic release is [`v0.6.0`](../../tree/v0.6.0).
>
> This beta may contain bugs, incomplete features, incorrect calculations, data-provider edge cases, configuration incompatibilities, and breaking changes. Use it only for development and research. Do not rely on it for investment decisions.

# Portfolio AI Assistant

> **Read-only research and advisory tool — not financial advice and not an automated trading system. It does not create, modify or cancel broker orders.**

Local portfolio monitoring and reporting assistant. It collects market data (yfinance), news (DDGS / DuckDuckGo), sector breadth (finvizfinance), and optional read-only Trading 212 positions, calculates deterministic BUY / SELL / WATCH signals with data-quality scoring, and renders Markdown + JSON reports. An optional local LLM layer (Ollama / LM Studio / OpenRouter) can tidy results, but all decisions are traceable without AI.

## Status

- **Current `main` — `v1.0.0-beta.1` (Beta / WIP):** Modular rewrite with market-regime analysis and experimental backtesting. Not production-ready. May contain bugs, incomplete features, incorrect calculations, data-provider edge cases, configuration incompatibilities and breaking changes.
- **Latest stable — `v0.6.0` (Current stable monolithic release):** Maintenance-oriented monolithic workflow used for regular local portfolio monitoring. No full automated test suite; all outputs must be independently validated. Recommended for routine use. See [`v0.6.0`](../../tree/v0.6.0).

> If you need a stable local workflow today, checkout [`v0.6.0`](../../tree/v0.6.0). Use `main` only for development and research.

## Version history

| Version | Tag | Status | Description | Release notes |
|---|---|---|---|---|
| v0.1.0 | [`v0.1.0`](../../tree/v0.1.0) | Historical baseline | Initial monolithic portfolio tracker baseline. | [v0.1.0](docs/releases/v0.1.0.md) |
| v0.5.0 | [`v0.5.0`](../../tree/v0.5.0) | Historical milestone | Feature-rich monolithic milestone with multi-source research and portfolio auto-discovery. | [v0.5.0](docs/releases/v0.5.0.md) |
| v0.6.0 | [`v0.6.0`](../../tree/v0.6.0) | **Current stable monolithic release** | Stable monolithic workflow used for regular local monitoring. No full automated suite; independent validation required. | [v0.6.0](docs/releases/v0.6.0.md) |
| v1.0.0-beta.1 | [`v1.0.0-beta.1`](../../tree/v1.0.0-beta.1) | **Beta / WIP** | Experimental modular rewrite with market regime analysis and backtesting. Not production-ready. | [v1.0.0-beta.1](docs/releases/v1.0.0-beta.1.md) |

See [CHANGELOG.md](CHANGELOG.md) for a chronological summary and [docs/releases/](docs/releases/) for per-version notes.

## What this beta does

- All features from `v0.5.0` plus modular architecture:
  - **Modular investment engine** (`investment_engine/`, ~30 Python files): `config/settings.py` (`EngineSettings.from_mapping`), `main.py` (`run_engine`), `providers/` (factory/fallback for Ollama / LM Studio / OpenRouter), `research/`, `portfolio/`, `reporting/`, `schemas/`, `scoring/`, `prompts/`
  - **Multi-timeframe market regime** (`research/market_regime.py`, `technical_analysis.py`, `peak_valley.py`): SMA/EMA/MACD/ADX/Supertrend/RSI/CCI/ATR/OBV/VWAP, Fibonacci levels, peak/valley detection, daily/weekly/monthly/15m/1h via `yfinance`
  - **Pie-level exposure** (`portfolio/exposure.py`, `pie_metadata.py`, `sidecar.py`, `PIEs/config/*.json`, `PIEs/example_*.csv`): read-only aggregation of pie holdings; real PIE CSV exports are gitignored
  - **Parallel news** (`research/news_engine.py`, 48h window, relevance threshold, `feedparser` + `finvizfinance`)
  - **Provider fallback** (`providers/fallback.py`, `factory.py`): LM Studio → Ollama → OpenRouter with retry
  - **Experimental backtest** (`experimental/backtest/`, isolated, advisory only): deterministic indicators, `tech_pie_pullback_v1` strategy, next-open execution, dated FX configs, no network calls in tests, no secrets — see [Limitations](#known-limitations-and-experimental-backtest)

## Trading 212 — read-only

- **Read-only import only.** The integration performs only `GET` requests (`/api/v0/equity/portfolio`, `/api/v0/equity/account/cash`, history). It never places, modifies or cancels broker orders.
- No automated trading. The tool does not create, modify or cancel orders and must not be used as an automated trading system.
- Credentials are local only (`api.env`, gitignored). See [Safe setup](#safe-local-setup).

## Safe local setup

Never commit secrets. Use the safe templates:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 1. Create secrets file (not tracked)
copy api.env.example api.env
# Edit api.env — keep TRADING212_ENABLED=false for offline testing

# 2. Create portfolio config (not tracked)
copy portfolio_config.example.json portfolio_config.json
# Adjust holdings / ai_backends if needed

# 3. Optional: example PIE exports are already included
dir PIEs
```

- `api.env.example` — placeholder env vars (TRADING212_*, OLLAMA_*, LM_STUDIO_*, OPENROUTER_* with empty keys, localhost endpoints)
- `portfolio_config.example.json` — safe example (demo assets, localhost endpoints)
- You must create local `api.env` and `portfolio_config.json` before running. The `*.example` files are safe templates.

## Privacy model and gitignored local data

Private and runtime files are gitignored:

- Secrets: `api.env`, `.env`, `portfolio_config.json` (only `*.example` tracked)
- Personal financial data: `PIEs/*.csv` (only `PIEs/example_*.csv` tracked), `investment_engine/memory/memory.json`
- Runtime output: `data/`, `logs/`, `reports/`, `experimental/reports/`
- Caches and DBs: `__pycache__/`, `.pytest_cache/`, `*.db`, `*.sqlite`, `.venv/`
- Temporary patches: `fix_*.py`, `*.bak`, `*.tmp`, `*Conflict*`

Example configs contain only demo assets (e.g. `AAPL`, `MSFT`, `BTC-USD`) — no real holdings, quantities, or P/L. Never paste real `TRADING212_API_KEY`, `TRADING212_API_SECRET`, `TRADING212_ACCOUNT_ID`, holdings, or PIE exports into issues or commits.

## Requirements

- Python 3.12+ (scipy/numpy, optional `pandas-ta` needs `numba` on 3.12)
- `pip install -r requirements.txt` — core: `requests`, `yfinance`, `pandas`, `ddgs`, `python-dotenv`, `scipy`, `numpy`, `feedparser`, `finvizfinance`

For LM Studio: load your model and enable the OpenAI-compatible server on `http://localhost:1234/v1`.

## How to run (typical)

```bash
# Deterministic report, no AI
python portfolio_ai_assistant.py --no-ai

# Full modular engine (requires local LLM if enabled, falls back to --no-ai)
python portfolio_ai_assistant.py --config portfolio_config.json --investment-engine --generate-full-report

# Helpers
python trading212\integration.py --health --config api.env
python trading212\integration.py --export reports\t212_portfolio.json --config api.env
```

Windows batch helpers (`run_automated.bat`, `run_full_report.bat`, `run_t212_analysis.bat`, `run_t212_export.bat`) wrap the same commands.

Outputs (gitignored):

- `reports/portfolio_analysis.md` / `.json` + `reports/archive/*` + `reports/ai_context_*.md`
- `logs/portfolio_*.log` + `logs/latest_run.log`
- `data/cache/*.db`

## High-level architecture

```
portfolio_ai_assistant.py            — thin wrapper (EngineSettings, run_engine, write_reports)
investment_engine/
  config/settings.py               — EngineSettings.from_mapping
  main.py                          — run_engine() orchestration (T212 + news + regime + AI)
  providers/{base,factory,fallback,lmstudio,ollama,openrouter}.py
  research/{market_data,market_regime,news_engine,peak_valley,pies,sector_templates,technical_analysis}.py
  portfolio/{exposure,pie_metadata,sidecar}.py + README.md
  reporting/regime_report.py
  schemas/ai_recommendations.py
  scoring/priority.py, pipeline/engine.py, etc.
  prompts/{adrian_alpha,constraints,loader,research,style,summary,think}.md
  memory/store.py                  — local memory (memory.json gitignored)
experimental/
  backtest/{engine,indicators,benchmarks,compare,io,metrics,validate,costs}.py
  backtest/strategy/{base,tech_pie_pullback_v1}.py + config/*.json
  tests/ (backtest fixtures & 7 tests)
  README.md                        — advisory warning, no-lookahead convention
tests/{test_market_regime,test_pie_exposure,test_pie_hardening}.py
PIEs/config/*.json                 — pie strategy metadata (tracked, no holdings)
PIEs/example_*.csv                 — safe CSV examples (real PIEs/*.csv gitignored)
portfolio_config.example.json      — safe example (tracked)
api.env.example                    — placeholder env vars (tracked)
```

## Known limitations and experimental backtest

### This beta (v1.0.0-beta.1)

- Beta / WIP: may contain bugs, incomplete features, incorrect calculations, data-provider edge cases, configuration incompatibilities and breaking changes.
- Architecture is still evolving; configuration keys and report schemas may change without migration.
- Requires independent validation before any investment decision. Do not rely on it for live decisions.

### Stable monolithic (v0.6.0)

- Current stable monolithic release used for regular local portfolio monitoring.
- Has **no full automated test suite**; outputs must be independently validated (prices, signals, LLM tidy-ups).
- Live data providers (yfinance, DDGS, Trading 212) may be unavailable or rate-limited.

### Experimental backtest (`experimental/backtest/`)

- **Research only, not a guarantee of future performance.** Isolated from the production engine: consumes only local OHLCV CSV/parquet with dated FX (`experimental/backtest/config/*.json`), executes signals at next open, tracks `data_hash`/`fx_hash`/`config_hash` for reproducibility.
- No Trading 212, LLM, or network calls. No secrets.
- Must be checked for look-ahead bias (`experimental/tests/test_no_lookahead.py`). Passing a backtest does **not imply future profitability**.
- Limitations apply: simplified costs, fixed slippage assumptions, survivorship bias if universe is not point-in-time.

## Releases

- [v0.1.0](docs/releases/v0.1.0.md) — Historical baseline
- [v0.5.0](docs/releases/v0.5.0.md) — Historical feature-rich monolith
- [v0.6.0](docs/releases/v0.6.0.md) — Current stable monolithic release
- [v1.0.0-beta.1](docs/releases/v1.0.0-beta.1.md) — Beta / WIP modular rewrite (this branch)
- [CHANGELOG.md](CHANGELOG.md) — Chronological summary
- [EXI2_MARKET_REGIME_PLAN.md](EXI2_MARKET_REGIME_PLAN.md) and [ROBUST_EXI2_REGIME_SPEC.md](ROBUST_EXI2_REGIME_SPEC.md) — market-regime design notes (modular line)

## License

MIT
