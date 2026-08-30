# Portfolio AI Assistant — v0.1.0-baseline (historical)

> **Research/advisory tool only — not financial advice and not an automated trading system.**

Historical baseline snapshot from `portfolio_ai_assistant_V1` (2026-06-30). Prepared for Git tag `v0.1.0`.

## What this version does

- Read-only Trading 212 portfolio sync (`GET /api/v0/equity/portfolio` + `/cash`)
- Yahoo Finance price snapshots (`yfinance`, period `6mo`, session cache in `data/cache`)
- DuckDuckGo news search with relevance filtering and keyword sentiment
- Deterministic data-quality scoring (0–100) and rule-based BUY/SELL/WATCH signals
- Optional TradingView public checks
- Ollama local LLM validation (`/api/tags`, `/api/chat`) and prompt-based report generation
- Markdown + JSON output (`reports/portfolio_analysis.*`) with timestamped archive

Single-file monolith: `portfolio_ai_assistant.py:1` contains config, broker, market data, signals and LLM logic. `trading212_auth.py`, `trading212_integration.py`, `trading212_portfolio.py` handle broker specifics.

## High-level architecture

```
v0.1.0-baseline/
├── portfolio_ai_assistant.py   — monolith (load_config, fetch_trading212_positions, get_price_snapshot, calculate_data_quality, calculate_signals, ask_ollama, generate_report)
├── trading212_auth.py          — Trading 212 authentication helper
├── trading212_integration.py   — portfolio/cash fetch
├── trading212_portfolio.py     — P/L, FX, holdings calculations
├── fetch_trading212_cash.py    — standalone cash API snippet (reference)
├── portfolio_config.example.json — safe example config (3 assets)
├── portfolio_config.json       — safe default (identical to example, gitignored in future repo)
├── api.env.example             — placeholder env vars
├── requirements.txt            — requests, yfinance, pandas, duckduckgo-search, python-dotenv
└── run_*.bat                   — Windows helpers (%~dp0, no hardcoded paths)
```

Inputs: `portfolio_config.json:2` (settings + assets), `api.env` (env), T212 API, yfinance, DDGS. Outputs: `reports/` and `logs/` (both gitignored).

## Requirements

- Python 3.11+ (original run on 3.11)
- `pip install -r requirements.txt`

## Safe local setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate  |  Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp api.env.example api.env
# edit api.env locally — never commit
cp portfolio_config.example.json portfolio_config.json
# edit portfolio_config.json locally — add your own symbols
```

Never commit `api.env`, `portfolio_config.json` with real holdings, `data/`, `logs/`, `reports/`.

## How to run (typical)

```bash
python portfolio_ai_assistant.py --config portfolio_config.json
# or via batch (Windows):
run_automated.bat        # no-interactive, writes logs/latest_run.log
run_full_report.bat
```

The code validates Ollama availability before calling the model and falls back to deterministic reporting if the model is unavailable.

## Runtime outputs (gitignored)

- `data/cache/*.db` — yfinance tz/cookie cache
- `logs/latest_run.log` + timestamped logs
- `reports/portfolio_analysis.md` / `.json` + `reports/archive/*`

All are excluded via `.gitignore` and must not be committed.

## Notes on historical limitations

- Monolith ~1926 lines, no test suite, no modular `investment_engine`
- No pie-level handling (`PIEs/` absent)
- No market-regime analysis
- `fetch_trading212_cash.py` is a reference snippet already merged into the main flow
