# Portfolio AI Assistant — v0.6.0 Monolith

> **Historical maintenance-oriented monolithic snapshot.**
> This version is potentially more stable than the current modular WIP branch, but it has not been fully test-validated and may contain bugs or incorrect calculations.
> Research and advisory use only — not financial advice and not an automated trading system.

## Purpose

Local portfolio monitoring and reporting tool. It collects market data (yfinance), news (DuckDuckGo), and optional read-only Trading 212 positions, calculates deterministic BUY/SELL/ WATCH signals with data-quality scoring, and renders Markdown + JSON reports. An optional local LLM layer can tidy results, but all decisions are traceable without AI.

**Trading 212 is read-only.** The integration only performs `GET` requests (`/api/v0/equity/portfolio`, `/api/v0/equity/account/cash`, history). It never places orders.

## Deterministic Mode

```bash
python portfolio_ai_assistant.py --no-ai
```

`--no-ai` produces the same markdown/JSON output without calling any LLM. Signals, probabilities, and scores are computed by pure Python.

## Optional Local LLM Providers

Configured via `portfolio_config.json` → `ai_backends` and `settings`:

- **Ollama** — `http://127.0.0.1:11434` (default)
- **LM Studio** — `http://127.0.0.1:1234/v1` (OpenAI-compatible)
- **Odysseus** (WSL wrapper) — `http://127.0.0.1:7000`

All providers are optional and local. If no backend is reachable, the tool automatically falls back to `--no-ai`.

## Architecture

```
portfolio_ai_assistant.py   # Monolith entry point (~2700 lines)
trading212/
  __init__.py
  auth.py                   # HTTP Basic Auth (api_key:api_secret)
  portfolio.py              # PortfolioMonitor / parse_position
  integration.py            # Trading212Integration + OllamaClient
portfolio_config.json       # Local config (gitignored)
api.env                     # Local secrets (gitignored)
```

The monolith contains inline Trading 212 logic (`fetch_trading212_positions`, `merge_broker_data`, …) **and** the `trading212/` package exposes the same domain a second time. The two implementations coexist — see Known Limitations.

## Safe Setup

Never commit secrets.

```bash
pip install -r requirements.txt

# 1. Create secrets file (not tracked)
copy api.env.example api.env
# Edit api.env — keep TRADING212_ENABLED=false for offline testing

# 2. Create portfolio config (not tracked)
copy portfolio_config.example.json portfolio_config.json
# Edit holdings / ai_backends if needed

# 3. Optional: restore example PIE exports (already included)
dir PIEs
```

> You must create local `api.env` and `portfolio_config.json` before running. The `*.example` files are safe templates.

## Typical CLI

No execution is required to inspect the snapshot:

```bash
# Deterministic report, no AI
python portfolio_ai_assistant.py --no-ai

# Single asset
python portfolio_ai_assistant.py --no-ai --asset AAPL

# Full report with AI (requires local LLM)
python portfolio_ai_assistant.py

# Validate config only
python portfolio_ai_assistant.py --validate-only

# Trading 212 read-only helpers
python trading212\integration.py --health --config api.env
python trading212\integration.py --export reports\t212_portfolio.json --config api.env
```

Windows batch helpers (`run_automated.bat`, `run_full_report.bat`, `run_t212_analysis.bat`, `run_t212_export.bat`) wrap the same commands with `%~dp0`-aware relative paths.

## Output

- `reports/portfolio_analysis.md` — human-readable report
- `reports/portfolio_analysis.json` — machine-readable data
- `reports/archive/` — timestamped copies

All output directories are ignored by git.

## Known Limitations

- **No test suite** — this snapshot ships without automated tests; validate market data and calculations independently.
- **Dual Trading 212 implementation** — inline logic in `portfolio_ai_assistant.py` and the `trading212/` package duplicate the same read-only domain (intentional tech debt, not migrated).
- **Live data providers may fail or rate-limit** — yfinance, DDGS, and Trading 212 depend on external connectivity and may return partial data.
- **Manual validation required** — prices, signals, and LLM tidy-ups must be cross-checked before use.

## Privacy

- Private files are gitignored: `api.env`, `portfolio_config.json`, `data/`, `logs/`, `reports/`, `PIEs/*.csv` (except `PIEs/example_*.csv`), `*.db`, `*.log`, `investment_engine/memory/memory.json`.
- Example configs contain only `AAPL`, `MSFT`, `BTC-USD` demo assets — no real holdings, quantities, or P/L.
- Never paste real `TRADING212_API_KEY`, `TRADING212_API_SECRET`, `TRADING212_ACCOUNT_ID`, holdings, or PIE exports into issues or commits.

## Releases

See [docs/releases/v0.6.0-monolith.md](docs/releases/v0.6.0-monolith.md) for the full maintenance note and the v0.5.0 / v0.6.0-monolith / v1.0.0 lineage.

## License

MIT
