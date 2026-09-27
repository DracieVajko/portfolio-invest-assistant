# Portfolio AI Assistant

Reliable Investment Monitoring System with local AI models (Ollama/LM Studio).

## Features

- **T212 Integration** - Read-only portfolio sync via Trading 212 API
- **Technical Analysis** - RSI, MA crossovers, momentum, trend detection
- **Signal Generation** - BUY/SELL/HOLD with probabilities
- **Per-Asset News** - 48h filtered news with sentiment analysis
- **Local AI** - Works with Ollama or LM Studio (Fin-R1, Gemma, GPT-OSS)
- **Deterministic Reports** - Markdown + JSON output

## Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure
```bash
cp api.env.example api.env
# Edit api.env with your T212 API credentials
```

### 3. Configure Portfolio
Edit `portfolio_config.json` with your assets and settings.

### 4. Run (Python 3.12 required — use `py -3.12`)

**Full report (requires LM Studio unless all providers fall back to deterministic output):**
```bash
py -3.12 portfolio_ai_assistant.py
```

**Custom config:**
```bash
py -3.12 portfolio_ai_assistant.py --config portfolio_config.json
```

Supported flags today: `--config <path>`, `--investment-engine`, `--generate-full-report`
(compatibility only; reports are always written).
Planned / not implemented: `--no-ai`, `--asset`, `--scheduled`, `--validate-only`, `--dry-run`.

**Environment verification (offline, no secrets printed):**
```bash
py -3.12 scripts/verify_environment.py
py -3.12 scripts/verify_environment.py --smoke
```

### Batch Files (Windows)
```cmd
run_automated.bat      # Daily automated run
run_full_report.bat    # Full report with AI
run_t212_analysis.bat  # T212 analysis only
```

## Configuration

### `portfolio_config.json`
- `assets` - Your portfolio assets with symbols, groups, aliases
- `settings` - Thresholds, AI backends, news settings
- `symbol_aliases` - T212 internal ticker → Yahoo Finance mappings
- `ai_backends` - LM Studio / Ollama model configuration

### `api.env`
```env
TRADING212_API_KEY=your_key
TRADING212_API_SECRET=your_secret
TRADING212_ENABLED=true
```

## Project Structure

```
portfolio_ai_assistant/
├── portfolio_ai_assistant.py   # Main entry point
├── portfolio_config.json       # Configuration
├── requirements.txt
├── api.env                     # Secrets (gitignored)
├── .gitignore
├── trading212/                 # T212 modules
│   ├── auth.py                 # Authentication
│   ├── portfolio.py            # Portfolio analysis
│   └── integration.py          # High-level integration
├── reports/                    # Generated reports (gitignored)
├── logs/                       # Runtime logs (gitignored)
├── data/                       # Cache/data
└── run_*.bat                   # Windows batch helpers
```

## AI Models (LM Studio)

| Model | Purpose |
|-------|---------|
| `fin-r1` | Alpha analysis (reasoning) |
| `gemma-4-12b` | Summary (fast) |
| `gpt-oss-20b` | Fallback |

Configure in `ai_backends` section of config.

## Scheduled operation

Windows Task Scheduler (or double-click `run_full_report.bat`, which pins Python 3.12):

```cmd
py -3.12 portfolio_ai_assistant.py --config portfolio_config.json --investment-engine --generate-full-report
```

> Note: `--scheduled` is planned / not implemented. The batch files above pass only
> the actually supported flags. A `runtime/run.lock` single-run lock plus exit codes
> `3` (already running) / `4` (partial) / `5` (broker sync failed) are likewise
> planned / not implemented.

Current outputs per run (see `portfolio_ai_assistant.write_reports`):

```text
reports/current/portfolio_intelligence_brief.md  # short human brief (≤60 lines, read this)
reports/current/portfolio_decision_brief.md      # curated deterministic brief
reports/current/t212_portfolio_snapshot.md       # full broker inventory
reports/current/portfolio_analysis.json          # canonical machine-readable result
reports/current/failed_tickers.md                # only when tickers are unresolvable
reports/current/run_manifest.json                # run_id, sha256, counts, recon, providers, indicator engine
reports/ai_context/ai_context_<run_id>.md        # full machine archive
reports/debug/<run_id>/                          # raw dump, recon diagnostics, run log
reports/archive/<run_id>/                        # full copy of current/ for this run
```

Provider policy: local-first chain, but **LM Studio is disabled by default for
testing** — set `LMSTUDIO_ENABLED=1` (or `"lmstudio_enabled": true` in config) to
enable it. Every run records the actual winning provider/model per stage in
`run_manifest.json` (`providers_per_stage`); `provider` is the winners summary,
`stance` the human portfolio stance.

Exit codes today: `0` success · `1` report/LLM-stage failure · `2` config error
(unknown flags also exit `2` via argparse).

Pre-flight checks (offline, no secrets printed):

```cmd
py -3.12 scripts/verify_environment.py          :: interpreter, deps, gitignore, syntax
py -3.12 scripts/verify_environment.py --smoke  :: plus one fast offline test module
py -3.12 -m pytest tests/ -q                    :: full offline unit suite
py -3.12 -m pytest experimental/tests/ -q       :: isolated backtest suite
```

## Technical indicators (optional dependencies)

`pandas-ta` is optional and NOT required. When installed, `TechnicalAnalyzer` uses the
`pandas-ta` engine; otherwise it falls back to deterministic manual indicators
(SMA/EMA/MACD/RSI/Bollinger/ATR/simplified ADX and Supertrend, OBV, rolling VWAP, …).
Both paths are covered by tests, but numerics differ — see `docs/audit/PROJECT_AUDIT.md`
(Technical Indicator Audit) and run `scripts/verify_environment.py` to see which engine
would activate on your PC. `tzdata` is a core requirement so `ZoneInfo` works on Windows.

Secrets: `api.env` holds real keys — never commit it, never paste logs containing
`Authorization`/`Bearer`/`key=` values (the tool redacts them automatically).

## Output (legacy layout note)

- `reports/portfolio_analysis.md` - Human-readable Markdown (compat copy of the main report)
- `reports/portfolio_analysis.json` - Machine-readable JSON (compat copy)
- `reports/archive/` - Timestamped history (new layout: `YYYY-MM-DD/<run_id>/`)
- `reports/latest/` - Always the newest complete run (published atomically)

## License

MIT