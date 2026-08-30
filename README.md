# Portfolio AI Assistant — v0.5.0-monolith (historical feature milestone)

> **Research/advisory tool only — not financial advice and not an automated trading system.**

Historical feature milestone snapshot from `portfolio_ai_assistant_V4` (2026-08-01). Prepared for optional Git tag `v0.5.0`.

## What this version does

Everything in `v0.1.0-baseline` plus:

- Auto-discovery of unmatched Trading 212 positions (`auto_discover_unmatched_positions:355`)
- Symbol-mismatch detection (`detect_symbol_mismatch:554`, `resolve_yahoo_symbol:570`)
- Backend detection for Ollama vs LM Studio (`_detect_backend:585`, `build_ollama_url:602`)
- Expanded news/sentiment: `use_reddit`, `use_x_sentiment`, `use_stocktwits`, `use_sector_news`, `use_macro_news`, `use_crypto_analysis`, `use_etf_analysis`
- Separate recommendation and data-quality reasoning (`recommendation_label:537`, `data_quality_reason:543`, `calculate_recommendation:1379`)
- Modular markdown rendering (`_asset_line:1614`, `_append_asset_block:1632`)
- `max_news_age_days`, `ignore_duplicate_news`, `ignore_otc`, `prioritize_manual_assets`, `always_include_portfolio/watchlist`

Still a monolith (`portfolio_ai_assistant.py:1`, 2720 lines, 54 defs) but the last feature-complete version before modularization.

## High-level architecture

```
v0.5.0-monolith/
├── portfolio_ai_assistant.py   — monolith with auto-discovery and multi-source research
├── trading212_*.py             — identical to v0.1.0 (auth/integration/portfolio)
├── portfolio_config.example.json — 52 settings keys, 3 example assets, localhost endpoints
├── portfolio_config.json       — safe default (gitignored)
├── api.env.example             — placeholder env vars
├── requirements.txt            — requests, yfinance, pandas, ddgs, python-dotenv (deduped)
└── run_*.bat                   — Windows helpers
```

Inputs/outputs same as `v0.1.0-baseline`. Additional LM Studio config: `lm_studio_base_url` (sanitized to `http://localhost:1234/v1`).

## Requirements

- Python 3.11+
- `pip install -r requirements.txt`

## Safe local setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp api.env.example api.env
cp portfolio_config.example.json portfolio_config.json
# fill api.env and portfolio_config.json locally
```

## How to run

```bash
python portfolio_ai_assistant.py --config portfolio_config.json --no-interactive
# or
run_automated.bat
```

## Runtime outputs (gitignored)

- `data/cache/*.db`
- `logs/`
- `reports/` + `reports/archive/` (previous versions generated 97 reports — all excluded here)

## Notes

- No `investment_engine/` yet; no `PIEs/` or `experimental/` directory
- Last monolith before the `v1.0.0-current` modular refactor
- `requirements.txt` previously contained duplicate `ddgs>=0.2.0` — deduped in this clean snapshot
